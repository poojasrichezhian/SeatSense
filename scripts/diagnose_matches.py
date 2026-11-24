"""Check stop-name matching quality after build_model.py.

    python scripts/diagnose_matches.py                 # GTFS stops that absorbed many different ticket names
    python scripts/diagnose_matches.py "Asian Market"  # every ticket name matched to this stop
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from seatsense.config import load_config  # noqa: E402
from seatsense.gtfs import GTFS  # noqa: E402

cfg = load_config(sys.argv[2] if len(sys.argv) > 2 else "config.yaml")
proc = Path(cfg["data"]["processed_dir"])
m = pd.read_csv(proc / "stop_matches.csv", dtype=str)
m["score"] = m["score"].astype(float)
g = GTFS.load(proc / "gtfs.pkl")
m = m.merge(g.stops[["stop_id", "stop_name"]], on="stop_id", how="left")
pd.set_option("display.width", 200, "display.max_colwidth", 60, "display.max_rows", 200)

if len(sys.argv) > 1 and sys.argv[1] != "-":
    q = sys.argv[1].lower()
    sub = m[m["stop_name"].str.lower().str.contains(q, regex=False)]
    print(f"Ticket stop names matched to GTFS stops containing '{sys.argv[1]}':")
    print(sub.groupby(["stop_name", "raw", "method"])["route"].nunique().rename("routes").reset_index()
          .sort_values("routes", ascending=False).to_string(index=False))
else:
    fz = m[m["method"] == "fuzzy"]
    print(f"{len(fz)} fuzzy matches, score distribution:")
    print(fz["score"].describe().round(1).to_string())
    print("\nGTFS stops that received the most DIFFERENT ticket names via fuzzy matching (suspicious):")
    top = fz.groupby("stop_name")["raw"].nunique().sort_values(ascending=False).head(20)
    print(top.to_string())
    print("\nLowest-scoring fuzzy matches (raw ticket name -> GTFS stop):")
    print(fz.sort_values("score").head(25)[["route", "raw", "stop_name", "score"]].to_string(index=False))
