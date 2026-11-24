"""FIRST THING TO RUN on the real data: shows the columns, which ones SeatSense
auto-detects, and whether tickets carry an alighting stop and a bus number.

    python scripts/inspect_tickets.py path/to/tickets.csv
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from seatsense.ingest import detect_columns  # noqa: E402

path = sys.argv[1]
df = pd.read_csv(path, nrows=20000, dtype=str)
print(f"\nColumns ({len(df.columns)}):")
for c in df.columns:
    nn = df[c].notna().mean()
    ex = df[c].dropna().astype(str).head(3).tolist()
    print(f"  {c!r:40} non-null {nn:5.0%}  e.g. {ex}")
cm = detect_columns(df.columns.tolist())
print("\nAuto-detected mapping:")
for k in ["datetime", "date", "route", "board_stop", "alight_stop", "vehicle", "pax", "ticket_type"]:
    print(f"  {k:12} -> {cm.get(k, '-- NOT FOUND --')}")
print("\nChecks:")
print("  has alighting stop :", "alight_stop" in cm, "(needed for true on-board loads; fallback = average ride length)")
print("  has bus number     :", "vehicle" in cm)
if "ticket_type" in cm:
    print("  ticket types       :", df[cm["ticket_type"]].value_counts().head(8).to_dict())
print("\nIf anything is wrong, set tickets.columns in config.yaml.")
