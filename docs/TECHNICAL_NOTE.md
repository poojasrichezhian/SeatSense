# SeatSense: technical note
Transport Stack Hackathon 2026 · Problem 2 · _[team name]_

## 1. Inputs
- **E-tickets** (October 2026, about 2.56M rows): route, boarding stop, alighting stop, timestamp,
  optionally bus number, passenger count and ticket type. Columns are auto-detected
  (`seatsense/ingest.py`); explicit mapping is possible in `config.yaml`.
- **Static GTFS**: stops, routes, trips, stop_times, calendar.

## 2. Matching tickets to the network
- Routes: normalised route numbers (upper-case, punctuation and leading zeros removed, UP/DN suffix
  ignored), matched against GTFS `route_short_name` and `route_id`.
- Stops: matched only among stops served by that route, in order: exact stop_id/stop_code → exact
  normalised name → fuzzy match (RapidFuzz WRatio blended with token-sort ratio, threshold 80). The
  normaliser expands Delhi abbreviations (NGR→NAGAR, RLY→RAILWAY, STN→STATION, PH→PHASE, III→3,
  CHK→CHOWK …) and drops filler words (BUS STOP, NEAR, STAGE …). Every match is written to
  `stop_matches.csv` for review.

## 3. Stop patterns (directions)
GTFS trips are grouped by their exact stop sequence. Per route we keep the 2 most frequent patterns
plus any pattern with at least 5% of trips. For each pattern we store stop positions and the median
scheduled departure offset from the first stop, `off(p)`.

**Orientation.** A ticket (route r, boarding b, alighting a) is assigned to the most frequent pattern of
r in which `pos(b) < pos(a)`. Tickets without a usable alighting stop are split equally across the
patterns that contain b, with an assumed ride of 6 stops (configurable).

## 4. Load model
Let ticket i have pattern k, boarding position b_i, alighting position a_i, weight w_i (passengers),
service date d_i, timestamp t_i (seconds after midnight of the service day, which starts at 03:00).

- Bus cohort slot: `s_i = floor((t_i − off_k(b_i)) / Δ)`, the slot in which that bus left the first stop
  (Δ = 60 min by default).
- Day type: weekday, or weekend/holiday.

For each (k, day type D, slot s), the flow into position p is
`F(p) = Σ_i w_i [b_i = p] − Σ_i w_i [a_i = p]`, and the riders on board when leaving p, summed over the month, are
`C(p) = Σ_{q ≤ p} F(q)`.

Mean tickets on board per bus: `L(p) = C(p) / (N_D × B_k,D,s)`, where N_D is the number of service days of
that type in the data and B the number of buses scheduled (GTFS) to leave in that slot. If no bus is
scheduled, a default headway of 15 min is used.

The slot axis is then shifted from "departed first stop in s" to "is at stop p in slot s + ⌊(Δ/2 + off(p))/Δ⌋",
so the planner can look up crowding by the time the rider is at their stop.

### Scale (riders per ticket)
E-tickets cover only part of ridership (cash, passes and free travel are not in the file). Expected riders
on board are `μ = λ_r · L`, where λ_r is the scale for route r:
- **City anchor** (`scale: ridership`, used for the real data): λ = city bus riders per day ÷ e-tickets per
  day. The Delhi Economic Survey 2025-26 reports about 40.8 lakh daily riders on DTC + cluster buses
  (FY2024-25). With about 82.6k e-tickets/day in October 2026, this gives λ ≈ 49 riders per e-ticket, i.e.
  e-tickets are roughly 2% of bus trips.
- **Physical capacity cap per route**: the e-ticket share differs strongly between routes (some routes are
  mostly digital). A bus cannot carry more than about 1.3× its nominal capacity, so for each route λ_r is
  capped so that the 90th percentile of its weekday slot peaks stays at or below 1.3 × capacity. This removes
  impossible values (e.g. 13× capacity in the first run) without hand-tuning.
- `route_scale_csv`: route-specific λ from ETM totals (preferred whenever available).
- `scale: auto` or a fixed number are also supported.

On the real data: λ = **47.4** riders per e-ticket (4.08M riders/day ÷ 86,050 e-ticket passengers/day), capped on **380 of 1,975** routes.

### Seat probability
Bus loads vary more than a Poisson count would, because of bunching, weather and day-to-day variation.
We model the load X of the bus a rider boards as negative-binomial with mean μ and variance μ + μ²/r
(r = 4), and use a normal approximation:

