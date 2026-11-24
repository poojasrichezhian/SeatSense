"""FastAPI app: REST API + the web front end.

Run:  uvicorn seatsense.api:app --reload      (SEATSENSE_CONFIG=config.yaml by default)
"""
from __future__ import annotations

import datetime as dt
import os
from functools import lru_cache
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import load_config
from .planner import Planner

ROOT = Path(__file__).resolve().parent.parent
app = FastAPI(title="SeatSense API", version="0.1.0",
              description="Crowdedness-aware bus journey planner for Delhi (Transport Stack Hackathon 2026)")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.mount("/static", StaticFiles(directory=ROOT / "web"), name="static")


@lru_cache(maxsize=1)
def cfg():
    return load_config(os.environ.get("SEATSENSE_CONFIG", str(ROOT / "config.yaml")))


@lru_cache(maxsize=1)
def planner() -> Planner:
    return Planner(cfg())


@lru_cache(maxsize=1)
def route_hour() -> pd.DataFrame:
    p = Path(cfg()["data"]["processed_dir"]) / "route_hour.csv"
    return pd.read_csv(p, dtype={"route": str}) if p.exists() else pd.DataFrame()


def _parse_time(time: str | None) -> float:
    if not time:
        now = dt.datetime.now(dt.timezone(dt.timedelta(hours=5, minutes=30)))
        return now.hour * 3600 + now.minute * 60
    try:
        h, m = time.split(":")[:2]
        return int(h) * 3600 + int(m) * 60
    except Exception as e:  # noqa
        raise HTTPException(400, "time must be HH:MM") from e


def _resolve(lat, lon, stop_id, label):
    if stop_id:
        p = planner()
        if stop_id not in p.stop_idx:
            raise HTTPException(404, f"Unknown {label} stop_id {stop_id}")
        i = p.stop_idx[stop_id]
        return float(p.lat[i]), float(p.lon[i])
    if lat is None or lon is None:
        raise HTTPException(400, f"Give {label}_lat & {label}_lon or {label}_stop")
    return lat, lon


@app.get("/api/health")
def health():
    p = planner()
    return {"status": "ok", "stops": len(p.stop_ids), "trips": len(p.trip_ids),
            "patterns_with_crowding": len(p.model["patterns"]), "stats": p.meta.get("stats", {})}


@app.get("/api/plan")
def plan(from_lat: float | None = None, from_lon: float | None = None, to_lat: float | None = None,
         to_lon: float | None = None, from_stop: str | None = None, to_stop: str | None = None,
         time: str | None = Query(None, description="HH:MM (IST). Default: now"),
         day: str = Query("weekday", pattern="^(weekday|weekend)$")):
    o = _resolve(from_lat, from_lon, from_stop, "from")
    d = _resolve(to_lat, to_lon, to_stop, "to")
    return planner().plan(o[0], o[1], d[0], d[1], _parse_time(time), day)


@app.get("/api/stops")
def stops(q: str, limit: int = 10):
    return planner().search_stops(q, limit)


@app.get("/api/crowding")
def crowding(route: str, stop_id: str, day: str = "weekday"):
    """Hour-by-hour expected load and seat chance for one route at one stop."""
    return planner().stop_profile(route, stop_id, day)


@app.get("/api/heatmap")
def heatmap(route: str, day: str | None = None):
    rh = route_hour()
    if rh.empty:
        return []
    sub = rh[rh["route"].astype(str) == str(route)]
    if day:
        sub = sub[sub["day_type"] == day]
    g = sub.groupby(["day_type", "hour"]).agg(peak_load_factor=("peak_load_factor", "max"),
                                             boardings_per_day=("boardings_per_day", "sum"),
                                             p_seat_at_peak=("p_seat_at_peak", "min")).reset_index()
    return g.round(3).to_dict(orient="records")


@app.get("/api/routes")
def routes(q: str = "", limit: int = 50):
    rh = route_hour()
    if rh.empty:
        return []
    r = pd.Series(rh["route"].astype(str).unique())
    if q:
        r = r[r.str.contains(q, case=False, regex=False)]
    return sorted(r.tolist())[:limit]


@app.get("/api/busiest")
def busiest(day: str = "weekday", hour: float | None = None, limit: int = 15):
    rh = route_hour()
    if rh.empty:
        return []
    sub = rh[rh["day_type"] == day]
    if hour is not None:
        sub = sub[sub["hour"] == hour]
    sub = sub.sort_values("peak_load_factor", ascending=False).drop_duplicates("route").head(limit)
    return sub[["route", "hour", "peak_load_factor", "p_seat_at_peak", "peak_stop", "boardings_per_day"]].round(3).to_dict(orient="records")


@app.get("/")
def index():
    return FileResponse(ROOT / "web" / "index.html")
