"""Validation report: coverage, peak/off-peak patterns, spot checks and (for the
synthetic data) recovery of the hidden true loads.

    python scripts/validate.py --config config.yaml
Writes <reports_dir>/validation.md and PNG charts.
"""
import argparse
import json
import pickle
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from seatsense.config import load_config  # noqa: E402
from seatsense.gtfs import DAY_TYPES  # noqa: E402

BLUE, ORANGE, GREY = "#2a6fdb", "#e8833a", "#8a8f98"


def style(ax, title, xl, yl):
    ax.set_title(title, loc="left", fontsize=12, fontweight="bold")
    ax.set_xlabel(xl)
    ax.set_ylabel(yl)
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", alpha=0.25)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    cfg = load_config(ap.parse_args().config)
    proc, rep = Path(cfg["data"]["processed_dir"]), Path(cfg["data"]["reports_dir"])
    rep.mkdir(parents=True, exist_ok=True)
    stats = json.loads((proc / "build_stats.json").read_text())
    rh = pd.read_csv(proc / "route_hour.csv", dtype={"route": str})
    with open(proc / "crowd_model.pkl", "rb") as f:
        model = pickle.load(f)
    meta = model["meta"]
    md = ["# SeatSense validation report\n"]

    # ---------- coverage
    ing = stats["ingest"]
    md += ["## 1. Data coverage\n",
           f"- Tickets read: **{ing['rows_read']:,}** ({ing['date_min'][:10]} to {ing['date_max'][:10]}), valid {ing['rows_valid']:,}",
           f"- Share of tickets with an alighting stop: **{ing['share_with_alight_stop']:.0%}**",
           f"- Ticket routes matched to GTFS: **{stats['ticket_routes_matched']} / {stats['ticket_routes']}** "
           f"({stats['share_route_matched']:.0%} of tickets)",
           f"- Stop names matched: {stats['stop_names_matched']} / {stats['stop_names_total']} {stats['stop_match_methods']}",
           f"- Tickets used in the model: **{stats['tickets_used']:,}** ({stats['share_used']:.0%}); "
           f"oriented by real alighting stop: {stats['share_oriented_by_alight_stop']:.0%}",
           f"- Route-directions with crowding estimates: **{stats['patterns_with_data']}**",
           f"- Service days: {stats['n_days']}; scale = **{stats['scale_riders_per_ticket']:.2f} riders per ticket**; "
           f"capacity {meta['seats']} seats + {meta['standing']} standing\n"]

    # ---------- peak patterns
    rh["h"] = rh["hour"].astype(int) % 24  # service day runs past midnight
    prof = rh.groupby(["day_type", "h"]).agg(boardings=("boardings_per_day", "sum"),
                                            lf=("peak_load_factor", "mean")).reset_index()
    fig, axes = plt.subplots(1, 2, figsize=(12, 4))
    for d, c in zip(DAY_TYPES, [BLUE, ORANGE]):
        p = prof[prof["day_type"] == d].sort_values("h")
        axes[0].plot(p["h"], p["boardings"], marker="o", ms=3, color=c, label=d)
        axes[1].plot(p["h"], p["lf"], marker="o", ms=3, color=c, label=d)
    style(axes[0], "Estimated boardings per hour (all routes)", "hour of day", "riders / day")
    style(axes[1], "Average peak load factor per route", "hour of day", "load / capacity")
    axes[1].axhline(meta["seats"] / meta["capacity"], color=GREY, ls="--", lw=1)
    axes[1].text(5, meta["seats"] / meta["capacity"] + 0.02, "all seats taken", color=GREY, fontsize=8)
    for ax in axes:
        ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(rep / "peak_profile.png", dpi=130)
    plt.close(fig)

    wk = prof[prof["day_type"] == "weekday"].set_index("h")
    md.append("## 2. Peak and off-peak patterns\n\n![peak profile](peak_profile.png)\n")
    if len(wk):
        am = wk.loc[(wk.index >= 6) & (wk.index < 12), "lf"]
        pm = wk.loc[(wk.index >= 15) & (wk.index < 22), "lf"]
        off = wk.loc[(wk.index >= 12) & (wk.index < 16), "lf"]
        if len(am) and len(pm) and len(off):
            md += [f"- Weekday morning peak hour: **{am.idxmax()}:00** (avg peak load factor {am.max():.2f})",
                   f"- Weekday evening peak hour: **{pm.idxmax()}:00** (avg peak load factor {pm.max():.2f})",
                   f"- Midday off-peak average: {off.mean():.2f} -> peak/off-peak ratio "
                   f"**{max(am.max(), pm.max()) / max(off.mean(), 1e-9):.2f}x**"]
        we = prof[prof["day_type"] == "weekend"]
        if len(we):
            md.append(f"- Weekend average load factor {we['lf'].mean():.2f} vs weekday {wk['lf'].mean():.2f}\n")

    top = rh[rh["obs"] >= meta["min_obs"]].sort_values("peak_load_factor", ascending=False)
    top = top.drop_duplicates(["route", "day_type"]).head(15)
    md += ["\n## 3. Most crowded route-hours\n", "| Route | Day | Hour | Peak load factor | Seat chance at busiest stop | Busiest stop |",
           "|---|---|---|---|---|---|"]
    for _, r in top.iterrows():
        md.append(f"| {r['route']} | {r['day_type']} | {int(r['hour']):02d}:00 | {r['peak_load_factor']:.2f} | "
                  f"{r['p_seat_at_peak']:.0%} | {r['peak_stop']} |")

    # ---------- spot checks
    sp = Path(cfg["data"]["spotchecks_csv"] or "")
    if sp.is_file():
        s = pd.read_csv(sp, dtype={"route": str})
        res = []
        for _, r in s.iterrows():
            x = rh[(rh["route"] == r["route"]) & (rh["day_type"] == r["day_type"]) & (rh["h"] == int(r["hour"]))]
            if x.empty:
                res.append((r, None, None))
                continue
            lf = x["peak_load_factor"].max()
            pred = "crowded" if lf >= 0.75 else "not crowded"
            res.append((r, lf, pred))
        ok = [p == r["expected"] for r, _, p in res if p]
        md += ["\n## 4. Spot checks against known patterns\n",
               "| Route | Day | Hour | Expected | Model peak load factor | Model says | Match |", "|---|---|---|---|---|---|---|"]
        for r, lf, p in res:
            md.append(f"| {r['route']} | {r['day_type']} | {int(r['hour']):02d}:00 | {r['expected']} | "
                      f"{'n/a' if lf is None else f'{lf:.2f}'} | {p or 'no data'} | {'yes' if p == r['expected'] else 'no'} |")
        if ok:
            md.append(f"\n**{sum(ok)}/{len(ok)} spot checks agree.**")

    # ---------- synthetic ground truth
    tp = cfg["data"].get("truth_csv")
    if tp and Path(tp).is_file():
        from seatsense.gtfs import GTFS
        g = GTFS.load(proc / "gtfs.pkl")
        truth = pd.read_csv(tp, dtype={"route": str})
        first = g.pattern_stops[g.pattern_stops["pos"] == 0].set_index("pattern_id")["stop_id"]
        labels = g.routes.set_index("route_id")["label"]
        rows = []
        for pid, p in model["patterns"].items():
            for pos, sid in enumerate(p["stop_ids"]):
                for d in range(2):
                    for s in range(meta["n_slots"]):
                        v = p["load"][d, s, pos]
                        if np.isfinite(v):
                            rows.append((str(labels[p["route_id"]]), first[pid], sid, DAY_TYPES[d], s, float(v) * p["scale"]))
        est = pd.DataFrame(rows, columns=["route", "first_stop", "stop_id", "day_type", "hour", "est_load"])
        cmp_ = truth.merge(est, on=["route", "first_stop", "stop_id", "day_type", "hour"])
        mae = (cmp_["est_load"] - cmp_["true_load"]).abs().mean()
        corr = cmp_[["est_load", "true_load"]].corr().iloc[0, 1]
        fig, ax = plt.subplots(figsize=(5, 5))
        ax.scatter(cmp_["true_load"], cmp_["est_load"], s=6, alpha=0.4, color=BLUE)
        lim = max(cmp_["true_load"].max(), cmp_["est_load"].max())
        ax.plot([0, lim], [0, lim], color=GREY, lw=1)
        style(ax, "Synthetic test: estimated vs true load", "true riders on board", "estimated riders on board")
        fig.tight_layout()
        fig.savefig(rep / "truth_scatter.png", dpi=130)
        plt.close(fig)
        md += ["\n## 5. Recovery test on synthetic data (known ground truth)\n",
               f"- Stop-hour cells compared: {len(cmp_):,}",
               f"- Mean absolute error: **{mae:.1f} riders** (mean true load {cmp_['true_load'].mean():.1f})",
               f"- Correlation: **{corr:.2f}**\n", "![truth scatter](truth_scatter.png)"]

    (rep / "validation.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))
    print(f"\nWrote {rep / 'validation.md'}")


if __name__ == "__main__":
    main()