`P(seat) = Φ((S + 0.5 − μ) / √(μ + μ²/r))`, where S is the number of seats (38 by default; plus 30 standing = capacity 68).

### Per-bus adjustment (gaps between buses)
Riders build up at stops in proportion to the time since the last bus. For a specific trip, the planner
multiplies the expected load by `h / h̃`, where h is the scheduled gap before this bus at the boarding
stop and h̃ is the rolling median gap there, clipped to [0.4, 2.5]. In Phase 2, h comes from live GTFS-RT
positions, which captures real bunching.

## 5. Journey planner
- Access/egress: stops within 700 m, walking at 1.2 m/s with a 1.3 detour factor.
- **Direct**: trips serving an origin stop within the next 40 min and later a destination stop; per route,
  the best stop pair and its next 3 departures.
- **One transfer** (if fewer than 3 direct options): downstream stops of first-leg trips → same stop or a stop
  within 250 m → second-leg trips leaving 2–25 min after arrival → destination.
- **Felt time**: `1.5·walk + 1.5·wait + Σ_legs ride·m(P_seat, x) + 5 min per transfer`, where
  `m = P_seat·m_seated(x) + (1 − P_seat)·m_standing(x)`, x = mean load / capacity along the ride, and the curves
  run from 1.0 (seated, quiet) up to about 1.35 (seated in a crush) and from 1.4 up to about 2.1 (standing in a
  crush). These are indicative values in line with the ranges in the crowding-valuation literature
  (e.g. Wardman & Whelan 2011) and can be changed in `config.py`.
- The best departure within a route is the one with the lowest felt time. If it is not the first bus, the
  planner prints a "skip this bus" tip.

## 6. Validation
`scripts/validate.py` produces `reports/validation.md`:
1. **Coverage**: tickets read and valid, share with an alighting stop, routes and stops matched, scale.
2. **Peak patterns**: hourly boardings and load factor by day type; detected AM and PM peak hours;
   peak/off-peak ratio.
3. **Most crowded route-hours**, with the busiest stop.
4. **Spot checks**: routes and hours the team knows to be crowded or empty, compared with the model.
5. **Ground-truth recovery (synthetic)**: on a simulated 6-route network where riders are generated, 8%
   buy e-tickets, and true loads are known, the model recovers stop-hour loads with **correlation 0.92**
   and mean absolute error 3.7 riders (mean load 17). Peak detection: 09:00 and 19:00; peak/off-peak ratio
   2.2×; 5/5 spot checks agree.

**Real-data results (October 2026, 2,559,899 e-tickets):**
- Alighting stop present on 100% of tickets; 99% of tickets on GTFS-matched routes (2,025 / 2,259 route
  codes); 53,538 of 65,598 route–stop names matched (43,143 exact, 10,395 fuzzy); 2,065,585 tickets (81%)
  used; 84% of used tickets oriented by their real alighting stop.
- 2,508 route-directions with crowding estimates; 21 weekday and 11 weekend/holiday service days.
- Weekday peaks at 09:00 (average route peak load factor 0.51) and 18:00 (0.36); midday 0.13, giving a
  peak/off-peak ratio of about 4.0×. Weekend average load factor 0.14 vs weekday 0.18.
- The first run (single citywide scale) produced impossible loads (up to 13.5× capacity) on routes with
  high e-ticket use. The per-route capacity cap fixed this, and is reported openly as a data limitation.
- Busy hubs such as Asian Market are exact name matches serving 52 routes, i.e. genuine interchange points,
  not matching errors.

## 7. Limitations and next steps
- **E-ticket share varies** by route, time and rider type (for example passes and concessions). Auto-scale
  gets the network-wide level right but not differences between routes. Per-route ETM scaling fixes this.
- **Riders without tickets** (free or concession travel not recorded in the e-ticket data) are invisible unless
  ETM covers them. Scaling compensates on average only.
- **Schedule vs reality**: B (buses per slot) comes from the GTFS schedule. Cancelled trips make buses
  fuller than we estimate. Phase 2 can count actual trips from GPS.
- **Time resolution**: hourly slots are robust with sparse data. With ETM trip-level data we can model
  individual trips.
- **Capacity** is a single default. A per-fleet value (standard / e-bus) is a simple extension.
