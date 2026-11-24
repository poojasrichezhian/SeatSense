"""Build the crowdedness model from tickets + GTFS.

Pipeline
1. Load tickets, match ticket routes/stops to GTFS routes/stops.
2. Orient every ticket onto a GTFS stop pattern (direction): the pattern in which
   the boarding stop comes before the alighting stop.
3. Assign each ticket to the bus "cohort" that left the first stop in the same
   time slot (estimated from the scheduled offset of the boarding stop).
4. For each pattern x day type x slot, add +pax at the boarding position and
   -pax at the alighting position, cumulative-sum along the route -> riders on
   board between every pair of stops. Divide by (#days x #scheduled buses) to
   get mean load per bus.
5. Scale ticket counts to real riders (e-tickets are a sample), shift from
   departure-slot to stop-time-slot, compute P(seat) at every stop/slot.
"""
from __future__ import annotations

import json
import logging
import pickle
from pathlib import Path

import numpy as np
import pandas as pd

from .crowd import p_seat
from .gtfs import DAY_TYPES, get_gtfs
from .ingest import load_tickets
from .matching import match_routes, match_stops

log = logging.getLogger("seatsense.model")
NA = "__NA__"


def _day_type(dates: pd.Series, holidays) -> np.ndarray:
    hol = set(pd.to_datetime(pd.Series(holidays)).dt.normalize()) if holidays else set()
    wk = dates.dt.weekday.to_numpy() >= 5
    if hol:
        wk = wk | dates.isin(hol).to_numpy()
    return wk.astype(np.int8)  # 0 weekday, 1 weekend


def orient_tickets(t: pd.DataFrame, rp: pd.DataFrame, ps: pd.DataFrame, default_alight: int) -> pd.DataFrame:
    """Return rows (route, b, a, pattern_id, pos_b, pos_a, w, oriented)."""
    combos = t[["route", "b", "a"]].drop_duplicates()
    psb = ps[["pattern_id", "stop_id", "pos", "rank", "n_pos"]].rename(columns={"stop_id": "b", "pos": "pos_b"})
    c1 = combos.merge(rp[["route", "pattern_id"]], on="route").merge(psb, on=["pattern_id", "b"])
    psa = ps[["pattern_id", "stop_id", "pos"]].rename(columns={"stop_id": "a", "pos": "pos_a"})
    full = c1[c1["a"] != NA].merge(psa, on=["pattern_id", "a"])
    full = full[full["pos_a"] > full["pos_b"]].copy()
    full["span"] = full["pos_a"] - full["pos_b"]
    full = full.sort_values(["rank", "span"]).drop_duplicates(["route", "b", "a"])
    full["w"], full["oriented"] = 1.0, True

    done = full[["route", "b", "a"]].assign(_d=1)
    rest = c1.merge(done, on=["route", "b", "a"], how="left")
    rest = rest[rest["_d"].isna() & (rest["pos_b"] < rest["n_pos"] - 1)]
    rest = rest.sort_values("pos_b").drop_duplicates(["route", "b", "a", "pattern_id"])
    rest = rest.copy()
    rest["pos_a"] = np.minimum(rest["pos_b"] + default_alight, rest["n_pos"] - 1)
    rest["w"] = 1.0 / rest.groupby(["route", "b", "a"])["pattern_id"].transform("size")
    rest["oriented"] = False
    cols = ["route", "b", "a", "pattern_id", "pos_b", "pos_a", "w", "oriented"]
    return pd.concat([full[cols], rest[cols]], ignore_index=True)


