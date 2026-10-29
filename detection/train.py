"""Nightly training job with evaluation gate (Section 4.5, "Model lifecycle").

1. Pull ``train_days`` of features (and traces) from storage.
2. Split chronologically: first 80 % train, last 20 % holdout.
3. Fit T1 baseline, T2 forests (per service group), T3 sequence model.
4. Build the evaluation set: holdout windows + injected anomalies (+ windows
   labelled by responder feedback), score each tier, tune ensemble weights and
   threshold for F1 under the alert-rate budget.
5. Gate: F1 must not regress vs the current champion on the *same* evaluation
   set and the projected alert rate must stay within budget.
6. Register: first model -> champion; later models -> challenger (shadow for
   ``shadow_days``) unless ``--promote`` is given.
"""
from __future__ import annotations

import common.preload  # noqa: F401  (torch before pandas on Windows)

import argparse
import copy
import itertools
import json
import time

import numpy as np
import pandas as pd

from common.config import load_config
from detection.baseline import RobustBaseline
from detection.ensemble import Debouncer, Ensemble
from detection.iforest import GroupForest
from detection.registry import Bundle, Registry
from detection.sequence import SequenceDetector
from storage.store import Store, make_store

INJECTIONS = ("volume_drop", "volume_spike", "error_spike", "latency", "new_templates", "mix_shift", "host_drop")


def inject(df: pd.DataFrame, frac: float = 0.08, seed: int = 0) -> tuple[pd.DataFrame, np.ndarray, list[str]]:
    """Copy a sample of holdout rows and perturb them into labelled anomalies."""
    rng = np.random.default_rng(seed)
    base = df.reset_index(drop=True)
    n = max(10, int(len(base) * frac))
    picks = base.sample(n=min(n, len(base)), random_state=seed).copy().reset_index(drop=True)
    kinds = []
    for i in range(len(picks)):
        kind = INJECTIONS[i % len(INJECTIONS)]
        kinds.append(kind)
        r = picks.loc[i]
        tc = float(r["total_count"] or 0)
        if kind == "volume_drop":
            f = rng.uniform(0.05, 0.3)
            for c in ("total_count", "error_count", "warn_count"):
                picks.at[i, c] = float(r[c] or 0) * f
            picks.at[i, "seconds_since_last"] = float(r["seconds_since_last"] or 0) + rng.uniform(20, 60)
        elif kind == "volume_spike":
            picks.at[i, "total_count"] = tc * rng.uniform(3, 6)
        elif kind == "error_spike":
            extra = max(5.0, tc * rng.uniform(0.15, 0.4))
            picks.at[i, "error_count"] = float(r["error_count"] or 0) + extra
            picks.at[i, "total_count"] = tc + extra
            picks.at[i, "error_ratio"] = picks.at[i, "error_count"] / max(1.0, picks.at[i, "total_count"])
            picks.at[i, "ratio_5xx"] = min(1.0, float(r["ratio_5xx"] or 0) + rng.uniform(0.15, 0.4))
        elif kind == "latency":
            m = rng.uniform(3, 8)
            for c in ("p50_latency_ms", "p95_latency_ms", "p99_latency_ms"):
                if pd.notna(r[c]):
                    picks.at[i, c] = float(r[c]) * m
        elif kind == "new_templates":
            k = int(rng.integers(3, 7))
            picks.at[i, "new_templates"] = float(r["new_templates"] or 0) + k
            picks.at[i, "unique_templates"] = float(r["unique_templates"] or 0) + k
            picks.at[i, "warn_count"] = float(r["warn_count"] or 0) + 3 * k
        elif kind == "mix_shift":
            h = list(r["template_hist"] or [])
            if h:
                h = list(np.roll(h, int(rng.integers(5, 30))))
                picks.at[i, "template_hist"] = h
            picks.at[i, "template_entropy"] = float(r["template_entropy"] or 0) + rng.uniform(0.8, 1.5)
        elif kind == "host_drop":
            picks.at[i, "distinct_hosts"] = max(0.0, float(r["distinct_hosts"] or 0) - 1 - int(rng.integers(0, 2)))
            picks.at[i, "total_count"] = tc * rng.uniform(0.4, 0.7)
    eval_df = pd.concat([base, picks], ignore_index=True)
    labels = np.concatenate([np.zeros(len(base), dtype=int), np.ones(len(picks), dtype=int)])
    return eval_df, labels, ["normal"] * len(base) + kinds


