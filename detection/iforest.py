"""Tier 2 -- Isolation Forest on the full feature vector, one model per service group.

Input per window: the scalar features robust-scaled *per service* (so one model
can serve services of very different volume) plus ``mix_shift``, the divergence
of the window's template mix from the service's normal mix, which lets the
forest notice "normal volume but a shifted template mix". Missing values are
imputed with the service median.

Raw anomaly scores are calibrated to 0..1 against the training distribution
(q50 -> 0, q90 -> 0.2, q99 -> 0.5, q99.9 -> 0.8, beyond max -> 1). Models are
exported to ONNX and served with onnxruntime; joblib is the fallback.

Division of labour with T1: a forest cannot rank a point *beyond* its training
range above the range's edge (it lands in the same leaf as the most extreme
training windows), so single-feature extremes are T1's job; T2 contributes
unusual *combinations* of individually plausible values.
"""
from __future__ import annotations

import json
import warnings
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

from detection.features import hist_proportions, scalar_vector

ANCHORS_Y = [0.0, 0.2, 0.5, 0.8, 1.0]


def js_divergence(p: np.ndarray, q: np.ndarray) -> float:
    """Jensen-Shannon divergence (base 2, 0..1) between two distributions."""
    if p.sum() <= 0 or q.sum() <= 0:
        return float("nan")
    p, q = p / p.sum(), q / q.sum()
    m = 0.5 * (p + q)
    with np.errstate(divide="ignore", invalid="ignore"):
        kl_pm = np.nansum(np.where(p > 0, p * np.log2(p / m), 0.0))
        kl_qm = np.nansum(np.where(q > 0, q * np.log2(q / m), 0.0))
    return float(0.5 * (kl_pm + kl_qm))


class ServiceScaler:
    """Per-service robust scaling + the service's normal template mix.

    The 64 hashed template buckets are summarised as one feature, ``mix_shift``
    (JS divergence of the window's mix from the service's normal mix): feeding
    64 noisy proportions to the forest directly drowns the informative scalar
    features in random splits.
    """

    def __init__(self, features: list[str], buckets: int = 64):
        self.features = features
        self.buckets = buckets
        self.params: dict[str, dict] = {}

    def _mix(self, rec: dict, svc: str) -> float:
        p = self.params.get(svc)
        if p is None or "mean_hist" not in p:
            return float("nan")
        return js_divergence(hist_proportions(rec, self.buckets), np.asarray(p["mean_hist"]))

    def fit(self, df: pd.DataFrame) -> "ServiceScaler":
        for svc, g in df.groupby("service"):
            recs = g.to_dict("records")
            hists = np.array([hist_proportions(r, self.buckets) for r in recs])
            valid = hists.sum(axis=1) > 0
            mean_hist = hists[valid].mean(axis=0) if valid.any() else np.ones(self.buckets) / self.buckets
            self.params[svc] = {"mean_hist": mean_hist.tolist()}
            mix = np.array([self._mix(r, svc) for r in recs])
            X = np.column_stack([g[self.features].astype(float).to_numpy(), mix])
            with warnings.catch_warnings():  # all-NaN columns (e.g. count_delta_week in week one) are expected
                warnings.simplefilter("ignore", RuntimeWarning)
                med = np.nanmedian(X, axis=0)
                q75, q25 = np.nanpercentile(X, 75, axis=0), np.nanpercentile(X, 25, axis=0)
                std = np.nanstd(X, axis=0)
            iqr = q75 - q25
            scale = np.where(iqr > 1e-9, iqr / 1.349, np.where(std > 1e-9, std, 1.0))
            # features never observed in training (e.g. count_delta_week in week one) are neutralised
            # at serving time instead of extrapolated
            unseen = np.isnan(X).mean(axis=0) > 0.9  # too sparse to learn a scale from
            self.params[svc].update({"median": np.nan_to_num(med).tolist(),
                                     "scale": np.nan_to_num(scale, nan=1.0).tolist(), "unseen": unseen.tolist()})
        return self

    def transform_one(self, service: str, rec: dict) -> np.ndarray:
        x = np.append(scalar_vector(rec, self.features), self._mix(rec, service))
        p = self.params.get(service)
        if p is None:
            return np.nan_to_num(x)
        med, sc = np.asarray(p["median"]), np.asarray(p["scale"])
        unseen = np.asarray(p.get("unseen") or [False] * len(x), dtype=bool)
        x = np.where(np.isnan(x) | unseen, med, x)
        return np.clip((x - med) / sc, -50, 50)


