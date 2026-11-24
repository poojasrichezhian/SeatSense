"""Static GTFS loading, stop patterns ("directions") and schedule offsets."""
from __future__ import annotations

import logging
import pickle
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger("seatsense.gtfs")

DAY_TYPES = ["weekday", "weekend"]


def _read(gdir, name, required=True, usecols=None):
    p = Path(gdir) / name
    if not p.exists():
        if required:
            raise FileNotFoundError(f"GTFS file missing: {p}")
        return None
    return pd.read_csv(p, dtype=str, keep_default_na=False, na_values=[""], usecols=usecols)


def time_to_sec(s: pd.Series) -> np.ndarray:
    s = s.astype(object).where(s.notna(), "").astype(str).str.strip()
    parts = s.str.split(":", expand=True)
    if parts.shape[1] < 2:
        return np.full(len(s), np.nan)
    h = pd.to_numeric(parts[0], errors="coerce")
    m = pd.to_numeric(parts[1], errors="coerce")
    sec = pd.to_numeric(parts[2], errors="coerce").fillna(0) if parts.shape[1] > 2 else 0
    return (h * 3600 + m * 60 + sec).to_numpy(dtype=float)


@dataclass
class GTFS:
    stops: pd.DataFrame
    routes: pd.DataFrame
    trips: pd.DataFrame  # trip_id, route_id, service_id, pattern_id, start_s, ok_weekday, ok_weekend
    stop_times: pd.DataFrame  # trip_id, stop_id, pos, arr, dep  (sorted by trip, pos)
    pattern_stops: pd.DataFrame  # pattern_id, route_id, rank, pos, stop_id, offset_s, n_pos

    def save(self, path):
        with open(path, "wb") as f:
            pickle.dump(self, f, protocol=pickle.HIGHEST_PROTOCOL)

    @staticmethod
    def load(path) -> "GTFS":
        with open(path, "rb") as f:
            return pickle.load(f)


def _service_daytypes(gdir, service_ids) -> pd.DataFrame:
    cal = _read(gdir, "calendar.txt", required=False)
    df = pd.DataFrame({"service_id": pd.Series(service_ids).unique()})
    if cal is None:
        df["ok_weekday"], df["ok_weekend"] = True, True
        return df
    cal = cal.copy()
    # use a "typical" day of each type so that we don't double count Mon-Thu + Fri services
    cal["ok_weekday"] = cal.get("wednesday", "1").astype(str) == "1"
    cal["ok_weekend"] = cal.get("saturday", "1").astype(str) == "1"
    df = df.merge(cal[["service_id", "ok_weekday", "ok_weekend"]], on="service_id", how="left")
    # services only in calendar_dates: allow both
    df["ok_weekday"] = df["ok_weekday"].fillna(True).astype(bool)
    df["ok_weekend"] = df["ok_weekend"].fillna(True).astype(bool)
    return df