def feedback_labels(store: Store, df_all: pd.DataFrame) -> tuple[pd.DataFrame, np.ndarray]:
    """Responder verdicts -> labelled windows (false alarms are the most valuable negatives)."""
    fb = store.feedback()
    if fb.empty:
        return df_all.iloc[0:0], np.zeros(0, dtype=int)
    alerts = store.alerts()
    if alerts.empty:
        return df_all.iloc[0:0], np.zeros(0, dtype=int)
    latest = fb.sort_values("ts").groupby("alert_id").tail(1).merge(alerts, on="alert_id")
    rows, labels = [], []
    for r in latest.itertuples():
        w = df_all[(df_all.service == r.service) & (df_all.window_start >= r.first_window)
                   & (df_all.window_start <= r.last_window)]
        rows.append(w)
        labels.extend([1 if r.verdict == "real" else 0] * len(w))
    if not rows:
        return df_all.iloc[0:0], np.zeros(0, dtype=int)
    return pd.concat(rows, ignore_index=True), np.asarray(labels, dtype=int)


def tier_scores(bundle: Bundle, df: pd.DataFrame, n_sequential: int = 0) -> pd.DataFrame:
    """Score every tier exactly as the serving path does.

    The first ``n_sequential`` rows (the real holdout, ordered by service and
    time) run through the stateful trend detector like live traffic; injected
    copies are scored without advancing that state.
    """
    out = pd.DataFrame(index=df.index)
    recs = df.to_dict("records")
    base = copy.deepcopy(bundle.baseline)
    base.ewma = {}
    out["stats"] = [RobustBaseline.score(base.zscores(r, use_trend=i < n_sequential)) for i, r in enumerate(recs)]
    forest = np.full(len(df), np.nan)
    for g, f in bundle.forests.items():
        mask = (df["service"].map(bundle.service_group) == g).to_numpy()
        if mask.any():
            forest[mask] = f.score_df(df[mask])
    out["iforest"] = forest
    return out


def prf(pred: np.ndarray, y: np.ndarray) -> dict:
    tp = int(((pred == 1) & (y == 1)).sum())
    fp = int(((pred == 1) & (y == 0)).sum())
    fn = int(((pred == 0) & (y == 1)).sum())
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    return {"precision": p, "recall": r, "f1": f1, "tp": tp, "fp": fp, "fn": fn}


def projected_alert_rate(df: pd.DataFrame, scores: np.ndarray, threshold: float, cfg: dict) -> float:
    """Alerts per service per day on (normal) holdout windows after debounce + 15-min dedup."""
    e = cfg["detection"]["ensemble"]
    dedup = cfg["alerting"]["dedup_minutes"] * 60
    d = df.assign(_s=scores).sort_values(["service", "window_start"])
    alerts = 0
    for _svc, g in d.groupby("service"):
        deb = Debouncer(e["debounce_windows"], e["debounce_hits"])
        last = -1e18
        for ws, s in zip(g["window_start"], g["_s"]):
            if deb.update(_svc, s >= threshold) and ws - last > dedup:
                alerts += 1
                last = ws
    days = max(1e-9, (df["window_start"].max() - df["window_start"].min() + 60) / 86400)
    return alerts / max(1, df["service"].nunique()) / days


