"""Tier 1 -- robust statistical baseline.

For every (service, hour-of-week, feature) we keep a rolling sample of recent
*normal* values and score a new window with a robust z-score

    z = (x - median) / (1.4826 * max(MAD, floor))

falling back to hour-of-day and then service-wide samples when a slot has fewer
than ``min_samples`` values. An EWMA of each feature's z-score catches slow
drifts (memory leaks); its spread is calibrated on training data because
consecutive windows are autocorrelated.

No training is required: the baseline warms up online from the stream and can
be bootstrapped from history (``fit``). It is cheap (< 1 ms per window) and is
also what the Flink fallback path runs when the scorer is down.
"""
from __future__ import annotations

import math
from collections import defaultdict, deque

import numpy as np
import pandas as pd

from common.timeutil import hour_of_week
from detection.features import DIRECTION, TREND_FEATURES, value

MAD_K = 1.4826


class RobustBaseline:
    def __init__(self, cfg: dict, features: list[str] | None = None, max_per_slot: int = 240):
        d = cfg["detection"]
        b = d["baseline"]
        self.features = [f for f in (features or d["features"]) if f not in set(b.get("exclude_features") or [])]
        self.min_samples = int(b["min_samples"])
        self.floor = dict(b["mad_floor"])
        self.alpha = float(b["ewma_alpha"])
        self.z_cap = float(b["z_cap"])
        self.trend_freeze_z = float(b.get("trend_freeze_z", 6.0))
        self.max_per_slot = max_per_slot
        # samples[(service, slot_kind, slot, feature)] -> deque of floats
        self.samples: dict[tuple, deque] = defaultdict(lambda: deque(maxlen=self.max_per_slot))
        self.ewma: dict[tuple[str, str], float] = {}
        self.trend_stats: dict[tuple[str, str], tuple[float, float]] = {}

    # ------------------------------------------------------------------ learning
    def _slots(self, service: str, ts: float):
        how = hour_of_week(ts)
        return [(service, "how", how), (service, "hod", how % 24), (service, "all", 0)]

    def update(self, fv: dict) -> None:
        ts = fv["window_start"]
        for slot in self._slots(fv["service"], ts):
            cap = self.max_per_slot * (4 if slot[1] != "how" else 1)
            for f in self.features:
                x = value(fv, f)
                if math.isnan(x):
                    continue
                dq = self.samples[slot + (f,)]
                if dq.maxlen != cap:
                    dq = self.samples[slot + (f,)] = deque(dq, maxlen=cap)
                dq.append(x)

    def fit(self, df: pd.DataFrame, calib_days: float = 2.0) -> "RobustBaseline":
        """Bootstrap from historical feature rows (one row per service-minute).

        The trend detector is calibrated *out of sample*: the baseline is built
        from everything except the last ``calib_days``, the EWMA of raw z-scores
        is measured on those days, and then they are added to the baseline too.
        """
        df = df.sort_values("window_start")
        cut = df["window_start"].max() - calib_days * 86400
        head, tail = df[df.window_start < cut], df[df.window_start >= cut]
        in_sample = len(head) < len(df) * 0.3  # short history: calibrate in-sample
        if in_sample:
            head, tail = df, df
        for rec in head.to_dict("records"):
            self.update(rec)
        self._calibrate_trend(tail)
        if not in_sample:
            for rec in tail.to_dict("records"):
                self.update(rec)
        return self

    def _calibrate_trend(self, df: pd.DataFrame) -> None:
        for svc, g in df.sort_values("window_start").groupby("service"):
            series = defaultdict(list)
            e: dict[str, float] = {}
            for rec in g.to_dict("records"):
                raw = self._raw_z(rec)
                for f in TREND_FEATURES:
                    if f in raw:
                        e[f] = (1 - self.alpha) * e.get(f, raw[f]) + self.alpha * raw[f]
                        series[f].append(e[f])
            for f, vals in series.items():
                a = np.asarray(vals[30:] if len(vals) > 120 else vals)
                if len(a) > 10:
                    # inflate the spread a little: windows are autocorrelated and the calibration set is finite
                    self.trend_stats[(svc, f)] = (float(np.mean(a)), max(float(np.std(a)) * 1.25, 0.1))

    # ------------------------------------------------------------------ scoring
    def _stats(self, service: str, ts: float, f: str) -> tuple[float, float] | None:
        for slot in self._slots(service, ts):
            dq = self.samples.get(slot + (f,))
            if dq is not None and len(dq) >= self.min_samples:
                a = np.fromiter(dq, dtype=float)
                med = float(np.median(a))
                mad = float(np.median(np.abs(a - med)))
                return med, mad
        return None

    def _raw_z(self, fv: dict) -> dict[str, float]:
        """Signed robust z-scores (no direction clipping, no damping)."""
        svc, ts = fv["service"], fv["window_start"]
        out: dict[str, float] = {}
        for f in self.features:
            x = value(fv, f)
            if math.isnan(x):
                continue
            st = self._stats(svc, ts, f)
            if st is None:
                continue
            med, mad = st
            scale = MAD_K * max(mad, float(self.floor.get(f, self.floor.get("default", 1.0))))
            out[f] = float(np.clip((x - med) / scale, -self.z_cap, self.z_cap))
        return out

    def zscores(self, fv: dict, use_trend: bool = True, damping: float = 1.0) -> dict[str, float]:
        raw = self._raw_z(fv)
        out: dict[str, float] = {}
        for f, z in raw.items():
            d = DIRECTION.get(f, 0)
            if d > 0:
                z = max(z, 0.0)
            elif d < 0:
                z = min(z, 0.0)
            out[f] = z * damping
        if use_trend:
            svc = fv["service"]
            # acute windows are T1's job; feeding them into the slow-drift EWMA would keep it
            # "anomalous" for ~20 minutes after a short incident ends and block alert resolution
            acute = max((abs(v) for v in raw.values()), default=0.0) >= self.trend_freeze_z
            for f in TREND_FEATURES:
                if f not in raw:
                    continue
                key = (svc, f)
                mu, sd = self.trend_stats.get(key, (0.0, 2.0 * math.sqrt(self.alpha / (2 - self.alpha))))
                e = self.ewma.get(key, mu)
                if not acute:
                    e = (1 - self.alpha) * e + self.alpha * raw[f]
                self.ewma[key] = e
                # only sustained *increases* (latency creep, growing warnings) are suspicious
                out[f + "_trend"] = float(np.clip((e - mu) / sd * damping, 0.0, self.z_cap))
        return out

    @staticmethod
    def score(z: dict[str, float], z_center: float = 4.5, z_width: float = 0.8) -> float:
        """Map the strongest deviations to 0..1 (z=3 -> ~0.13, 4.5 -> 0.5, 6 -> ~0.87)."""
        if not z:
            return 0.0
        mags = sorted((abs(v) for v in z.values()), reverse=True)
        # mild bonus when several features move together
        zeff = mags[0] + 0.25 * sum(mags[1:3])
        return float(1.0 / (1.0 + math.exp(-(zeff - z_center) / z_width)))

    # ------------------------------------------------------------------ persistence
    def state(self) -> dict:
        return {"samples": {"|".join(map(str, k)): list(v) for k, v in self.samples.items()},
                "trend_stats": {"|".join(k): list(v) for k, v in self.trend_stats.items()}, "features": self.features}

    def load_state(self, st: dict) -> "RobustBaseline":
        for k, v in st["samples"].items():
            svc, kind, slot, f = k.split("|")
            key = (svc, kind, int(slot), f)
            cap = self.max_per_slot * (4 if kind != "how" else 1)
            self.samples[key] = deque(v, maxlen=cap)
        self.trend_stats = {tuple(k.split("|")): tuple(v) for k, v in st.get("trend_stats", {}).items()}
        return self