def design_matrix(df: pd.DataFrame, scaler: ServiceScaler, buckets: int = 64) -> np.ndarray:
    return np.asarray([scaler.transform_one(r["service"], r) for r in df.to_dict("records")], dtype=np.float32)


class GroupForest:
    def __init__(self, group: str, features: list[str], n_estimators: int = 200, contamination="auto",
                 seed: int = 0, buckets: int = 64):
        self.group = group
        self.features = features
        self.buckets = buckets
        self.scaler = ServiceScaler(features, buckets)
        self.model = IsolationForest(n_estimators=n_estimators, contamination=contamination, random_state=seed,
                                     n_jobs=-1)
        self.anchors_x: list[float] = []
        self._onnx = None

    def fit(self, df: pd.DataFrame) -> "GroupForest":
        self.scaler.fit(df)
        X = design_matrix(df, self.scaler, self.buckets)
        self.model.fit(X)
        raw = -self.model.score_samples(X)
        q = np.percentile(raw, [50, 90, 99, 99.9])
        top = float(raw.max()) + 0.25 * float(q[3] - q[0] + 1e-6)
        self.anchors_x = [float(v) for v in q] + [top]
        for i in range(1, len(self.anchors_x)):  # strictly increasing for np.interp
            self.anchors_x[i] = max(self.anchors_x[i], self.anchors_x[i - 1] + 1e-6)
        return self

    def raw_scores(self, X: np.ndarray) -> np.ndarray:
        if self._onnx is not None:
            sess, name = self._onnx
            outs = sess.run(None, {name: X.astype(np.float32)})
            # skl2onnx IsolationForest outputs [label, score]; score = decision_function = offset - raw
            dec = np.asarray(outs[1]).reshape(-1)
            return -(dec + self.model.offset_)
        return -self.model.score_samples(X)

    def calibrate(self, raw: np.ndarray) -> np.ndarray:
        return np.interp(raw, self.anchors_x, ANCHORS_Y, left=0.0, right=1.0)

    def score_one(self, fv: dict) -> float:
        X = self.scaler.transform_one(fv["service"], fv)[None, :].astype(np.float32)
        return float(self.calibrate(self.raw_scores(X))[0])

    def score_df(self, df: pd.DataFrame) -> np.ndarray:
        if df.empty:
            return np.zeros(0)
        return self.calibrate(self.raw_scores(design_matrix(df, self.scaler, self.buckets)))

    # ------------------------------------------------------------------ persistence
    def save(self, directory: Path) -> dict:
        directory.mkdir(parents=True, exist_ok=True)
        joblib.dump(self.model, directory / "iforest.joblib")
        meta = {"group": self.group, "features": self.features, "buckets": self.buckets,
                "scaler": self.scaler.params, "anchors_x": self.anchors_x, "offset": float(self.model.offset_),
                "onnx": False}
        try:
            from skl2onnx import to_onnx

            n_in = len(self.features) + 1
            onx = to_onnx(self.model, np.zeros((1, n_in), dtype=np.float32), target_opset={"": 17, "ai.onnx.ml": 3})
            (directory / "iforest.onnx").write_bytes(onx.SerializeToString())
            meta["onnx"] = True
        except Exception as e:  # serving falls back to joblib
            meta["onnx_error"] = f"{type(e).__name__}: {e}"
        (directory / "iforest_meta.json").write_text(json.dumps(meta), encoding="utf-8")
        return meta

    @classmethod
    def load(cls, directory: Path, prefer_onnx: bool = True) -> "GroupForest":
        meta = json.loads((directory / "iforest_meta.json").read_text(encoding="utf-8"))
        gf = cls(meta["group"], meta["features"], buckets=meta["buckets"])
        gf.model = joblib.load(directory / "iforest.joblib")
        gf.scaler.params = meta["scaler"]
        gf.scaler.buckets = gf.buckets
        gf.anchors_x = meta["anchors_x"]
        if prefer_onnx and meta.get("onnx") and (directory / "iforest.onnx").exists():
            try:
                import onnxruntime as ort

                sess = ort.InferenceSession(str(directory / "iforest.onnx"), providers=["CPUExecutionProvider"])
                gf._onnx = (sess, sess.get_inputs()[0].name)
                # parity check against sklearn so a bad export can never silently change scores
                probe = np.random.default_rng(0).normal(size=(16, len(gf.features) + 1)).astype(np.float32)
                a = gf.raw_scores(probe)
                b = -gf.model.score_samples(probe)
                if not np.allclose(a, b, atol=1e-3):
                    gf._onnx = None
            except Exception:
                gf._onnx = None
        return gf

    @property
    def backend(self) -> str:
        return "onnx" if self._onnx is not None else "sklearn"