def tune(tiers: pd.DataFrame, y: np.ndarray, normal_df: pd.DataFrame, normal_tiers: pd.DataFrame, cfg: dict,
         seq_weight: float) -> tuple[dict, float, dict]:
    budget = cfg["detection"]["evaluation_gate"]["max_alerts_per_service_day"]
    best = None
    for ws_, th in itertools.product(np.linspace(0.2, 0.8, 7), np.linspace(0.35, 0.8, 10)):
        w = {"stats": round(float(ws_), 2), "iforest": round(float(1 - ws_), 2), "sequence": seq_weight}
        ens = Ensemble(w, th)
        s = np.array([ens.combine({"stats": a, "iforest": None if np.isnan(b) else b})
                      for a, b in zip(tiers["stats"], tiers["iforest"])])
        m = prf((s >= th).astype(int), y)
        sn = np.array([ens.combine({"stats": a, "iforest": None if np.isnan(b) else b})
                       for a, b in zip(normal_tiers["stats"], normal_tiers["iforest"])])
        rate = projected_alert_rate(normal_df, sn, th, cfg)
        m["alerts_per_service_day"] = rate
        key = (rate <= budget, m["f1"], -rate)
        if best is None or key > best[0]:
            best = (key, w, float(th), m)
    _, w, th, m = best
    return w, th, m


def load_traces(store: Store, start: float, end: float, limit: int = 60000) -> list[dict]:
    logs = store.search_logs(start=start, end=end, limit=4_000_000)
    logs = logs[logs["trace_id"].astype(bool)]
    if logs.empty:
        return []
    traces = []
    for tid, g in logs.sort_values("ts").groupby("trace_id", sort=False):
        traces.append({"trace_id": tid, "start": float(g["ts"].iloc[0]),
                       "events": [{"ts": float(t), "service": s, "template_id": tp}
                                  for t, s, tp in zip(g["ts"], g["service"], g["template_id"])]})
        if len(traces) >= limit:
            break
    return traces


