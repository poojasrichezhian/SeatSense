"""Generate a small SYNTHETIC Delhi-like dataset (GTFS + e-tickets) so the whole
pipeline can be run and demoed before the real data arrives.

It also writes the hidden "true" bus loads, so we can check the model recovers them.
All numbers here are made up. Do not present them as real Delhi results.

    python scripts/make_sample_data.py --out data/sample --days 14
"""
from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd

STOPS = [  # id, GTFS name, ticket-system spelling, lat, lon, hub weight
    ("S01", "Kashmere Gate ISBT", "KASHMERE GATE I.S.B.T.", 28.6676, 77.2280, 2.0),
    ("S02", "Red Fort", "RED FORT", 28.6562, 77.2410, 1.0),
    ("S03", "ITO", "I.T.O.", 28.6280, 77.2410, 1.5),
    ("S04", "Pragati Maidan", "PRAGATI MAIDAN", 28.6186, 77.2433, 1.0),
    ("S05", "Sarai Kale Khan ISBT", "SARAI KALE KHAN I.S.B.T.", 28.5890, 77.2580, 1.6),
    ("S06", "Ashram Chowk", "ASHRAM CHK", 28.5720, 77.2590, 1.3),
    ("S07", "Lajpat Nagar", "LAJPAT NGR", 28.5677, 77.2433, 1.6),
    ("S08", "Moolchand", "MOOLCHAND", 28.5640, 77.2340, 1.0),
    ("S09", "Nehru Place Terminal", "NEHRU PLACE TERM", 28.5491, 77.2533, 2.0),
    ("S10", "Kalkaji Mandir", "KALKAJI MANDIR", 28.5400, 77.2590, 1.0),
    ("S11", "Govindpuri", "GOVINDPURI", 28.5340, 77.2630, 0.9),
    ("S12", "Badarpur Border", "BADARPUR BORDER", 28.4930, 77.3030, 1.2),
    ("S13", "Anand Vihar ISBT", "ANAND VIHAR I.S.B.T.", 28.6469, 77.3159, 2.0),
    ("S14", "Laxmi Nagar", "LAXMI NGR", 28.6304, 77.2777, 1.4),
    ("S15", "Akshardham", "AKSHARDHAM", 28.6127, 77.2773, 1.0),
    ("S16", "Mayur Vihar Phase 1", "MAYUR VIHAR PH-1", 28.6040, 77.2890, 1.2),
    ("S17", "Okhla Phase 3", "OKHLA PH-III", 28.5450, 77.2730, 1.3),
    ("S18", "New Delhi Railway Station", "NEW DELHI RLY STN", 28.6430, 77.2194, 2.0),
    ("S19", "Connaught Place", "CONNAUGHT PLACE", 28.6315, 77.2167, 1.8),
    ("S20", "Central Secretariat", "CENTRAL SECRETARIAT", 28.6150, 77.2120, 1.4),
    ("S21", "AIIMS", "A.I.I.M.S.", 28.5672, 77.2100, 1.8),
    ("S22", "Green Park", "GREEN PARK", 28.5590, 77.2060, 1.0),
    ("S23", "Hauz Khas", "HAUZ KHAS", 28.5494, 77.2001, 1.2),
    ("S24", "Malviya Nagar", "MALVIYA NGR", 28.5355, 77.2100, 1.0),
    ("S25", "Saket", "SAKET", 28.5245, 77.2066, 1.3),
    ("S26", "Mehrauli", "MEHRAULI", 28.5244, 77.1855, 1.0),
    ("S27", "Dwarka Mor", "DWARKA MOR", 28.6190, 77.0330, 1.6),
    ("S28", "Uttam Nagar", "UTTAM NGR", 28.6219, 77.0590, 1.4),
    ("S29", "Janakpuri", "JANAKPURI", 28.6219, 77.0878, 1.2),
    ("S30", "Tilak Nagar", "TILAK NGR", 28.6366, 77.0965, 1.1),
    ("S31", "Rajouri Garden", "RAJOURI GARDEN", 28.6490, 77.1220, 1.5),
    ("S32", "Dhaula Kuan", "DHAULA KUAN", 28.5918, 77.1615, 1.3),
    ("S33", "Munirka", "MUNIRKA", 28.5580, 77.1740, 1.1),
    ("S34", "IIT Gate", "I.I.T. GATE", 28.5450, 77.1926, 1.1),
    ("S35", "Punjabi Bagh", "PUNJABI BAGH", 28.6680, 77.1320, 1.2),
    ("S36", "Shadipur", "SHADIPUR", 28.6517, 77.1580, 0.9),
    ("S37", "Patel Nagar", "PATEL NGR", 28.6447, 77.1690, 0.9),
    ("S38", "Karol Bagh", "KAROL BAGH", 28.6519, 77.1909, 1.5),
]

