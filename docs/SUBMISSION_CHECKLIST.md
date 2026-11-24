# Submission checklist: Phase 1 closes 24 Nov 2026, 11:59 PM IST

The form needs: **proposal + working prototype + code repo URL + demo video (MP4, max 100 MB)**.
Mock-only or slide-only submissions do not qualify, so the repo must run.

## Today's plan
| By (IST) | Do |
|---|---|
| 13:30 | `pip install -r requirements.txt`, run `./run_demo.sh` (or `run_demo.bat`). Confirm the app opens at http://127.0.0.1:8000 |
| 14:00 | Download the e-ticketing data + GTFS from delhi.transportstack.in → `data/raw/`. Run `python scripts/inspect_tickets.py data/raw/tickets.csv` |
| 14:30 | Fix `tickets.columns` in `config.yaml` if needed. Dry run with `tickets.nrows: 200000` |
| 16:00 | Full run: `build_model.py` + `validate.py`. Check `stop_matches.csv` and `reports/validation.md` |
| 17:00 | Fill in `data/spotchecks.csv` with 8–10 routes and hours you know. Re-run validate |
| 18:30 | Find a good real demo pair (busy corridor, 08:00–09:30) where a "skip this bus" tip shows |
| 19:30 | Fill the `[fill in]` spots in `docs/PROPOSAL.md` and `docs/TECHNICAL_NOTE.md` with real numbers. Export the proposal to PDF if the form wants a file |
| 20:30 | Record the demo video (`docs/DEMO_VIDEO_SCRIPT.md`). Check it's MP4 and under 100 MB |
| 21:30 | Push to GitHub (public). **Do not commit the raw data** (`.gitignore` already excludes it). Add the team names to the README |
| 22:00 | Submit. You can edit later; the latest submission counts. Don't wait for 23:55 |

## Fallback if the real data isn't usable in time
Submit with the synthetic demo, and state clearly in the proposal and video: *"pipeline validated on a
synthetic network with known ground truth; runs unchanged on the October e-ticketing data, results to be
added in Phase 2."* It's honest, and the code is real and working.

## Before pushing
- [ ] `pytest -q` passes
- [ ] README has the team, the repo URL and the video link
- [ ] No data files or secrets in the repo (`git status` should show no `data/raw`, `.env`)
- [ ] Open-source credits in the README