def build_model(cfg: dict, rebuild_gtfs=False) -> dict:
    mc = cfg["model"]
    proc = Path(cfg["data"]["processed_dir"])
    proc.mkdir(parents=True, exist_ok=True)
    g = get_gtfs(cfg, rebuild=rebuild_gtfs)
    t, ingest_stats = load_tickets(cfg["data"]["tickets_csv"], cfg)
    ps = g.pattern_stops

    # ---- 1. match routes and stops
    rmap = match_routes(t["route"].unique(), g.routes)
    rp = pd.DataFrame([(r, rid) for r, ids in rmap.items() for rid in ids], columns=["route", "route_id"])
    rp = rp.merge(ps[["pattern_id", "route_id"]].drop_duplicates(), on="route_id")
    route_stops = rp.merge(ps[["pattern_id", "stop_id"]], on="pattern_id")[["route", "stop_id"]].drop_duplicates()
    names = pd.concat([
        t[["route", "board_raw"]].rename(columns={"board_raw": "raw"}),
        t[["route", "alight_raw"]].rename(columns={"alight_raw": "raw"}),
    ]).dropna().drop_duplicates()
    names = names[names["route"].isin(rmap)]
    smap = match_stops(names, route_stops, g.stops, cfg["matching"]["stop_threshold"])
    smap.to_csv(proc / "stop_matches.csv", index=False)
    sm = smap.set_index(["route", "raw"])["stop_id"]
    t["b"] = pd.Series(list(zip(t["route"], t["board_raw"]))).map(sm).to_numpy()
    t["a"] = pd.Series(list(zip(t["route"], t["alight_raw"]))).map(sm).to_numpy()
    t["a"] = t["a"].astype(object).where(pd.notna(t["a"]), NA)
    t["b"] = t["b"].astype(object)
    n_all = len(t)
    route_ok = t["route"].isin(rmap)
    t = t[route_ok & t["b"].notna()].copy()

    # ---- 2. orientation
    orient = orient_tickets(t, rp, ps, mc["default_alight_stops"])
    t = t.merge(orient, on=["route", "b", "a"], how="inner")
    n_used = float(t["w"].sum())
    t["w"] = t["w"] * t["pax"]

    # ---- 3. cohort slot
    slot_s = int(mc["slot_minutes"] * 60)
    n_slots = int(np.ceil(30 * 3600 / slot_s))
    sdate = (t["ts"] - pd.Timedelta(hours=cfg["tickets"]["service_day_start_hour"])).dt.normalize()
    secs = (t["ts"] - sdate).dt.total_seconds().to_numpy()
    off = ps.set_index(["pattern_id", "pos"])["offset_s"]
    off_b = off.reindex(pd.MultiIndex.from_arrays([t["pattern_id"], t["pos_b"]])).to_numpy()
    t["slot"] = np.clip(np.floor((secs - off_b) / slot_s), 0, n_slots - 1).astype(np.int32)
    t["dt"] = _day_type(sdate, cfg["tickets"]["holidays"])
    t["sdate"] = sdate
    n_days = np.array([sdate[t["dt"] == d].nunique() for d in range(2)], dtype=float)
    log.info("Service days in data: weekday=%d weekend=%d", n_days[0], n_days[1])

    # ---- scheduled buses per pattern x daytype x slot
    pcol = "count_pattern_id" if "count_pattern_id" in g.trips else "pattern_id"
    tr = g.trips[g.trips[pcol].notna()].copy()
    tr["pattern_id"] = tr[pcol]
    tr["slot"] = np.clip(tr["start_s"] // slot_s, 0, n_slots - 1).astype(int)
    buses = {}
    for d, col in enumerate(["ok_weekday", "ok_weekend"]):
        c = tr[tr[col]].groupby(["pattern_id", "slot"]).size()
        for (pid, s), v in c.items():
            buses.setdefault(pid, np.zeros((2, n_slots)))[d, s] = v
    default_buses = max(slot_s / 60 / mc["default_headway_min"], 1.0)

    # ---- 4. per-pattern load arrays
    pinfo = {pid: grp.sort_values("pos") for pid, grp in ps.groupby("pattern_id")}
    patterns = {}
    for pid, grp in t.groupby("pattern_id"):
        info = pinfo[pid]
        n = len(info)
        D = np.zeros((2, n_slots, n + 1))
        R = np.zeros((2, n_slots, n + 1))
        Bo = np.zeros((2, n_slots, n))
        dt, sl = grp["dt"].to_numpy(), grp["slot"].to_numpy()
        pb, pa, w = grp["pos_b"].to_numpy(), grp["pos_a"].to_numpy(), grp["w"].to_numpy()
        np.add.at(D, (dt, sl, pb), w)
        np.add.at(D, (dt, sl, pa), -w)
        np.add.at(R, (dt, sl, pb), 1.0)
        np.add.at(R, (dt, sl, pa), -1.0)
        np.add.at(Bo, (dt, sl, pb), w)
        onboard = np.cumsum(D, axis=2)[:, :, :n]
        obs = np.cumsum(R, axis=2)[:, :, :n]
        bz = buses.get(pid, np.zeros((2, n_slots)))
        be = np.where(bz > 0, bz, default_buses)
        with np.errstate(invalid="ignore", divide="ignore"):
            denom = n_days[:, None] * be
            load_o = onboard / denom[:, :, None]
            board_o = Bo / np.where(n_days > 0, n_days, np.nan)[:, None, None]
        load_o[n_days == 0] = np.nan
        # slots where no bus runs and no ticket was seen -> unknown
        has = (bz > 0) | (np.abs(D[:, :, :n]).sum(axis=2) > 0)
        load_o = np.where(has[:, :, None], load_o, np.nan)
        # shift from "left first stop in slot s" to "at stop p in slot s + shift_p"
        offs = info["offset_s"].to_numpy()
        shift = np.floor((slot_s / 2 + offs) / slot_s).astype(int)
        L = np.full((2, n_slots, n), np.nan, dtype=np.float32)
        O = np.zeros((2, n_slots, n), dtype=np.float32)
        Bs = np.zeros((2, n_slots, n), dtype=np.float32)
        for p in range(n):
            sh = min(max(shift[p], 0), n_slots)
            if sh < n_slots:
                L[:, sh:, p] = load_o[:, : n_slots - sh, p]
                O[:, sh:, p] = obs[:, : n_slots - sh, p]
                Bs[:, sh:, p] = board_o[:, : n_slots - sh, p]
        patterns[pid] = {
            "route_id": info["route_id"].iloc[0],
            "stop_ids": info["stop_id"].tolist(),
            "offset_s": offs.astype(np.float32),
            "load": L, "obs": O, "boardings": Bs,
            "tickets": int(len(grp)),
        }

    # ---- 5. scale factor (riders per ticket)
    cap = mc["seats"] + mc["standing"]
    scale = mc["scale"]
    total_days = float(n_days.sum())
    if scale == "ridership":
        # riders per ticket = city bus ridership per day / tickets per day
        per_day = float(ingest_stats["pax_total"]) / max(total_days, 1)
        scale = float(mc["daily_ridership"]) / max(per_day, 1e-9)
        log.info("Ridership scale: %.0f riders/day / %.0f tickets/day -> %.2f riders/ticket",
                 mc["daily_ridership"], per_day, scale)
    elif scale == "auto":
        vals = []
        for p in patterns.values():
            m = np.where(p["obs"][0] >= mc["min_obs"], p["load"][0], -np.inf)
            mx = m.max(axis=1)
            vals.extend(mx[np.isfinite(mx)].tolist())
        vals = [v for v in vals if np.isfinite(v) and v > 0]
        q = float(np.quantile(vals, mc["auto_quantile"])) if vals else 1.0
        scale = mc["auto_target_load_factor"] * cap / q if q > 0 else 1.0
        log.info("Auto scale: q%.2f of slot peak loads = %.2f tickets/bus -> scale %.2f riders/ticket",
                 mc["auto_quantile"], q, scale)
    scale = float(scale)
    route_scale = {}
    if mc.get("route_scale_csv") and Path(mc["route_scale_csv"]).exists():
        rs = pd.read_csv(mc["route_scale_csv"], dtype={"route": str})
        for r, s in zip(rs["route"], rs["scale"]):
            for rid in rmap.get(r, []):
                route_scale[rid] = float(s)
    # physical cap: e-ticket share differs by route (some routes are mostly digital). A bus cannot
    # carry more than ~route_cap_lf x capacity, so a route's riders-per-ticket is capped so that its
    # typical busy-hour peak (route_cap_quantile of its weekday slot peaks) stays within that limit.
    n_capped = 0
    if mc.get("route_cap_lf"):
        by_route = {}
        for p in patterns.values():
            m = np.where(p["obs"][0] >= mc["min_obs"], p["load"][0], -np.inf)
            mx = m.max(axis=1)
            by_route.setdefault(p["route_id"], []).extend(mx[np.isfinite(mx) & (mx > 0)].tolist())
        for rid, vals in by_route.items():
            if not vals:
                continue
            q = float(np.quantile(vals, mc["route_cap_quantile"]))
            kmax = mc["route_cap_lf"] * cap / q if q > 0 else scale
            k0 = route_scale.get(rid, scale)
            if kmax < k0:
                route_scale[rid] = kmax
                n_capped += 1
        log.info("Capacity cap applied to %d of %d routes", n_capped, len(by_route))
    for pid, p in patterns.items():
        k = route_scale.get(p["route_id"], scale)
        p["scale"] = k
        p["p_seat"] = p_seat(p["load"] * k, mc["seats"], mc["dispersion_r"]).astype(np.float32)

    # ---- route x hour summary for heatmaps
    labels = g.routes.set_index("route_id")["label"]
    rows = []
    for pid, p in patterns.items():
        k = p["scale"]
        for d in range(2):
            for s in range(n_slots):
                lrow = p["load"][d, s]
                if not np.isfinite(lrow).any():
                    continue
                i = int(np.nanargmax(lrow))
                rows.append((labels.get(p["route_id"], p["route_id"]), p["route_id"], pid, DAY_TYPES[d], s,
                             float(np.nansum(p["boardings"][d, s]) * k), float(min(lrow[i] * k / cap, mc.get("display_max_lf", 1.5))),
                             float(p["p_seat"][d, s, i]), p["stop_ids"][i], float(p["obs"][d, s].max())))
    rh = pd.DataFrame(rows, columns=["route", "route_id", "pattern_id", "day_type", "slot", "boardings_per_day",
                                     "peak_load_factor", "p_seat_at_peak", "peak_stop_id", "obs"])
    rh["hour"] = rh["slot"] * mc["slot_minutes"] / 60.0
    rh = rh.merge(g.stops[["stop_id", "stop_name"]].rename(columns={"stop_id": "peak_stop_id", "stop_name": "peak_stop"}),
                  on="peak_stop_id", how="left")
    rh.to_csv(proc / "route_hour.csv", index=False)

    stats = {
        "ingest": ingest_stats,
        "tickets_total": int(n_all),
        "tickets_route_matched": int(route_ok.sum()),
        "share_route_matched": float(route_ok.mean()),
        "tickets_used": int(n_used),
        "share_used": float(n_used / max(n_all, 1)),
        "share_oriented_by_alight_stop": float((t.loc[t["oriented"], "w"].sum()) / max(t["w"].sum(), 1)),
        "ticket_routes": int(ingest_stats["routes"]),
        "ticket_routes_matched": int(len(rmap)),
        "stop_names_matched": int(len(smap)),
        "stop_names_total": int(len(names)),
        "stop_match_methods": smap["method"].value_counts().to_dict() if len(smap) else {},
        "patterns_with_data": int(len(patterns)),
        "n_days": {"weekday": int(n_days[0]), "weekend": int(n_days[1])},
        "scale_riders_per_ticket": scale,
        "routes_capped": int(n_capped),
        "scale_median_after_cap": float(np.median([p["scale"] for p in patterns.values()])) if patterns else scale,
        "capacity": {"seats": mc["seats"], "standing": mc["standing"]},
    }
    model = {
        "meta": {"slot_s": slot_s, "n_slots": n_slots, "seats": mc["seats"], "standing": mc["standing"],
                 "capacity": cap, "scale": scale, "dispersion_r": mc["dispersion_r"], "min_obs": mc["min_obs"],
                 "stats": stats},
        "patterns": patterns,
    }
    with open(proc / "crowd_model.pkl", "wb") as f:
        pickle.dump(model, f, protocol=pickle.HIGHEST_PROTOCOL)
    with open(proc / "build_stats.json", "w") as f:
        json.dump(stats, f, indent=2, default=str)
    log.info("Model built: %s", json.dumps({k: v for k, v in stats.items() if k != "ingest"}, default=str))
    return model
