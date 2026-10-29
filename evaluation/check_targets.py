"""Fail (exit 1) when an evaluation report misses the M5 / SLO targets.

    python evaluation/check_targets.py evaluation/reports/<name>.json
"""
from __future__ import annotations

import json
import sys

TARGETS = {  # metric: (comparison, threshold)
    "precision": (">=", 0.80),
    "recall": (">=", 0.70),
    "alerts_per_service_day": ("<", 5.0),
}


def check(report: dict) -> list[str]:
    p = report["platform"]
    fails = []
    for k, (op, thr) in TARGETS.items():
        v = p[k]
        ok = v >= thr if op == ">=" else v < thr
        print(f"{k:28s} {v:8.3f}  target {op} {thr:<6}  {'ok' if ok else 'FAIL'}")
        if not ok:
            fails.append(k)
    lat = report["latency"]["e2e_p95_s"]
    ok = lat is not None and lat < 60
    print(f"{'e2e_latency_p95_s':28s} {lat if lat is not None else float('nan'):8.1f}  target < 60      "
          f"{'ok' if ok else 'FAIL'}")
    if not ok:
        fails.append("latency")
    return fails


if __name__ == "__main__":
    fails = check(json.load(open(sys.argv[1], encoding="utf-8")))
    sys.exit(1 if fails else 0)
