"""Feature-vector helpers shared by training and serving."""
from __future__ import annotations

import math

import numpy as np

# Which direction of deviation is suspicious for each scalar feature.
#   +1: only increases matter, -1: only decreases, 0: both.
DIRECTION = {
    "total_count": 0, "count_delta_week": 0, "distinct_hosts": -1, "error_count": 1, "warn_count": 1,
    "error_ratio": 1, "unique_templates": 0, "new_templates": 1, "template_entropy": 0,
    "p50_latency_ms": 1, "p95_latency_ms": 1, "p99_latency_ms": 1, "ratio_5xx": 1, "seconds_since_last": 1,
}

# Features that get an EWMA trend detector (slow drifts such as memory leaks).
TREND_FEATURES = ("p50_latency_ms", "p95_latency_ms", "warn_count", "error_ratio")


def value(fv: dict, name: str) -> float:
    v = fv.get(name)
    if v is None:
        return math.nan
    try:
        return float(v)
    except (TypeError, ValueError):
        return math.nan


def scalar_vector(fv: dict, names: list[str]) -> np.ndarray:
    return np.array([value(fv, n) for n in names], dtype=float)


def hist_proportions(fv: dict, buckets: int = 64) -> np.ndarray:
    h = fv.get("template_hist")
    if not h:
        return np.zeros(buckets)
    a = np.asarray(h, dtype=float)
    s = a.sum()
    return a / s if s > 0 else a
