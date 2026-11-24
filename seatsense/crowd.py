"""Crowding maths shared by the model and the planner."""
from __future__ import annotations

import numpy as np


def _norm_cdf(z):
    z = np.asarray(z, dtype=float)
    # Abramowitz & Stegun 7.1.26 approximation of erf
    x = np.abs(z) / np.sqrt(2.0)
    t = 1.0 / (1.0 + 0.3275911 * x)
    y = 1.0 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * np.exp(-x * x)
    return 0.5 * (1.0 + np.sign(z) * y)


def p_seat(mu, seats: float, r: float = 4.0):
    """Probability that a rider boarding finds a seat, given the expected number
    on board when the bus leaves the stop (mu, rider included).

    Bus loads are over-dispersed (bunching, day-to-day variation), so we use a
    negative-binomial variance mu + mu^2/r with a normal approximation."""
    mu = np.asarray(mu, dtype=float)
    var = mu + mu ** 2 / max(r, 1e-6)
    z = (seats + 0.5 - mu) / np.sqrt(np.maximum(var, 1e-9))
    p = _norm_cdf(z)
    return np.where(np.isnan(mu), np.nan, np.clip(p, 0.0, 1.0))


def interp_curve(x, points):
    pts = np.asarray(points, dtype=float)
    return np.interp(x, pts[:, 0], pts[:, 1])


def crowding_multiplier(p, x, curves: dict):
    """Expected in-vehicle time multiplier: seated curve weighted by P(seat),
    standing curve otherwise. x = load / total capacity."""
    if p is None or x is None or np.isnan(p) or np.isnan(x):
        return float(curves.get("unknown", 1.2))
    return float(p * interp_curve(x, curves["seated"]) + (1 - p) * interp_curve(x, curves["standing"]))


def crowd_label(p, x):
    if p is None or np.isnan(p):
        return "unknown"
    if p >= 0.7:
        return "seats likely"
    if p >= 0.35:
        return "some seats"
    if x is not None and x < 0.85:
        return "standing room"
    return "packed"
