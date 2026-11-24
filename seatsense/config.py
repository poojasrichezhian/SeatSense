"""Configuration loading with sensible defaults."""
from __future__ import annotations

import copy
from pathlib import Path

import yaml

DEFAULTS = {
    "data": {
        "tickets_csv": "data/raw/tickets.csv",
        "gtfs_dir": "data/raw/gtfs",
        "processed_dir": "data/processed",
        "reports_dir": "reports",
        "spotchecks_csv": "data/spotchecks.csv",
        "truth_csv": None,  # only for synthetic data
    },
    "tickets": {
        "columns": {},  # explicit mapping, e.g. {route: "Route No", board_stop: "From Stop"}
        "dayfirst": True,
        "datetime_format": None,
        "service_day_start_hour": 3,
        "holidays": [],  # ["2026-10-02", ...] treated as weekend
        "nrows": None,  # limit rows while debugging
    },
    "model": {
        "slot_minutes": 60,
        "seats": 38,
        "standing": 30,
        "scale": "auto",  # number (riders per ticket), "auto" or "ridership"
        "daily_ridership": None,  # city bus riders per day, used when scale == "ridership"
        "route_cap_lf": 1.3,  # max plausible load / capacity (crush) for a route's busy hours
        "route_cap_quantile": 0.9,
        "display_max_lf": 1.5,
        "auto_target_load_factor": 1.0,
        "auto_quantile": 0.95,
        "route_scale_csv": None,  # optional CSV: route,scale (e.g. from ETM totals)
        "dispersion_r": 4.0,
        "default_alight_stops": 6,
        "default_headway_min": 15,
        "min_obs": 3,
    },
    "gtfs": {"pattern_keep_share": 0.05, "max_patterns_per_route": 6},
    "matching": {"stop_threshold": 80},
    "planner": {
        "walk_radius_m": 700,
        "walk_speed_mps": 1.2,
        "detour_factor": 1.3,
        "max_wait_min": 40,
        "departures_per_option": 3,
        "max_options": 6,
        "min_transfer_min": 2,
        "max_transfer_wait_min": 25,
        "transfer_walk_m": 250,
        "weights": {"walk": 1.5, "wait": 1.5, "transfer_penalty_min": 5},
        # x = expected on-board load / total capacity -> multiplier on in-vehicle time
        "crowding_multipliers": {
            "seated": [[0.0, 1.0], [0.55, 1.0], [0.8, 1.15], [1.0, 1.35], [1.3, 1.5]],
            "standing": [[0.0, 1.4], [0.55, 1.5], [0.8, 1.75], [1.0, 2.1], [1.3, 2.4]],
            "unknown": 1.2,
        },
    },
}


def _deep_update(base: dict, new: dict) -> dict:
    for k, v in (new or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_update(base[k], v)
        else:
            base[k] = v
    return base


def load_config(path: str | None = None) -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    root = Path.cwd()
    if path:
        p = Path(path).resolve()
        with open(p, encoding="utf-8") as f:
            _deep_update(cfg, yaml.safe_load(f) or {})
        root = p.parent
    # resolve data paths relative to the config file's folder
    for k, v in cfg["data"].items():
        if v and not Path(v).is_absolute():
            cfg["data"][k] = str((root / v).resolve())
    if cfg["model"].get("route_scale_csv") and not Path(cfg["model"]["route_scale_csv"]).is_absolute():
        cfg["model"]["route_scale_csv"] = str((root / cfg["model"]["route_scale_csv"]).resolve())
    return cfg