def build_gtfs(gdir: str, keep_share=0.05, max_patterns=6) -> GTFS:
    log.info("Reading GTFS from %s", gdir)
    stops = _read(gdir, "stops.txt")
    stops["stop_lat"] = pd.to_numeric(stops["stop_lat"], errors="coerce")
    stops["stop_lon"] = pd.to_numeric(stops["stop_lon"], errors="coerce")
    stops = stops.dropna(subset=["stop_lat", "stop_lon"]).reset_index(drop=True)
    if "stop_code" not in stops:
        stops["stop_code"] = np.nan
    stops["stop_name"] = stops["stop_name"].fillna(stops["stop_id"])
    stops = stops[["stop_id", "stop_code", "stop_name", "stop_lat", "stop_lon"]]

    routes = _read(gdir, "routes.txt")
    for c in ("route_short_name", "route_long_name"):
        if c not in routes:
            routes[c] = np.nan
    short = routes["route_short_name"].fillna("").astype(str)
    routes["label"] = np.where(short.str.len() > 0, short, routes["route_id"])
    routes = routes[["route_id", "route_short_name", "route_long_name", "label"]]

    trips = _read(gdir, "trips.txt", usecols=lambda c: c in {"route_id", "service_id", "trip_id", "direction_id"})
    st = _read(gdir, "stop_times.txt",
               usecols=lambda c: c in {"trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence"})
    st["stop_sequence"] = pd.to_numeric(st["stop_sequence"], errors="coerce")
    st["arr"] = time_to_sec(st["arrival_time"])
    st["dep"] = time_to_sec(st["departure_time"])
    st["arr"] = st["arr"].fillna(st["dep"])
    st["dep"] = st["dep"].fillna(st["arr"])
    st = st.drop(columns=["arrival_time", "departure_time"])
    st = st[st["stop_id"].isin(set(stops["stop_id"]))]
    st = st.sort_values(["trip_id", "stop_sequence"]).reset_index(drop=True)
    if st["dep"].isna().any():
        log.info("Interpolating missing stop times ...")
        st["dep"] = st.groupby("trip_id")["dep"].transform(lambda s: s.interpolate(limit_direction="both"))
        st["arr"] = st["arr"].fillna(st["dep"])
    st = st.dropna(subset=["dep"])
    st["pos"] = st.groupby("trip_id").cumcount().astype(np.int32)

    # ---- stop patterns: one per distinct stop sequence, keep the main ones per route
    seqs = st.groupby("trip_id", sort=False)["stop_id"].agg(tuple).rename("seq").reset_index()
    tp = trips[["trip_id", "route_id"]].merge(seqs, on="trip_id")
    cnt = tp.groupby(["route_id", "seq"]).size().rename("n").reset_index()
    cnt["share"] = cnt["n"] / cnt.groupby("route_id")["n"].transform("sum")
    cnt = cnt.sort_values(["route_id", "n"], ascending=[True, False])
    cnt["rank"] = cnt.groupby("route_id").cumcount()
    kept = cnt[((cnt["rank"] < 2) | (cnt["share"] >= keep_share)) & (cnt["rank"] < max_patterns)].copy()
    kept = kept[kept["seq"].map(len) >= 2]
    kept["pattern_id"] = kept["route_id"].astype(str) + "::" + kept["rank"].astype(str)
    tp = tp.merge(kept[["route_id", "seq", "pattern_id"]], on=["route_id", "seq"], how="left")
    # variant trips (short turns, skipped stops): map to the closest kept pattern in the same
    # direction so they are still counted as buses running on that pattern
    kmap = {}
    for rid, pid, seq in zip(kept["route_id"], kept["pattern_id"], kept["seq"]):
        kmap.setdefault(rid, []).append((pid, {s_: i for i, s_ in enumerate(seq)}))
    var = cnt.merge(kept[["route_id", "seq"]].assign(_k=1), on=["route_id", "seq"], how="left")
    var = var[var["_k"].isna()]
    vmap = {}
    for rid, seq in zip(var["route_id"], var["seq"]):
        best, bs = None, 0.0
        for pid, pos in kmap.get(rid, []):
            common = [pos[x] for x in seq if x in pos]
            if len(common) >= 2 and common[-1] > common[0]:
                sc = len(common) / len(seq)
                if sc > bs:
                    best, bs = pid, sc
        if best is not None and bs >= 0.5:
            vmap[(rid, seq)] = best
    tp["count_pattern_id"] = tp["pattern_id"]
    miss = tp["count_pattern_id"].isna()
    tp.loc[miss, "count_pattern_id"] = [vmap.get((r, q)) for r, q in zip(tp.loc[miss, "route_id"], tp.loc[miss, "seq"])]
    trips = trips.merge(tp[["trip_id", "pattern_id", "count_pattern_id"]], on="trip_id", how="left")

    first_dep = st.groupby("trip_id")["dep"].min().rename("start_s")
    trips = trips.merge(first_dep.reset_index(), on="trip_id", how="inner")
    trips = trips.merge(_service_daytypes(gdir, trips["service_id"]), on="service_id", how="left")

    st2 = st.merge(trips.loc[trips["pattern_id"].notna(), ["trip_id", "pattern_id", "start_s"]], on="trip_id")
    st2["off"] = st2["dep"] - st2["start_s"]
    offs = st2.groupby(["pattern_id", "pos"])["off"].median().rename("offset_s").reset_index()

    ps = kept[["pattern_id", "route_id", "rank", "seq"]].explode("seq").rename(columns={"seq": "stop_id"})
    ps["pos"] = ps.groupby("pattern_id").cumcount().astype(np.int32)
    ps = ps.merge(offs, on=["pattern_id", "pos"], how="left")
    ps["offset_s"] = ps.groupby("pattern_id")["offset_s"].transform(lambda s: s.interpolate().ffill().bfill()).fillna(0)
    ps["n_pos"] = ps.groupby("pattern_id")["pos"].transform("size").astype(np.int32)
    ps = ps.reset_index(drop=True)

    log.info("GTFS: %d stops, %d routes, %d trips, %d stop_times, %d patterns",
             len(stops), len(routes), len(trips), len(st), ps["pattern_id"].nunique())
    return GTFS(stops=stops, routes=routes, trips=trips,
                stop_times=st[["trip_id", "stop_id", "pos", "arr", "dep"]], pattern_stops=ps)


def get_gtfs(cfg: dict, rebuild=False) -> GTFS:
    proc = Path(cfg["data"]["processed_dir"])
    proc.mkdir(parents=True, exist_ok=True)
    cache = proc / "gtfs.pkl"
    src = [f for f in Path(cfg["data"]["gtfs_dir"]).glob("*.txt")]
    newest = max((f.stat().st_mtime for f in src), default=0)
    if cache.exists() and not rebuild and cache.stat().st_mtime >= newest:
        return GTFS.load(cache)
    g = build_gtfs(cfg["data"]["gtfs_dir"], cfg["gtfs"]["pattern_keep_share"], cfg["gtfs"]["max_patterns_per_route"])
    g.save(cache)
    return g
