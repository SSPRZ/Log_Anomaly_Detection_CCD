"""Incident-level evaluation (Section 9.2).

* An injected fault is an incident affecting its service and, for faults that
  propagate, the services upstream of it (callers see the errors too).
* An alert is a true positive if it opens on an affected service during the
  incident (from 1 minute before the start to ``grace`` after the end);
  otherwise it is a false positive.
* Recall = detected incidents / incidents; precision = TP alerts / alerts.
* Detection delay = first matching alert's open time - fault start (event time).
"""
from __future__ import annotations

from collections import defaultdict

import numpy as np
import pandas as pd
import yaml

from common.config import resolve_path
from services.simulator.faults import Fault

PROPAGATES = {"downstream_timeout": True, "crash_loop": True, "bad_deploy": True, "memory_leak": True,
              "silent": False, "skip_step": False}


def callers_map() -> dict[str, set]:
    topo = yaml.safe_load(open(resolve_path("config/topology.yaml"), encoding="utf-8"))
    callers = defaultdict(set)
    for src, dsts in (topo.get("calls") or {}).items():
        for d in dsts:
            callers[d].add(src)
    return callers


def affected(f: Fault, callers: dict[str, set]) -> set[str]:
    out = {f.service}
    if f.kind == "skip_step":
        return {"orders", "payments"}
    if PROPAGATES.get(f.kind):
        stack = [f.service]
        while stack:
            s = stack.pop()
            for c in callers.get(s, ()):
                if c not in out:
                    out.add(c)
                    stack.append(c)
    return out


def evaluate(alerts: pd.DataFrame, faults: list[Fault], start: float, end: float, n_services: int,
             grace_s: float = 300, time_col: str = "first_window") -> dict:
    callers = callers_map()
    alerts = alerts[(alerts[time_col] >= start) & (alerts[time_col] < end)].copy() if len(alerts) else alerts
    incidents = [(f, affected(f, callers)) for f in faults if start <= f.start < end]
    matched_inc: dict[str, float] = {}
    tp = fp = 0
    per_kind = defaultdict(lambda: {"incidents": 0, "detected": 0, "delays": []})
    alert_match = []
    for a in (alerts.to_dict("records") if len(alerts) else []):
        t = a[time_col]
        hit = None
        for f, svcs in incidents:
            if a["service"] in svcs and f.start - 60 <= t <= f.end + grace_s:
                hit = f
                break
        alert_match.append(hit.id if hit else None)
        if hit:
            tp += 1
            delay = a.get("opened_at", t + 60) - hit.start
            if hit.id not in matched_inc or delay < matched_inc[hit.id]:
                matched_inc[hit.id] = delay
        else:
            fp += 1
    for f, _ in incidents:
        k = per_kind[f.kind]
        k["incidents"] += 1
        if f.id in matched_inc:
            k["detected"] += 1
            k["delays"].append(matched_inc[f.id])
    n_inc = len(incidents)
    recall = len(matched_inc) / n_inc if n_inc else 0.0
    precision = tp / (tp + fp) if tp + fp else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    delays = [d for d in matched_inc.values()]
    days = max(1e-9, (end - start) / 86400)
    first_per_incident = len(matched_inc)
    return {
        "incidents": n_inc, "detected": len(matched_inc), "alerts": tp + fp, "tp_alerts": tp, "fp_alerts": fp,
        "precision": precision, "recall": recall, "f1": f1,
        "detection_delay_s_median": float(np.median(delays)) if delays else None,
        "detection_delay_s_p95": float(np.percentile(delays, 95)) if delays else None,
        "alerts_per_service_day": (tp + fp) / max(1, n_services) / days,
        "false_alerts_per_service_day": fp / max(1, n_services) / days,
        "duplicate_rate": (tp - first_per_incident) / tp if tp else 0.0,
        "per_kind": {k: {"incidents": v["incidents"], "detected": v["detected"],
                         "recall": v["detected"] / v["incidents"] if v["incidents"] else 0.0,
                         "median_delay_s": float(np.median(v["delays"])) if v["delays"] else None}
                     for k, v in sorted(per_kind.items())},
        "_alert_match": alert_match,
    }


def static_threshold_alerts(features: pd.DataFrame, errors_in_5min: int = 50, dedup_s: float = 900) -> pd.DataFrame:
    """Today's alerting: 'more than 50 errors in 5 minutes', per service, de-duplicated like the router."""
    rows = []
    for svc, g in features.sort_values("window_start").groupby("service"):
        roll = g["error_count"].fillna(0).rolling(5, min_periods=1).sum().to_numpy()
        last = -1e18
        for ws, v in zip(g["window_start"].to_numpy(), roll):
            if v > errors_in_5min:
                if ws - last > dedup_s:
                    rows.append({"service": svc, "first_window": float(ws), "opened_at": float(ws) + 60})
                last = ws
    return pd.DataFrame(rows, columns=["service", "first_window", "opened_at"])
