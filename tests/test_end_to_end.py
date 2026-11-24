"""End-to-end test on the synthetic dataset:  pytest -q"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ["SEATSENSE_CONFIG"] = str(ROOT / "config.sample.yaml")


def setup_module(_):
    if not (ROOT / "data/sample/tickets.csv").exists():
        subprocess.check_call([sys.executable, "scripts/make_sample_data.py", "--out", "data/sample", "--days", "14"], cwd=ROOT)
    subprocess.check_call([sys.executable, "scripts/build_model.py", "--config", "config.sample.yaml"], cwd=ROOT)


def test_api():
    from fastapi.testclient import TestClient
    from seatsense.api import app
    c = TestClient(app)
    h = c.get("/api/health").json()
    assert h["status"] == "ok" and h["patterns_with_crowding"] > 0
    stops = c.get("/api/stops", params={"q": "lajpat"}).json()
    assert stops and stops[0]["name"].startswith("Lajpat")
    r = c.get("/api/plan", params={"from_stop": "S07", "to_stop": "S09", "time": "08:15", "day": "weekday"}).json()
    assert r["options"], r
    o = r["options"][0]
    assert o["departures"][0]["legs"][0]["crowd"]["p_seat"] is not None
    # crowding should be worse in the morning peak than at midday on the busiest route
    peak = c.get("/api/heatmap", params={"route": "101", "day": "weekday"}).json()
    lf = {int(x["hour"]): x["peak_load_factor"] for x in peak}
    assert lf[9] > lf[14]
    assert c.get("/api/busiest").json()
    assert c.get("/api/crowding", params={"route": "101", "stop_id": "S07"}).json()
    assert c.get("/").status_code == 200
