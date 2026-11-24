# SeatSense: crowdedness-aware bus journey planner for Delhi

**Transport Stack Hackathon 2026 · Problem 2: Crowdedness-aware journey planner**

Journey planners tell you *when* you will arrive. SeatSense also tells you **whether you will get a seat**,
and ranks bus options by how the trip will *feel*, not just how long it takes:

> 💡 *Skip the 08:22 bus (seat chance 10%). Wait 4 min more for the 08:26 (seat chance 98%).*

It estimates how many people are on board every bus route, in each direction, at every stop and hour of
the day, using about a month of Delhi bus e-ticketing data. That estimate is built into a journey planner
API and a map-based web app.

| | |
|---|---|
| **Input** | Bus e-ticketing / ETM records (route, boarding stop, alighting stop, time) + static GTFS |
| **Model** | Rebuilds on-board load from boardings minus alightings along each route direction, scales ticket counts up to total ridership, then computes seat probability per stop and hour |
| **Planner** | Direct + 1-transfer journeys over GTFS, next few departures each, ranked by *felt time* (crowding-weighted) |
| **Outputs** | REST API (`/api/plan`, `/api/crowding`, `/api/heatmap` …), web app, validation report |

---

## Quick start (synthetic demo data, about 1 minute)

```bash
pip install -r requirements.txt
python scripts/make_sample_data.py --out data/sample --days 14      # synthetic GTFS + e-tickets
python scripts/build_model.py --config config.sample.yaml           # build crowding model
python scripts/validate.py   --config config.sample.yaml           # report -> reports_sample/
SEATSENSE_CONFIG=config.sample.yaml uvicorn seatsense.api:app --port 8000
# open http://127.0.0.1:8000
```
Windows: `run_demo.bat`. Mac/Linux: `./run_demo.sh`.

> The sample data is **synthetic** (made-up Delhi-like routes). It exists only to test the pipeline. It
> includes hidden "true" loads, so we can check the model recovers them (correlation 0.92 on the sample).

## Running on the real Transport Stack data

1. Sign up at https://delhi.transportstack.in and request the **October 2026 bus e-ticketing** dataset and
   the **static bus GTFS**.
2. Unzip into:
   ```
   data/raw/tickets.csv          # the e-ticketing CSV (rename or set the path in config.yaml)
   data/raw/gtfs/stops.txt, routes.txt, trips.txt, stop_times.txt, calendar.txt
   ```
3. **Check the columns first:**
   ```bash
   python scripts/inspect_tickets.py data/raw/tickets.csv
   ```
   This shows what SeatSense auto-detected (route, boarding stop, alighting stop, timestamp, bus number).
   If anything is wrong, set `tickets.columns` in `config.yaml`.
4. Build, validate and serve:
   ```bash
   python scripts/build_model.py --config config.yaml
   python scripts/validate.py   --config config.yaml       # -> reports/validation.md + charts
   uvicorn seatsense.api:app --port 8000                    # uses config.yaml by default
   ```
   Tip: set `tickets.nrows: 200000` in `config.yaml` for a fast dry run first.
5. Edit `data/spotchecks.csv` (copy `data/spotchecks.example.csv`) with routes and hours your team *knows*
   are crowded or empty. The validation report scores the model against them.

### Things to check on real data
- **Route and stop matching.** `data/processed/stop_matches.csv` lists every ticket stop name and the GTFS
  stop it was matched to, with a score. Look over the fuzzy matches.
- **Scale (riders per e-ticket).** E-tickets are only a share of all riders, since many pay cash or use
  passes. With `scale: auto`, the scale is set so that the 95th-percentile peak bus is at 100% capacity.
  If you have ETM (conductor machine) totals or published ridership by route, put
  `route,scale` in a CSV and set `model.route_scale_csv`.
- **Capacity** (`model.seats`, `model.standing`). Set these to match the fleet.

## API

| Endpoint | What it does |
|---|---|
| `GET /api/plan?from_lat&from_lon&to_lat&to_lon&time=HH:MM&day=weekday\|weekend` | Journey options with seat chance, crowd label, total and felt minutes, "skip this bus" tips. Can also use `from_stop` / `to_stop` (GTFS stop_id). |
| `GET /api/crowding?route=101&stop_id=S07&day=weekday` | Hour-by-hour expected load and seat chance for a route at a stop (each direction) |
| `GET /api/heatmap?route=101` | Route × hour peak load factor (weekday and weekend) |
| `GET /api/busiest?day=weekday[&hour=9]` | Most crowded routes |
| `GET /api/stops?q=lajpat` | Stop search |
| `GET /api/health` | Status and model coverage stats |
| `GET /docs` | Interactive OpenAPI docs |

Example:
```bash
curl "http://127.0.0.1:8000/api/plan?from_stop=S07&to_stop=S09&time=08:15&day=weekday"
```

## How it works (short version, see `docs/TECHNICAL_NOTE.md`)

1. **Match** ticket route numbers and stop names to GTFS (exact ID, normalised name, then fuzzy matching
   with Delhi-specific abbreviations: NGR→NAGAR, RLY STN→RAILWAY STATION, PH-III→PHASE 3 …).
2. **Orient** each ticket onto a GTFS stop pattern (route direction): the pattern where its boarding stop
   comes before its alighting stop.
3. **Group by bus cohort:** use the scheduled offset of the boarding stop to estimate when that bus left
   its first stop, and assign the ticket to that time slot.
4. **Load profile:** add +riders at each boarding stop and −riders at each alighting stop, then take a
   running sum along the route. This gives riders on board between every pair of stops. Divide by
   service days × scheduled buses to get average load per bus.
5. **Scale** ticket counts up to total riders and compute **P(seat)** at each stop with a negative-binomial
   model that allows for bunching and day-to-day variation.
6. **Plan:** for each candidate bus, adjust load by the gap since the previous bus (longer gap, more
   riders). Score every option as
   `felt = 1.5·walk + 1.5·wait + Σ ride × crowding multiplier + transfer penalty`.
   The crowding multiplier runs from 1.0 (seated, quiet bus) to about 2.1 (standing in a packed bus).

## Repository layout
```
seatsense/            Python package
  ingest.py           ticket CSV loading + column auto-detection
  gtfs.py             GTFS loading, stop patterns, schedule offsets
  matching.py         route/stop name matching
  model.py            crowding model (load profiles, scale, P(seat))
  crowd.py            seat probability + crowding multipliers
  planner.py          journey search + felt-time ranking
  api.py              FastAPI app (serves web/ too)
web/index.html        map web app (Leaflet bundled in web/vendor)
scripts/              make_sample_data, inspect_tickets, build_model, validate
docs/                 proposal, technical note, demo video script, submission checklist
tests/                end-to-end test (pytest)
```

## Open-source credits
pandas, NumPy, FastAPI, Uvicorn, RapidFuzz, PyYAML, Matplotlib, Leaflet (BSD-2, bundled in
`web/vendor/leaflet`), OpenStreetMap tiles (© OpenStreetMap contributors).
Crowding multipliers are indicative values in line with the range reported in the crowding valuation
literature (e.g. Wardman & Whelan, 2011, *Transport Reviews*) and can be changed in `seatsense/config.py`.

## Team
_[Team name, members, contacts]_

## License
MIT
