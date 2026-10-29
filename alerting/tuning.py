"""Weekly feedback-driven threshold tuning (G6).

For each service, the share of alerts marked "false alarm" in the last
``days`` moves that service's threshold offset: noisy services get a higher
threshold, services whose alerts are consistently real get a slightly lower
one. Offsets are bounded and written to data/models/service_thresholds.json,
which the scorer reads on hot reload. The verdicts also flow into the next
training run as labels (detection/train.py:feedback_labels).
"""
from __future__ import annotations

import argparse
import json
import time

from common.config import load_config, resolve_path
from storage.store import Store, make_store


def tune(cfg: dict, store: Store | None = None, days: float = 7, now: float | None = None,
         step: float = 0.05, max_offset: float = 0.25, min_feedback: int = 3) -> dict:
    store = store or make_store(cfg)
    now = now or time.time()
    path = resolve_path(cfg["detection"]["registry_dir"]) / "service_thresholds.json"
    current = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    fb = store.feedback()
    alerts = store.alerts()
    if fb.empty or alerts.empty:
        return current
    fb = fb[fb.ts >= now - days * 86400].sort_values("ts").groupby("alert_id").tail(1)
    j = fb.merge(alerts[["alert_id", "service"]], on="alert_id")
    out = dict(current)
    for svc, g in j.groupby("service"):
        if len(g) < min_feedback:
            continue
        fa = (g.verdict == "false_alarm").mean()
        off = float(current.get(svc, 0.0))
        if fa > 0.4:
            off += step
        elif fa < 0.1:
            off -= step / 2
        out[svc] = round(max(-max_offset / 2, min(max_offset, off)), 3)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=7)
    a = ap.parse_args(argv)
    print(json.dumps(tune(load_config(), days=a.days), indent=2))


if __name__ == "__main__":
    main()
