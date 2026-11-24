"""Match ticket route numbers and stop names to GTFS routes and stops."""
from __future__ import annotations

import logging
import re

import numpy as np
import pandas as pd

try:
    from rapidfuzz import fuzz, process
except ImportError:  # pragma: no cover
    fuzz = process = None
    import difflib

log = logging.getLogger("seatsense.matching")

ABBR = {
    "STN": "STATION", "ST": "STATION", "RLY": "RAILWAY", "RLWY": "RAILWAY", "RAILWAYSTATION": "RAILWAY STATION",
    "TERM": "TERMINAL", "TML": "TERMINAL", "MKT": "MARKET", "NGR": "NAGAR", "NG": "NAGAR", "HOSP": "HOSPITAL",
    "XING": "CROSSING", "CHK": "CHOWK", "PH": "PHASE", "SEC": "SECTOR", "SECT": "SECTOR", "EXTN": "EXTENSION",
    "EXT": "EXTENSION", "ENCL": "ENCLAVE", "COL": "COLONY", "VIL": "VILLAGE", "VILL": "VILLAGE", "GT": "GATE",
    "II": "2", "III": "3", "IV": "4", "V": "5", "VI": "6", "MOD": "MODEL", "UNIV": "UNIVERSITY", "DEPO": "DEPOT",
}
DROP = {"BUS", "STOP", "BS", "STAND", "THE", "NEAR", "NR", "STAGE", "B"}


def norm_stop(s) -> str:
    s = str(s).upper().replace(".", "")
    s = re.sub(r"[^A-Z0-9]+", " ", s)
    toks = [ABBR.get(t, t) for t in s.split()]
    toks = [t for t in " ".join(toks).split() if t not in DROP]
    return " ".join(toks)


def norm_route(s) -> str:
    s = re.sub(r"[^A-Z0-9]", "", str(s).upper())
    return re.sub(r"^0+(?=\d)", "", s)


def _strip_dir(s: str) -> str:
    return re.sub(r"(UP|DN|DOWN|DWN)$", "", s)


def match_routes(ticket_routes, routes: pd.DataFrame) -> dict:
    """ticket route string -> list of GTFS route_ids."""
    idx: dict[str, set] = {}
    for _, r in routes.iterrows():
        for val in (r["route_short_name"], r["route_id"]):
            if isinstance(val, str) and val:
                for key in {norm_route(val), _strip_dir(norm_route(val))}:
                    idx.setdefault(key, set()).add(r["route_id"])
    out = {}
    for tr in ticket_routes:
        k = norm_route(tr)
        ids = idx.get(k) or idx.get(_strip_dir(k))
        if ids:
            out[tr] = sorted(ids)
    return out


def _best(name: str, choices: list[str]):
    if process is not None:
        res = process.extractOne(name, choices, scorer=fuzz.WRatio)
        if res is None:
            return None, 0
        # penalise matches that only work because of partial overlap
        exact = fuzz.ratio(name, res[0])
        return res[2], 0.5 * res[1] + 0.5 * max(exact, fuzz.token_sort_ratio(name, res[0]))
    best, score = None, 0
    for i, c in enumerate(choices):
        sc = 100 * difflib.SequenceMatcher(None, name, c).ratio()
        if sc > score:
            best, score = i, sc
    return best, score


def match_stops(pairs: pd.DataFrame, route_stops: pd.DataFrame, stops: pd.DataFrame, threshold=80) -> pd.DataFrame:
    """pairs: [route, raw] unique ticket stop names per ticket route.
    route_stops: [route, stop_id] - GTFS stops served by that ticket route (all patterns).
    Returns [route, raw, stop_id, score, method]."""
    by_id = set(stops["stop_id"].astype(str))
    code_to_id = {str(c): i for c, i in zip(stops["stop_code"], stops["stop_id"]) if isinstance(c, str) and c}
    names = stops.set_index("stop_id")["stop_name"].astype(str)
    rs = route_stops.merge(names.rename("stop_name").reset_index(), on="stop_id")
    rs["nname"] = rs["stop_name"].map(norm_stop)
    pools = {r: g for r, g in rs.groupby("route")}
    rows = []
    for route, g in pairs.groupby("route"):
        pool = pools.get(route)
        pool_ids = set(pool["stop_id"]) if pool is not None else set()
        exact = dict(zip(pool["nname"], pool["stop_id"])) if pool is not None else {}
        choices = pool["nname"].tolist() if pool is not None else []
        cids = pool["stop_id"].tolist() if pool is not None else []
        for raw in g["raw"].unique():
            r = str(raw).strip()
            if r in by_id and (not pool_ids or r in pool_ids):
                rows.append((route, raw, r, 100.0, "id"))
                continue
            if r in code_to_id and (not pool_ids or code_to_id[r] in pool_ids):
                rows.append((route, raw, code_to_id[r], 100.0, "code"))
                continue
            if not choices:
                continue
            n = norm_stop(r)
            if n in exact:
                rows.append((route, raw, exact[n], 100.0, "name"))
                continue
            i, sc = _best(n, choices)
            if i is not None and sc >= threshold:
                rows.append((route, raw, cids[i], float(sc), "fuzzy"))
    return pd.DataFrame(rows, columns=["route", "raw", "stop_id", "score", "method"])
