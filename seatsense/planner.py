"""Crowding-aware journey planner over static GTFS.

For a from/to location and time it finds direct and one-transfer bus journeys,
lists the next few departures of each option, attaches the expected crowding
of each specific bus and ranks everything by *felt time*:

    felt = walk*w_walk + wait*w_wait + in_vehicle*crowding_multiplier (+ transfer penalty)

That ranking is what produces advice like "skip the 08:42, wait 6 min for the
08:48 - 70% chance of a seat".
"""
from __future__ import annotations

import logging
import pickle
from pathlib import Path

import numpy as np
import pandas as pd

from .crowd import crowd_label, crowding_multiplier, p_seat
from .gtfs import DAY_TYPES, GTFS

log = logging.getLogger("seatsense.planner")
R_EARTH = 6371000.0


def hav(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * R_EARTH * np.arcsin(np.sqrt(a))


def fmt_time(sec: float) -> str:
    sec = int(round(sec)) % (24 * 3600)
    return f"{sec // 3600:02d}:{(sec % 3600) // 60:02d}"


class Planner:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.pc = cfg["planner"]
        proc = Path(cfg["data"]["processed_dir"])
        g = GTFS.load(proc / "gtfs.pkl")
        mp = proc / "crowd_model.pkl"
        if mp.exists():
            with open(mp, "rb") as f:
                self.model = pickle.load(f)
        else:
            log.warning("No crowd model found - planner will run without crowding")
            self.model = {"meta": {"slot_s": 3600, "n_slots": 30, "seats": 38, "standing": 30, "capacity": 68,
                                   "dispersion_r": 4.0, "min_obs": 3, "stats": {}}, "patterns": {}}
        self.meta = self.model["meta"]
        self.stops = g.stops.reset_index(drop=True)
        self.stop_ids = self.stops["stop_id"].to_numpy()
        self.stop_idx = {s: i for i, s in enumerate(self.stop_ids)}
        self.lat = self.stops["stop_lat"].to_numpy()
        self.lon = self.stops["stop_lon"].to_numpy()
        self.names = self.stops["stop_name"].to_numpy()

        trips = g.trips.reset_index(drop=True)
        self.trip_ids = trips["trip_id"].to_numpy()
        tix = {t: i for i, t in enumerate(self.trip_ids)}
        self.route_labels = g.routes.set_index("route_id")["label"].to_dict()
        self.trip_route = trips["route_id"].to_numpy()
        self.trip_ok = np.vstack([trips["ok_weekday"].to_numpy(bool), trips["ok_weekend"].to_numpy(bool)])

        st = g.stop_times
        st = st[st["trip_id"].isin(tix)]
        self.st_trip = st["trip_id"].map(tix).to_numpy(np.int64)
        self.st_stop = st["stop_id"].map(self.stop_idx).to_numpy(np.int64)
        self.st_pos = st["pos"].to_numpy(np.int32)
        self.st_arr = st["arr"].to_numpy(float)
        self.st_dep = st["dep"].to_numpy(float)
        order = np.lexsort((self.st_pos, self.st_trip))
        for a in ("st_trip", "st_stop", "st_pos", "st_arr", "st_dep"):
            setattr(self, a, getattr(self, a)[order])
        # headway factor: a bus that follows a long gap picks up more riders than one right
        # behind another bus (bus bunching). ratio = gap before this bus / typical gap nearby.
        self.st_hw = self._headway_ratio()
        # row range per trip
        self.trip_start = np.searchsorted(self.st_trip, np.arange(len(self.trip_ids)), side="left")
        self.trip_end = np.searchsorted(self.st_trip, np.arange(len(self.trip_ids)), side="right")

        ps = g.pattern_stops
        self.pattern_pos = {}
        for pid, grp in ps.groupby("pattern_id"):
            d = {}
            for s, p in zip(grp["stop_id"], grp["pos"]):
                d.setdefault(s, int(p))
            self.pattern_pos[pid] = d
        self.route_patterns = (ps[["route_id", "pattern_id", "rank"]].drop_duplicates()
                               .sort_values("rank").groupby("route_id")["pattern_id"].apply(list).to_dict())

    def _headway_ratio(self):
        route_of_row = pd.Series(self.trip_route[self.st_trip]).astype("category").cat.codes.to_numpy()
        df = pd.DataFrame({"r": route_of_row, "s": self.st_stop, "dep": self.st_dep, "row": np.arange(len(self.st_dep))})
        df = df.sort_values(["r", "s", "dep"])
        same = (df["r"].to_numpy()[1:] == df["r"].to_numpy()[:-1]) & (df["s"].to_numpy()[1:] == df["s"].to_numpy()[:-1])
        gap = np.full(len(df), np.nan)
        gap[1:] = np.where(same, np.diff(df["dep"].to_numpy()), np.nan)
        gap[gap <= 0] = np.nan
        df["gap"] = gap
        typ = df.groupby(["r", "s"])["gap"].transform(lambda x: x.rolling(5, center=True, min_periods=1).median())
        ratio = (df["gap"] / typ).clip(0.4, 2.5).fillna(1.0).to_numpy()
        out = np.ones(len(df))
        out[df["row"].to_numpy()] = ratio
        return out

    # ------------------------------------------------------------------ helpers
    def nearby(self, lat, lon, radius=None, k=12):
        radius = radius or self.pc["walk_radius_m"]
        d = hav(lat, lon, self.lat, self.lon)
        idx = np.where(d <= radius)[0]
        if len(idx) == 0:  # always offer the closest few stops
            idx = np.argsort(d)[:3]
        idx = idx[np.argsort(d[idx])][:k]
        walk = d[idx] * self.pc["detour_factor"] / self.pc["walk_speed_mps"]
        return idx, walk, d[idx]

    def orient(self, route_id, b_stop, a_stop):
        for pid in self.route_patterns.get(route_id, []):
            pp = self.pattern_pos[pid]
            if b_stop in pp and a_stop in pp and pp[a_stop] > pp[b_stop]:
                return pid, pp[b_stop], pp[a_stop]
        return None, None, None

    def crowd(self, route_id, b_stop, a_stop, dep_s, dt, hw=1.0):
        pid, pb, pa = self.orient(route_id, b_stop, a_stop)
        p = self.model["patterns"].get(pid) if pid else None
        if p is None:
            return {"p_seat": None, "load_factor": None, "expected_onboard": None, "confidence": "none", "pattern_id": pid}
        m = self.meta
        offs = p["offset_s"]
        times = dep_s + (offs[pb:pa] - offs[pb])
        slots = np.clip((times // m["slot_s"]).astype(int), 0, m["n_slots"] - 1)
        posv = np.arange(pb, pa)
        loads = p["load"][dt, slots, posv].astype(float) * p["scale"] * hw
        if not np.isfinite(loads[0]):
            return {"p_seat": None, "load_factor": None, "expected_onboard": None, "confidence": "none", "pattern_id": pid}
        ps_ = float(p_seat(loads[0], m["seats"], m["dispersion_r"]))
        x = float(np.nanmean(loads) / m["capacity"])
        obs = float(p["obs"][dt, slots[0], pb])
        conf = "high" if obs >= 5 * m["min_obs"] else "medium" if obs >= m["min_obs"] else "low"
        return {"p_seat": ps_, "load_factor": x, "load_factor_max": float(np.nanmax(loads) / m["capacity"]),
                "expected_onboard": float(loads[0]), "confidence": conf, "pattern_id": pid,
                "headway_factor": round(float(hw), 2)}

    def _shape(self, trip, pos_from, pos_to):
        s, e = self.trip_start[trip], self.trip_end[trip]
        sel = slice(s + pos_from, s + pos_to + 1)
        si = self.st_stop[sel]
        return [[float(self.lat[i]), float(self.lon[i])] for i in si]

    def _leg(self, trip, i_b, i_a, dt):
        rid = self.trip_route[trip]
        b, a = self.stop_ids[self.st_stop[i_b]], self.stop_ids[self.st_stop[i_a]]
        dep, arr = self.st_dep[i_b], self.st_arr[i_a]
        c = self.crowd(rid, b, a, dep, dt, hw=float(self.st_hw[i_b]))
        mult = crowding_multiplier(c["p_seat"] if c["p_seat"] is not None else np.nan,
                                   c["load_factor"] if c["load_factor"] is not None else np.nan,
                                   self.pc["crowding_multipliers"])
        return {
            "route": str(self.route_labels.get(rid, rid)), "route_id": str(rid), "trip_id": str(self.trip_ids[trip]),
            "from_stop": {"id": str(b), "name": str(self.names[self.st_stop[i_b]]),
                          "lat": float(self.lat[self.st_stop[i_b]]), "lon": float(self.lon[self.st_stop[i_b]])},
            "to_stop": {"id": str(a), "name": str(self.names[self.st_stop[i_a]]),
                        "lat": float(self.lat[self.st_stop[i_a]]), "lon": float(self.lon[self.st_stop[i_a]])},
            "dep_s": float(dep), "arr_s": float(arr), "dep": fmt_time(dep), "arr": fmt_time(arr),
            "ride_min": round((arr - dep) / 60, 1), "stops": int(self.st_pos[i_a] - self.st_pos[i_b]),
            "crowd": {**c, "label": crowd_label(c["p_seat"] if c["p_seat"] is not None else np.nan, c["load_factor"]),
                      "multiplier": round(mult, 2)},
            "shape": self._shape(trip, int(self.st_pos[i_b]), int(self.st_pos[i_a])),
        }

    def _score(self, t0, walk_o, legs, walk_d, transfer_walk=0.0):
        w = self.pc["weights"]
        wait = legs[0]["dep_s"] - (t0 + walk_o)
        felt = walk_o / 60 * w["walk"] + wait / 60 * w["wait"] + walk_d / 60 * w["walk"]
        felt += sum(l["ride_min"] * l["crowd"]["multiplier"] for l in legs)
        if len(legs) > 1:
            tw = legs[1]["dep_s"] - legs[0]["arr_s"] - transfer_walk
            felt += max(tw, 0) / 60 * w["wait"] + transfer_walk / 60 * w["walk"] + w["transfer_penalty_min"]
        total = (legs[-1]["arr_s"] + walk_d - t0) / 60
        return {"wait_min": round(wait / 60, 1), "walk_min": round((walk_o + walk_d + transfer_walk) / 60, 1),
                "total_min": round(total, 1), "felt_min": round(felt, 1),
                "leave_at": fmt_time(legs[0]["dep_s"] - walk_o), "arrive_at": fmt_time(legs[-1]["arr_s"] + walk_d)}

    # ------------------------------------------------------------------ search
    def _first_leg_rows(self, O_idx, O_walk, t0, dt, horizon):
        walkO = np.full(len(self.stop_ids), np.nan)
        walkO[O_idx] = O_walk
        w = walkO[self.st_stop]
        m = np.isfinite(w) & (self.st_dep >= t0 + w) & (self.st_dep <= t0 + w + horizon)
        m &= self.trip_ok[dt, self.st_trip]
        rows = np.where(m)[0]
        # a trip may pass several origin stops - keep the row giving the earliest arrival later; here keep all
        return rows, walkO

    def plan(self, o_lat, o_lon, d_lat, d_lon, t0: float, day_type: str = "weekday"):
        dt = DAY_TYPES.index(day_type) if day_type in DAY_TYPES else 0
        pc = self.pc
        O_idx, O_walk, _ = self.nearby(o_lat, o_lon)
        D_idx, D_walk, _ = self.nearby(d_lat, d_lon)
        walkD = np.full(len(self.stop_ids), np.nan)
        walkD[D_idx] = D_walk
        horizon = pc["max_wait_min"] * 60
        rowsA, walkO = self._first_leg_rows(O_idx, O_walk, t0, dt, horizon)

        # ---------------- direct
        A = pd.DataFrame({"trip": self.st_trip[rowsA], "ia": rowsA, "pa": self.st_pos[rowsA]})
        tripsA = np.unique(A["trip"].to_numpy())
        mB = np.isfinite(walkD[self.st_stop]) & np.isin(self.st_trip, tripsA)
        rowsB = np.where(mB)[0]
        B = pd.DataFrame({"trip": self.st_trip[rowsB], "ib": rowsB, "pb": self.st_pos[rowsB]})
        M = A.merge(B, on="trip")
        M = M[M["pb"] > M["pa"]]
        options = []
        if len(M):
            M["wo"] = walkO[self.st_stop[M["ia"]]]
            M["wd"] = walkD[self.st_stop[M["ib"]]]
            M["arrive"] = self.st_arr[M["ib"]] + M["wd"]
            M["route"] = self.trip_route[M["trip"]]
            M["sb"] = self.st_stop[M["ia"]]
            M["sa"] = self.st_stop[M["ib"]]
            # per trip keep the best board/alight pair
            M = M.sort_values(["arrive", "wo"]).drop_duplicates("trip")
            # per route pick the stop pair of its earliest-arriving trip, then list next departures on that pair
            best_pair = M.drop_duplicates("route")[["route", "sb", "sa"]]
            for _, bp in best_pair.iterrows():
                sub = M[(M["route"] == bp["route"]) & (M["sb"] == bp["sb"]) & (M["sa"] == bp["sa"])]
                sub = sub.sort_values("ia").copy()
                sub["dep"] = self.st_dep[sub["ia"]]
                sub = sub.sort_values("dep").head(pc["departures_per_option"])
                deps = []
                for _, r in sub.iterrows():
                    leg = self._leg(int(r["trip"]), int(r["ia"]), int(r["ib"]), dt)
                    deps.append({"legs": [leg], **self._score(t0, r["wo"], [leg], r["wd"])})
                if deps:
                    options.append({"type": "direct", "routes": [deps[0]["legs"][0]["route"]], "departures": deps})

        # ---------------- one transfer (only if few direct options)
        if len(options) < 3 and len(rowsA):
            options += self._transfers(A, walkO, walkD, t0, dt)

        if not options:
            return {"options": [], "recommendation": None,
                    "message": "No bus journey found within walking distance and the time window."}

        # choose best departure in each option, rank options
        for o in options:
            felt = [d["felt_min"] for d in o["departures"]]
            o["best"] = int(np.argmin(felt))
            b = o["departures"][o["best"]]
            o["felt_min"], o["total_min"] = b["felt_min"], b["total_min"]
            o["tip"] = self._tip(o)
        options.sort(key=lambda o: o["felt_min"])
        options = options[: pc["max_options"]]
        fastest = min(options, key=lambda o: min(d["total_min"] for d in o["departures"]))
        rec = options[0]
        return {
            "options": options,
            "recommendation": {"option": 0, "departure": rec["best"], "message": self._headline(rec, fastest)},
            "meta": {"day_type": DAY_TYPES[dt], "time": fmt_time(t0)},
        }

    def _transfers(self, A, walkO, walkD, t0, dt, max_first=40):
        pc = self.pc
        A = A.copy()
        A["route"] = self.trip_route[A["trip"]]
        A["dep"] = self.st_dep[A["ia"]]
        A = A.sort_values("dep").drop_duplicates(["route", "pa"]).head(max_first)
        # downstream stops of first-leg trips
        X = []
        for _, r in A.iterrows():
            tr = int(r["trip"])
            s, e = int(r["ia"]) + 1, int(self.trip_end[tr])
            for i in range(s, e):
                X.append((tr, int(r["ia"]), i, self.st_stop[i], self.st_arr[i]))
        if not X:
            return []
        X = pd.DataFrame(X, columns=["trip1", "ia", "ix", "sx", "arrx"])
        # transfer stops (same stop + nearby stops within transfer_walk_m)
        ux = X["sx"].unique()
        pairs = []
        for s in ux:
            d = hav(self.lat[s], self.lon[s], self.lat, self.lon)
            near = np.where(d <= pc["transfer_walk_m"])[0]
            for n in near:
                pairs.append((s, n, d[n] * pc["detour_factor"] / pc["walk_speed_mps"]))
        P = pd.DataFrame(pairs, columns=["sx", "sy", "tw"])
        X = X.merge(P, on="sx")
        tmin, tmax = X["arrx"].min(), X["arrx"].max() + (pc["max_transfer_wait_min"] + 5) * 60
        sy = X["sy"].unique()
        m = np.isin(self.st_stop, sy) & (self.st_dep >= tmin) & (self.st_dep <= tmax) & self.trip_ok[dt, self.st_trip]
        rowsY = np.where(m)[0]
        Y = pd.DataFrame({"trip2": self.st_trip[rowsY], "iy": rowsY, "sy": self.st_stop[rowsY],
                          "dep2": self.st_dep[rowsY], "py": self.st_pos[rowsY]})
        t2 = Y["trip2"].unique()
        mD = np.isfinite(walkD[self.st_stop]) & np.isin(self.st_trip, t2)
        rowsD = np.where(mD)[0]
        Dd = pd.DataFrame({"trip2": self.st_trip[rowsD], "id": rowsD, "pd": self.st_pos[rowsD]})
        Y = Y.merge(Dd, on="trip2")
        Y = Y[Y["pd"] > Y["py"]]
        if Y.empty:
            return []
        J = X.merge(Y, on="sy")
        J = J[(J["dep2"] >= J["arrx"] + J["tw"] + pc["min_transfer_min"] * 60) &
              (J["dep2"] <= J["arrx"] + J["tw"] + pc["max_transfer_wait_min"] * 60)]
        J = J[self.trip_route[J["trip1"]] != self.trip_route[J["trip2"]]]
        if J.empty:
            return []
        J = J.copy()
        J["wd"] = walkD[self.st_stop[J["id"]]]
        J["arrive"] = self.st_arr[J["id"]] + J["wd"]
        J["r1"] = self.trip_route[J["trip1"]]
        J["r2"] = self.trip_route[J["trip2"]]
        J = J.sort_values("arrive").drop_duplicates(["r1", "r2"]).head(3)
        out = []
        for _, r in J.iterrows():
            l1 = self._leg(int(r["trip1"]), int(r["ia"]), int(r["ix"]), dt)
            l2 = self._leg(int(r["trip2"]), int(r["iy"]), int(r["id"]), dt)
            wo = walkO[self.st_stop[int(r["ia"])]]
            sc = self._score(t0, wo, [l1, l2], r["wd"], transfer_walk=float(r["tw"]))
            out.append({"type": "transfer", "routes": [l1["route"], l2["route"]],
                        "departures": [{"legs": [l1, l2], **sc}]})
        return out

    @staticmethod
    def _pct(p):
        return "unknown" if p is None else f"{round(100 * p)}%"

    def _tip(self, o):
        deps = o["departures"]
        if len(deps) < 2 or o["best"] == 0:
            return None
        first, best = deps[0], deps[o["best"]]
        extra = best["legs"][0]["dep_s"] - first["legs"][0]["dep_s"]
        return (f"Skip the {first['legs'][0]['dep']} bus (seat chance {self._pct(first['legs'][0]['crowd']['p_seat'])}). "
                f"Wait {round(extra / 60)} min more for the {best['legs'][0]['dep']} "
                f"(seat chance {self._pct(best['legs'][0]['crowd']['p_seat'])}).")

    def _headline(self, rec, fastest):
        b = rec["departures"][rec["best"]]
        leg = b["legs"][0]
        route = " + ".join(rec["routes"])
        msg = (f"Take route {route} at {leg['dep']} from {leg['from_stop']['name']}: "
               f"{self._pct(leg['crowd']['p_seat'])} chance of a seat, arrive {b['arrive_at']} "
               f"({b['total_min']:.0f} min, feels like {b['felt_min']:.0f}).")
        fb = min(fastest["departures"], key=lambda d: d["total_min"])
        if fb is not b and fb["total_min"] < b["total_min"] - 0.5:
            msg += (f" The fastest option ({' + '.join(fastest['routes'])} at {fb['legs'][0]['dep']}) saves "
                    f"{b['total_min'] - fb['total_min']:.0f} min but seat chance is only "
                    f"{self._pct(fb['legs'][0]['crowd']['p_seat'])}.")
        return msg

    # ------------------------------------------------------------------ misc
    def search_stops(self, q: str, limit=10):
        q = (q or "").strip().lower()
        if not q:
            return []
        names = pd.Series(self.names).str.lower()
        idx = np.where(names.str.contains(q, regex=False).to_numpy())[0]
        idx = sorted(idx, key=lambda i: (not names[i].startswith(q), len(names[i])))[:limit]
        return [{"id": str(self.stop_ids[i]), "name": str(self.names[i]),
                 "lat": float(self.lat[i]), "lon": float(self.lon[i])} for i in idx]

    def stop_profile(self, route_label: str, stop_id: str, day_type="weekday"):
        dt = DAY_TYPES.index(day_type) if day_type in DAY_TYPES else 0
        out = []
        rids = [r for r, l in self.route_labels.items() if str(l) == str(route_label) or str(r) == str(route_label)]
        for rid in rids:
            for pid in self.route_patterns.get(rid, []):
                p = self.model["patterns"].get(pid)
                if p is None or stop_id not in p["stop_ids"]:
                    continue
                pos = p["stop_ids"].index(stop_id)
                towards = self.names[self.stop_idx[p["stop_ids"][-1]]]
                prof = []
                for s in range(self.meta["n_slots"]):
                    l = float(p["load"][dt, s, pos]) * p["scale"]
                    if np.isfinite(l):
                        prof.append({"time": fmt_time(s * self.meta["slot_s"]), "expected_onboard": round(l, 1),
                                     "load_factor": round(l / self.meta["capacity"], 2),
                                     "p_seat": round(float(p["p_seat"][dt, s, pos]), 2)})
                out.append({"pattern_id": pid, "towards": str(towards), "profile": prof})
        return out
