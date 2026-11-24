# Demo video script (about 3 minutes, screen recording + voice-over)

Record with OBS / Loom / the Windows Game Bar (Win+G) / QuickTime. 1080p. Keep it under 100 MB (a 3-min
1080p screen recording is typically 20–60 MB). Export as **MP4**.

---

### 0:00–0:20 · Hook (face cam or title slide)
> "This is Ashram Chowk at 8:20 in the morning. Two buses on route 101 are four minutes apart. One will be
> packed, the other half empty. Every journey planner treats them as the same bus. SeatSense doesn't."

### 0:20–0:45 · What it is
Show the web app with the map.
> "SeatSense is a crowdedness-aware journey planner for Delhi buses. It uses a month of e-ticketing data,
> where every ticket has a boarding and an alighting stop, to rebuild how many people are on every bus at
> every stop and hour. Then it shows your chance of a seat for each bus."

### 0:45–1:30 · The money shot: planner
- From **Lajpat Nagar** to **Nehru Place**, weekday, **08:15**. Click *Find buses*.
- Point at the headline and the 💡 tip: *"Skip the 08:22 (10% seat chance). Wait 4 min for the 08:26 (98%)."*
- Point at the seat-chance bars on each departure and the "feels like" minutes.
- Click another option card. The map redraws the route, coloured by crowding.
> "We rank by felt time: waiting and standing in a packed bus count more than sitting. So four extra
> minutes for a seat wins."

### 1:30–2:00 · Crowd patterns tab
- Route **101** heatmap: red at 08–10 and 18–20 on weekdays, calmer at weekends.
- Most-crowded-routes list.
> "The same model gives operators a stop-level view of where buses are overloaded, with no new sensors."

### 2:00–2:35 · Under the hood
Show `reports/validation.md` and the two charts.
> "Each ticket is placed on its route direction. We add riders where they board, remove them where they
> alight, and take the running total along the route. E-tickets are only a sample of riders, so we scale
> them up, and we say so openly. On a synthetic network with known true loads, we recover them with 0.92
> correlation. On the October data, we detect the morning peak at [__] and the evening peak at [__]."

Quick flash of `/docs` (Swagger) and the `/api/plan` JSON.

### 2:35–3:00 · Phase 2 + close
> "Next: live bus positions, so 'skip this bus' uses real bunching, and per-route calibration from ETM data.
> SeatSense: pick your bus by time *and* by whether you'll get a seat."

---
**Before recording**: run on the real data if it's ready. Otherwise say clearly in the video that the demo
uses the synthetic sample network and the pipeline runs unchanged on the October dataset.