ROUTES = {  # label: (stops, demand multiplier)
    "101": (["S01", "S02", "S03", "S04", "S05", "S06", "S07", "S08", "S09", "S10", "S11", "S12"], 1.7),
    "205": (["S13", "S14", "S15", "S16", "S05", "S06", "S09", "S17"], 1.0),
    "340": (["S18", "S19", "S20", "S21", "S22", "S23", "S24", "S25", "S26"], 1.3),
    "423": (["S27", "S28", "S29", "S30", "S31", "S32", "S33", "S34", "S23", "S24", "S25"], 1.2),
    "511": (["S35", "S36", "S37", "S38", "S19", "S03", "S14", "S13"], 1.1),
    "764": (["S31", "S32", "S21", "S08", "S07", "S06", "S09", "S10", "S11", "S17"], 0.6),
}

SAMPLE_RATE = 0.08  # share of riders that buy an e-ticket (hidden from the model)


def hour_profile(h: float, weekend: bool) -> float:
    if weekend:
        return 0.35 + 0.75 * np.exp(-((h - 13.5) / 4.0) ** 2) + 0.35 * np.exp(-((h - 19) / 2) ** 2)
    return (0.3 + 1.7 * np.exp(-((h - 9.0) / 1.2) ** 2) + 1.5 * np.exp(-((h - 18.5) / 1.4) ** 2)
            + 0.5 * np.exp(-((h - 13.5) / 2.5) ** 2))


def headway_min(h: float, weekend: bool) -> float:
    base = 8 if (7.5 <= h < 11 or 17 <= h < 20.5) else 12 if 6 <= h < 22 else 20
    return base + (3 if weekend else 0)


def hav(a, b):
    la1, lo1, la2, lo2 = map(np.radians, (a[0], a[1], b[0], b[1]))
    x = np.sin((la2 - la1) / 2) ** 2 + np.cos(la1) * np.cos(la2) * np.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371000 * np.arcsin(np.sqrt(x))


