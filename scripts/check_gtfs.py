import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from seatsense.config import load_config
from seatsense.gtfs import get_gtfs

t = time.time()

g = get_gtfs(load_config("config.yaml"), rebuild=True)

print("stops:", len(g.stops))
print("routes:", len(g.routes))
print("trips:", len(g.trips))
print("stop_times:", len(g.stop_times))
print("patterns:", g.pattern_stops["pattern_id"].nunique())

print("sample stops:", g.stops["stop_name"].head(5).tolist())
print("sample routes:", g.routes["label"].head(10).tolist())

print(f"done in {time.time()-t:.0f}s")