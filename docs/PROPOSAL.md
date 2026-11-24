# SeatSense: will I get a seat?
### Proposal · Transport Stack Hackathon 2026 · Problem 2 (Crowdedness-aware journey planner)
**Team:** _[name]_ · **Repo:** _[GitHub URL]_ · **Demo video:** _[link]_

---

## 1. The problem, from the commuter's side
For a Delhi bus commuter, a 30-minute ride seated and a 30-minute ride squeezed in the aisle are very
different trips. Yet every journey planner, including Google Maps, ranks options only by clock time.
Commuters fall back on rules of thumb ("never take the first bus after a long gap", "the 534 is hopeless at
9") that are never written down and never shown to newcomers.

Crowding information is also a **safety and accessibility** issue. It matters for women, older riders,
people with disabilities and anyone carrying luggage. It also matters to operators: riders who know when
buses are less full can spread out across buses, which evens out loads without adding a single bus.

## 2. Our idea
**SeatSense** turns about a month of e-ticketing data into a **seat-probability map of the whole bus
network**: for every route, direction, stop and hour, how many people are likely on board and the chance a
boarding rider finds a seat. It then plugs that into a journey planner that ranks options by **felt time**
instead of clock time.

What the commuter sees:

> **Take route 101 at 08:26 from Lajpat Nagar: 98% chance of a seat, arrive 08:45.**
> The fastest option (101 at 08:22) saves 4 min, but your seat chance is only 10%.
> 💡 *Skip the 08:22. Wait 4 min for the 08:26.*

### What makes it different
1. **Seat chance, not a vague "busy" label.** Each ticket has a boarding and an alighting stop. Adding
   riders on at their stop and removing them at theirs rebuilds the actual **load along the route**. So we
   can say the bus is fullest between Ashram and Nehru Place, not just that "route 101 is busy at 9".
2. **"Skip this bus" advice.** A bus that comes after a long gap picks up everyone who built up at the
   stops, while the one right behind it runs near-empty (bus bunching). SeatSense scales each bus's
   expected load by its gap and recommends waiting when the comfort gain is worth the minutes.
3. **Felt-time ranking.** Standing in a crowded bus feels longer than sitting. We weight in-vehicle time
   by a crowding multiplier (1.0 seated, up to about 2.1 standing in a crush), with values in line with
   the published crowding-valuation literature. Users can trade a few minutes for a seat.
4. **Honest about the data.** E-tickets are a *sample* of riders, since many pay cash or use passes. We
   say this openly, estimate a riders-per-ticket scale, and make it replaceable by ETM totals per route
   the moment they are available.

## 3. Data used
| Dataset | Use |
|---|---|
| Bus e-ticketing, October 2026 (about 2.56M tickets) | Boardings and alightings by route, stop and time, which give load profiles |
| Static bus GTFS (about 10.5k stops, 2.4k routes, 89k trips) | Stop order per direction, schedules, headways, journey search |
| ETM data (if granted) | Ridership scaling per route; full per-trip loads in Phase 2 |
| Real-time bus GPS / GTFS-RT (Phase 2) | Live gaps between buses, so "skip this bus" uses *actual* bunching |

## 4. Method (summary, details in the technical note)
1. Clean the tickets, then match routes and stops to GTFS (ID → normalised name → fuzzy matching with Delhi
   abbreviations).
2. Assign each ticket to a route direction (pattern) and to the bus cohort that left the first stop in
   the same time slot.
3. Add +boardings and −alightings along the pattern, take a running sum to get riders on board per stop,
   then divide by days × scheduled buses to get mean load per bus.
4. Scale to all riders, then compute P(seat) with a negative-binomial normal approximation that allows for
   over-dispersion from bunching and day-to-day variation.
5. Journey planner: direct and 1-transfer search over GTFS, with per-bus crowding adjusted by gap, ranked
   by felt time.

## 5. Working prototype (Phase 1)
- **Pipeline** (Python): ingestion with column auto-detection → matching → crowding model → validation report.
- **REST API** (FastAPI): `/api/plan`, `/api/crowding`, `/api/heatmap`, `/api/busiest`, `/api/stops`.
- **Web app**: map planner with seat-chance bars per departure, route crowding heatmap (hour × day type),
  and a most-crowded-routes list.
- **Validation**: peak and off-peak profile, spot checks against known crowded routes, and a test against
  hidden ground truth on a synthetic network. On that test the model recovers true loads with
  correlation 0.92.
- **Results on the real October 2026 data (2,559,899 e-tickets, 1–31 October):**
  - 100% of tickets carry both a boarding and an alighting stop, so true on-board loads can be rebuilt.
  - 99% of tickets are on routes matched to GTFS (2,025 of 2,259 ticket route codes); 82% of distinct
    route–stop names matched (43,143 exact, 10,395 fuzzy); 2.07M tickets (81%) used in the model.
  - Crowding estimates for **2,508 route-directions**, by stop and hour, weekday and weekend.
  - E-tickets are about **2% of bus trips**: 40.8 lakh daily riders (Delhi Economic Survey 2025-26) ÷
    about 86,000 e-ticket passengers/day gives **47.4 riders per e-ticket**. A physical capacity cap was
    needed on 380 of 1,975 routes where e-ticket use is far above average.
  - Peaks found by the model with no hard-coding: **09:00 (morning) and 18:00 (evening)** on weekdays,
    peak/off-peak ratio **about 4×**; weekends flatter and lighter (average load factor 0.14 vs 0.18).
  - Most crowded corridors: outer-Delhi commuter routes (e.g. 981 Kanjhawala/Ghevra, 991 Nangloi,
    858 Samalkha, 588 Tihar Jail–Raj Rif Line) in the 07:00–09:00 and 18:00–20:00 peaks.

## 6. Success criteria → how we meet them
| Brief asks for | SeatSense |
|---|---|
| Crowdedness estimates across routes and times of day | Per route × direction × stop × hour, weekday and weekend |
| Grounded in the ticketing data | Every number traces back to boardings and alightings; the validation report shows coverage and scale |
| Consistent with peak and off-peak patterns | Automatic peak detection, peak/off-peak ratio, spot-check table |
| Shown alongside route options | Seat chance + crowd label + felt time on every departure in the planner |

## 7. Phase 2 roadmap (2 weeks)
1. **Real-time bunching:** use GTFS-RT bus positions for live gaps between buses, giving "the next bus
   is 2 min behind a full one and nearly empty".
2. **ETM calibration:** route-level riders-per-ticket from conductor machine totals, plus trip-level loads
   where waybills identify trips.
3. **Accuracy check:** compare against observed loads or published route ridership; tune the
   multipliers and the dispersion parameter.
4. **Inclusive comfort options:** a "need a seat" mode (older riders, pregnancy, luggage); a women's
   ridership share indicator if the ticket type field allows it.
5. **Packaging as a Transport Stack component:** documented API + crowding dataset export (route × stop ×
   hour) for agencies to use in planning.

## 8. Impact
- **Commuters:** a more comfortable, predictable trip, and the choice to trade minutes for a seat.
- **Operators (DTC / DIMTS):** a stop-level picture of where and when buses are overloaded, for headway
  tuning, extra short-turn trips and fleet allocation, with no new sensors.
- **City:** a better bus experience is one of the levers for keeping riders from moving to private
  vehicles.
