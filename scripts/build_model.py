"""Build GTFS cache + crowding model.

    python scripts/build_model.py --config config.yaml [--rebuild-gtfs]
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from seatsense.config import load_config  # noqa: E402
from seatsense.model import build_model  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--rebuild-gtfs", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    build_model(load_config(a.config), rebuild_gtfs=a.rebuild_gtfs)
