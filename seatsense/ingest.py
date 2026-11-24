"""Load the e-ticketing / ETM CSV and map its columns to a standard schema.

Column names are auto-detected from common spellings. If detection fails, set
`tickets.columns` in config.yaml, e.g.

    tickets:
      columns:
        route: "Route No"
        board_stop: "From Stop"
        alight_stop: "To Stop"
        datetime: "Ticket Date Time"
"""
from __future__ import annotations

import logging
import re

import numpy as np
import pandas as pd

log = logging.getLogger("seatsense.ingest")

FIELD_CANDIDATES = {
    "datetime": [
        "ticket_date_time", "ticket_datetime", "txn_date_time", "transaction_date_time",
        "transaction_time", "txn_time", "timestamp", "booking_time", "booking_date_time",
        "created_at", "issue_time", "issued_at", "datetime", "date_time", "ticket_time", "time",
    ],
    "date": ["ticket_date", "txn_date", "transaction_date", "travel_date", "journey_date", "date", "booking_date"],
    "route": ["route_no", "route_number", "route", "route_name", "route_short_name", "routeno", "route_id", "route_code"],
    "board_stop": [
        "from_stop", "from_stop_name", "start_stop_name", "from_stop_id", "source_stop", "source", "boarding_stop",
        "boarding_stop_name", "start_stop", "origin_stop", "origin", "src_stop", "from_stage", "from",
    ],
    "alight_stop": [
        "to_stop", "to_stop_name", "end_stop_name", "to_stop_id", "destination_stop", "destination", "alighting_stop",
        "alighting_stop_name", "end_stop", "dest_stop", "dest", "dst_stop", "to_stage", "to",
    ],
    "vehicle": ["bus_no", "bus_number", "vehicle_no", "vehicle_number", "vehicle_id", "bus_id", "registration_no", "bus_reg_no"],
    "pax": ["no_of_passengers", "passenger_count", "num_passengers", "pax", "quantity", "qty", "no_of_tickets", "ticket_count"],
    "ticket_type": ["ticket_type", "ticket_type_id", "passenger_type", "concession_type", "category", "fare_type"],
}

DATE_RE = re.compile(r"\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}")


def _norm_col(c: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(c).strip().lower()).strip("_")


def detect_columns(columns, explicit: dict | None = None) -> dict:
    explicit = explicit or {}
    normmap = {}
    for c in columns:
        normmap.setdefault(_norm_col(c), c)
    out, used = {}, set()
    for field, cands in FIELD_CANDIDATES.items():
        if explicit.get(field):
            if explicit[field] not in columns:
                raise ValueError(f"Configured column {explicit[field]!r} for {field} not in CSV: {list(columns)}")
            out[field] = explicit[field]
            used.add(explicit[field])
            continue
        for cand in cands:
            col = normmap.get(cand)
            if col is not None and col not in used:
                out[field] = col
                used.add(col)
                break
    return out


def _parse_time(df: pd.DataFrame, cm: dict, dayfirst: bool, fmt: str | None) -> pd.Series:
    dt_col, date_col = cm.get("datetime"), cm.get("date")
    sample = None
    if dt_col:
        nonnull = df[dt_col].dropna()
        sample = str(nonnull.iloc[0]) if len(nonnull) else ""
    if dt_col and sample and sample.isdigit() and len(sample) >= 10:  # epoch
        unit = "ms" if len(sample) >= 13 else "s"
        ts = pd.to_datetime(pd.to_numeric(df[dt_col], errors="coerce"), unit=unit, utc=True)
        return ts.dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    if dt_col and sample and DATE_RE.search(sample):
        s = df[dt_col].astype(str)
    elif dt_col and date_col:
        s = df[date_col].astype(str) + " " + df[dt_col].astype(str)
    elif date_col:
        raise ValueError("Only a date column was found; need a time-of-day too. Set tickets.columns.datetime.")
    else:
        raise ValueError("No timestamp column detected. Set tickets.columns.datetime in config.yaml.")
    if fmt:
        ts = pd.to_datetime(s, format=fmt, errors="coerce")
    else:
        ts = pd.to_datetime(s, dayfirst=dayfirst, errors="coerce")  # fast: infers one format
        if ts.isna().mean() > 0.05:  # mixed formats -> slow per-element parsing
            ts = pd.to_datetime(s, dayfirst=dayfirst, errors="coerce", format="mixed")
    if getattr(ts.dt, "tz", None) is not None:
        ts = ts.dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    return ts


def load_tickets(path: str, cfg: dict) -> tuple[pd.DataFrame, dict]:
    tc = cfg["tickets"]
    header = pd.read_csv(path, nrows=0).columns.tolist()
    cm = detect_columns(header, tc.get("columns"))
    log.info("Detected columns: %s", cm)
    for req in ("route", "board_stop"):
        if req not in cm:
            raise ValueError(f"Could not detect the {req!r} column. Columns are {header}. Set tickets.columns.{req}.")
    df = pd.read_csv(path, usecols=sorted(set(cm.values())), dtype=str, nrows=tc.get("nrows"))
    n0 = len(df)
    out = pd.DataFrame(index=df.index)
    out["ts"] = _parse_time(df, cm, tc.get("dayfirst", True), tc.get("datetime_format"))
    out["route"] = df[cm["route"]].astype(str).str.strip()
    out["board_raw"] = df[cm["board_stop"]].astype(str).str.strip()
    out["alight_raw"] = df[cm["alight_stop"]].astype(str).str.strip() if "alight_stop" in cm else np.nan
    out["vehicle"] = df[cm["vehicle"]].astype(str).str.strip() if "vehicle" in cm else np.nan
    if "pax" in cm:
        out["pax"] = pd.to_numeric(df[cm["pax"]], errors="coerce").fillna(1).clip(lower=1, upper=20)
    else:
        out["pax"] = 1.0
    if "ticket_type" in cm:
        out["ticket_type"] = df[cm["ticket_type"]].astype(str)
    for c in ("board_raw", "alight_raw"):
        out.loc[out[c].isin(["", "nan", "None", "NULL", "null", "NaN"]), c] = np.nan
    out = out.dropna(subset=["ts", "route", "board_raw"]).reset_index(drop=True)
    stats = {
        "rows_read": int(n0),
        "rows_valid": int(len(out)),
        "columns_detected": cm,
        "share_with_alight_stop": float(out["alight_raw"].notna().mean()) if len(out) else 0.0,
        "date_min": str(out["ts"].min()),
        "date_max": str(out["ts"].max()),
        "routes": int(out["route"].nunique()),
        "pax_total": float(out["pax"].sum()),
    }
    log.info("Tickets: %s", stats)
    return out, stats
