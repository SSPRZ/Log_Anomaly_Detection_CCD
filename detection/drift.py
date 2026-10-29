"""Population-stability index (PSI) drift monitor.

Compares the recent distribution of key features with the champion's training
window. PSI > 0.2 on any feature opens a ticket (data/drift_tickets.jsonl and
a log line; wire it to your ticketing webhook) for model review.
"""
from __future__ import annotations

import argparse
import json
import time

import numpy as np

from common import metrics as M
from common.config import load_config, resolve_path
from detection.registry import Registry
from storage.store import Store, make_store

KEY_FEATURES = ["total_count", "error_ratio", "unique_templates", "p95_latency_ms", "template_entropy"]


def psi(expected: np.ndarray, actual: np.ndarray, bins: int = 10) -> float:
    expected, actual = expected[~np.isnan(expected)], actual[~np.isnan(actual)]
    if len(expected) < 50 or len(actual) < 50:
        return 0.0
    edges = np.unique(np.quantile(expected, np.linspace(0, 1, bins + 1)))
    if len(edges) < 3:
        return 0.0
    edges[0], edges[-1] = -np.inf, np.inf
    e = np.histogram(expected, edges)[0] / len(expected)
    a = np.histogram(actual, edges)[0] / len(actual)
    e, a = np.clip(e, 1e-4, None), np.clip(a, 1e-4, None)
    return float(np.sum((a - e) * np.log(a / e)))


def run(cfg: dict, store: Store | None = None, recent_hours: float = 24, now: float | None = None) -> dict:
    store = store or make_store(cfg)
    champ = Registry(cfg).load("champion")
    if champ is None:
        return {}
    f = store.features()
    f = f[f.service != cfg["observability"]["canary_service"]]
    if f.empty:
        return {}
    now = now or float(f.window_start.max()) + 60
    ref = f[(f.window_start >= champ.meta["train_start"]) & (f.window_start < champ.meta["train_end"])]
    cur = f[f.window_start >= now - recent_hours * 3600]
    out = {}
    for svc in sorted(cur.service.unique()):
        for feat in KEY_FEATURES:
            v = psi(ref[ref.service == svc][feat].astype(float).to_numpy(),
                    cur[cur.service == svc][feat].astype(float).to_numpy())
            out[f"{svc}.{feat}"] = round(v, 4)
            M.PSI.labels(feature=f"{svc}.{feat}").set(v)
    breaches = {k: v for k, v in out.items() if v > 0.2}
    if breaches:
        path = resolve_path("data/drift_tickets.jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": time.time(), "model": champ.version, "breaches": breaches}) + "\n")
        print(f"[drift] PSI > 0.2 on {len(breaches)} feature(s); ticket written to {path}")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=24)
    a = ap.parse_args(argv)
    print(json.dumps(run(load_config(), recent_hours=a.hours), indent=2))


if __name__ == "__main__":
    main()