def train(cfg: dict, store: Store | None = None, end: float | None = None, days: float | None = None,
          promote: bool | None = None, seed: int = 0, verbose: bool = True) -> dict:
    t0 = time.time()
    store = store or make_store(cfg)
    reg = Registry(cfg)
    d = cfg["detection"]
    canary = cfg["observability"]["canary_service"]
    days = days or d["iforest"]["train_days"]
    all_f = store.features(with_payload=True)
    all_f = all_f[all_f.service != canary]
    if all_f.empty:
        raise RuntimeError("no features in storage -- run the pipeline first")
    end = end or float(all_f["window_start"].max()) + 60
    start = max(float(all_f["window_start"].min()), end - days * 86400)
    df = all_f[(all_f.window_start >= start) & (all_f.window_start < end)].reset_index(drop=True)
    split = start + 0.8 * (end - start)
    train_df = df[df.window_start < split]
    hold_df = df[df.window_start >= split].reset_index(drop=True)
    log = (lambda *a: print("[train]", *a)) if verbose else (lambda *a: None)
    log(f"windows: train={len(train_df)} holdout={len(hold_df)} services={sorted(df.service.unique())}")

    groups = d["iforest"]["service_groups"]
    service_group = {s: g for g, ss in groups.items() for s in ss}
    for s in df.service.unique():  # services not listed get their own group
        service_group.setdefault(s, f"svc_{s}")
    baseline = RobustBaseline(cfg).fit(train_df)
    forests = {}
    for g in sorted(set(service_group.values())):
        part = train_df[train_df.service.map(service_group) == g]
        if len(part) >= 200:
            forests[g] = GroupForest(g, d["features"], d["iforest"]["n_estimators"], d["iforest"]["contamination"],
                                     seed, cfg["stream"]["template_hash_buckets"]).fit(part)
    log(f"T1 baseline fitted; T2 forests: {list(forests)}")

    seq = None
    if d["sequence"]["enabled"]:
        traces = load_traces(store, start, split)
        if len(traces) >= 100:
            sc = d["sequence"]
            seq = SequenceDetector(sc["window"], sc["top_k"], sc["backend"], sc["epochs"]).fit(traces)
            log(f"T3 sequence model ({seq.backend}) trained on {len(traces)} traces")

    version = reg.next_version()
    bundle = Bundle(version, baseline, forests, seq, dict(d["ensemble"]["weights"]), d["ensemble"]["threshold"],
                    {"train_start": start, "train_end": split, "holdout_end": end, "created": time.time()},
                    service_group)

    eval_df, y, kinds = inject(hold_df, seed=seed)
    fb_df, fb_y = feedback_labels(store, df)
    if len(fb_df):
        eval_df = pd.concat([eval_df, fb_df], ignore_index=True)
        y = np.concatenate([y, fb_y])
        kinds += ["feedback"] * len(fb_y)
    tiers = tier_scores(bundle, eval_df, n_sequential=len(hold_df))
    normal_tiers = tiers.iloc[: len(hold_df)]
    w, th, m = tune(tiers, y, hold_df, normal_tiers, cfg, d["ensemble"]["weights"].get("sequence", 0.2))
    bundle.weights, bundle.threshold = w, th
    per_kind = {}
    ens = Ensemble(w, th)
    comb = np.array([ens.combine({"stats": a, "iforest": None if np.isnan(b) else b})
                     for a, b in zip(tiers["stats"], tiers["iforest"])])
    for k in set(kinds) - {"normal"}:
        mask = np.array([kk == k for kk in kinds])
        per_kind[f"recall_{k}"] = float((comb[mask] >= th).mean())
    metrics = {**m, **per_kind, "n_eval": int(len(y)), "n_pos": int(y.sum()), "train_seconds": time.time() - t0}
    log(f"tuned weights={w} threshold={th:.2f} -> P={m['precision']:.3f} R={m['recall']:.3f} "
        f"F1={m['f1']:.3f} alerts/svc/day={m['alerts_per_service_day']:.2f}")

    # ---- evaluation gate against the current champion on the same evaluation set
    gate = d["evaluation_gate"]
    champion = reg.load("champion")
    passed, reason = True, "first model"
    if champion is not None:
        ct = tier_scores(champion, eval_df, n_sequential=len(hold_df))
        cens = Ensemble(champion.weights, champion.threshold)
        cs = np.array([cens.combine({"stats": a, "iforest": None if np.isnan(b) else b})
                       for a, b in zip(ct["stats"], ct["iforest"])])
        cm = prf((cs >= champion.threshold).astype(int), y)
        metrics["champion_f1"] = cm["f1"]
        passed = m["f1"] >= cm["f1"] - gate["max_f1_regression"]
        reason = f"F1 {m['f1']:.3f} vs champion {cm['f1']:.3f}"
    if m["alerts_per_service_day"] > gate["max_alerts_per_service_day"]:
        passed, reason = False, f"alert budget exceeded ({m['alerts_per_service_day']:.2f}/svc/day)"
    metrics["gate_passed"] = passed

    # serve with a baseline that has seen the most recent data, too
    for rec in hold_df.sort_values("window_start").to_dict("records"):
        bundle.baseline.update(rec)

    if not passed:
        reg.register(bundle, metrics, alias=None)
        log(f"gate FAILED ({reason}); {version} archived")
        return {"version": version, "alias": None, "metrics": metrics, "reason": reason}
    alias = "champion" if champion is None or promote else "challenger"
    reg.register(bundle, metrics, alias=alias)
    log(f"gate passed ({reason}); {version} registered as {alias} in {time.time() - t0:.1f}s")
    return {"version": version, "alias": alias, "metrics": metrics, "reason": reason}


def promote_after_shadow(cfg: dict, store: Store | None = None) -> str | None:
    """Promote the challenger once it has shadowed for shadow_days without a worse alert rate."""
    reg = Registry(cfg)
    idx = reg.index()
    ch = idx.get("challenger")
    if not ch:
        return None
    since = idx["versions"][ch].get("shadow_since", time.time())
    if time.time() - since < cfg["detection"]["shadow_days"] * 86400:
        return None
    return reg.promote(ch, reason="shadow period completed")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Train, evaluate and register anomaly models")
    ap.add_argument("--days", type=float, default=None)
    ap.add_argument("--promote", action="store_true", help="skip shadow mode and promote if the gate passes")
    ap.add_argument("--rollback", action="store_true")
    a = ap.parse_args(argv)
    cfg = load_config()
    if a.rollback:
        print("champion is now", Registry(cfg).rollback())
        return
    res = train(cfg, days=a.days, promote=a.promote)
    print(json.dumps(res, indent=2, default=str))


if __name__ == "__main__":
    main()