def hms(s):
    s = int(round(s))
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/sample")
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--start", default="2026-10-05")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    rng = np.random.default_rng(a.seed)
    out = Path(a.out)
    (out / "gtfs").mkdir(parents=True, exist_ok=True)
    S = {s[0]: s for s in STOPS}

    # ------------------------------------------------ GTFS
    pd.DataFrame([{"agency_id": "SYN", "agency_name": "Synthetic Transit", "agency_url": "https://example.org",
                   "agency_timezone": "Asia/Kolkata"}]).to_csv(out / "gtfs/agency.txt", index=False)
    pd.DataFrame([{"stop_id": s[0], "stop_code": s[0], "stop_name": s[1], "stop_lat": s[3], "stop_lon": s[4]}
                  for s in STOPS]).to_csv(out / "gtfs/stops.txt", index=False)
    pd.DataFrame([{"route_id": f"R{r}", "agency_id": "SYN", "route_short_name": r, "route_long_name": "",
                   "route_type": 3} for r in ROUTES]).to_csv(out / "gtfs/routes.txt", index=False)
    pd.DataFrame([{"service_id": "ALL", "monday": 1, "tuesday": 1, "wednesday": 1, "thursday": 1, "friday": 1,
                   "saturday": 1, "sunday": 1, "start_date": "20261001", "end_date": "20261031"}]
                 ).to_csv(out / "gtfs/calendar.txt", index=False)

    def run_times(seq, dep, slow=1.0):
        t, out_ = dep, [dep]
        for i in range(1, len(seq)):
            h = t / 3600
            peak = (8 <= h < 10.5) or (17.5 <= h < 20.5)
            v = (15.0 if peak else 22.0) / 3.6 / slow
            t = t + hav(S[seq[i - 1]][3:5], S[seq[i]][3:5]) * 1.25 / v + 25
            out_.append(t)
        return out_

    trips, stimes, sched = [], [], []
    for r, (seq0, _) in ROUTES.items():
        for d, seq in enumerate([seq0, seq0[::-1]]):
            t = 5.5 * 3600 + d * 240
            n = 0
            gap_ratio = 1.0
            while t < 23 * 3600:
                tid = f"R{r}_{d}_{n:03d}"
                times = run_times(seq, t)
                trips.append({"route_id": f"R{r}", "service_id": "ALL", "trip_id": tid, "direction_id": d})
                for i, (s, tt) in enumerate(zip(seq, times)):
                    stimes.append({"trip_id": tid, "arrival_time": hms(tt), "departure_time": hms(tt),
                                   "stop_id": s, "stop_sequence": i + 1})
                sched.append((r, d, seq, t, tid, gap_ratio))
                gap_ratio = float(rng.choice([0.5, 1.0, 1.0, 1.5]))  # uneven gaps (riders accumulate)
                t += headway_min(t / 3600, False) * 60 * gap_ratio
                n += 1
    pd.DataFrame(trips).to_csv(out / "gtfs/trips.txt", index=False)
    pd.DataFrame(stimes).to_csv(out / "gtfs/stop_times.txt", index=False)

    # ------------------------------------------------ simulate riders + e-tickets
    tickets, truth = [], []
    start = dt.date.fromisoformat(a.start)
    tno = 100000
    for day in range(a.days):
        date = start + dt.timedelta(days=day)
        weekend = date.weekday() >= 5
        slow = rng.normal(1.0, 0.08)
        for r, d, seq, t0, tid, gap_ratio in sched:
            h0 = t0 / 3600
            if weekend and rng.random() < 0.2:  # fewer buses on weekends
                continue
            demand = ROUTES[r][1]
            times = run_times(seq, t0 + rng.normal(0, 120), slow)
            n = len(seq)
            hub = np.array([S[s][5] for s in seq])
            load = 0
            alight = np.zeros(n)
            bus = f"DL1PD{1000 + (sum(map(ord, tid)) * 37 + day) % 9000}"
            for i in range(n - 1):
                h = times[i] / 3600
                lam = 6.0 * gap_ratio * demand * hub[i] * hour_profile(h, weekend) * (1 - i / n) ** 0.4
                nb = rng.poisson(lam)
                w = hub[i + 1:] * np.exp(-np.arange(1, n - i) / 6.0) + 0.05
                dest = rng.multinomial(nb, w / w.sum()) if nb else np.zeros(n - i - 1, int)
                alight[i + 1:] += dest
                load = load - alight[i] + nb
                truth.append({"route": r, "first_stop": seq[0], "stop_id": seq[i], "day_type": "weekend" if weekend else "weekday",
                              "hour": int(h), "true_load": load})
                # sample e-ticket buyers
                sampled = rng.binomial(dest, SAMPLE_RATE)
                for j, k in enumerate(sampled):
                    for _ in range(k):
                        tno += 1
                        ts = dt.datetime.combine(date, dt.time()) + dt.timedelta(seconds=times[i] + rng.uniform(0, 90))
                        to_name = S[seq[i + 1 + j]][2] if rng.random() > 0.03 else ""
                        tickets.append({"Ticket No": tno, "Route No": r if rng.random() > 0.1 else r.zfill(4),
                                        "Bus No": bus, "From Stop": S[seq[i]][2], "To Stop": to_name,
                                        "Ticket Date": ts.strftime("%d-%m-%Y"), "Ticket Time": ts.strftime("%H:%M:%S"),
                                        "Fare": 10 if j < 3 else 15 if j < 7 else 25, "No Of Passengers": 1})
    tk = pd.DataFrame(tickets)
    tk.to_csv(out / "tickets.csv", index=False)
    tr = pd.DataFrame(truth).groupby(["route", "first_stop", "stop_id", "day_type", "hour"])["true_load"].mean().reset_index()
    tr.to_csv(out / "truth_loads.csv", index=False)
    print(f"Wrote {len(tk):,} synthetic e-tickets over {a.days} days, {len(trips)} GTFS trips -> {out}")


if __name__ == "__main__":
    main()
