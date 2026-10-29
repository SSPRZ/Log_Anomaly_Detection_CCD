"""Explanations attached to every anomaly: top deviating features, moved templates, samples."""
from __future__ import annotations

import math
from collections import defaultdict


class TemplateMovement:
    """EWMA of per-template counts per service, to report which templates moved most."""

    def __init__(self, alpha: float = 0.05):
        self.alpha = alpha
        self.mean: dict[str, dict[str, float]] = defaultdict(dict)

    def moved(self, fv: dict, k: int = 3) -> list[dict]:
        svc = fv["service"]
        counts = fv.get("template_counts") or {}
        m = self.mean[svc]
        out = []
        for tid in set(counts) | set(m):
            c = counts.get(tid, 0)
            mu = m.get(tid, 0.0)
            # Poisson-style deviation; new templates (mu ~ 0) rank high
            dev = (c - mu) / math.sqrt(mu + 1.0)
            out.append((abs(dev), tid, c, mu, dev))
        out.sort(reverse=True)
        return [{"template_id": tid, "count": c, "expected": round(mu, 2), "deviation": round(dev, 2)}
                for _, tid, c, mu, dev in out[:k] if abs(dev) >= 2.0]

    def update(self, fv: dict) -> None:
        svc = fv["service"]
        counts = fv.get("template_counts") or {}
        m = self.mean[svc]
        for tid in set(counts) | set(m):
            m[tid] = (1 - self.alpha) * m.get(tid, 0.0) + self.alpha * counts.get(tid, 0)
            if m[tid] < 1e-3 and tid not in counts:
                del m[tid]


def top_drivers(z: dict[str, float], fv: dict, k: int = 3) -> list[str]:
    ranked = sorted(z.items(), key=lambda kv: abs(kv[1]), reverse=True)[:k]
    out = []
    for name, val in ranked:
        if abs(val) < 2.0:
            continue
        out.append(f"{name} z={val:+.1f}")
    if fv.get("new_templates"):
        out.append(f"new_templates={int(fv['new_templates'])}")
    return out[:k + 1]


def build_explanation(fv: dict, z: dict[str, float], moved: list[dict], rule: dict | None,
                      seq_violations: list[dict] | None, template_text: dict[str, str]) -> dict:
    def text(tid):
        return template_text.get(tid) or (fv.get("template_text") or {}).get(tid) or tid

    return {
        "top_features": [{"feature": f, "z": round(v, 2)} for f, v in
                         sorted(z.items(), key=lambda kv: abs(kv[1]), reverse=True)[:3]],
        "top_templates": [dict(m, template=text(m["template_id"])) for m in moved],
        "samples": (fv.get("samples") or [])[:5],
        "rule": rule,
        "sequence_violations": (seq_violations or [])[:3],
        "values": {k: fv.get(k) for k in ("total_count", "error_ratio", "distinct_hosts", "p95_latency_ms",
                                          "new_templates", "seconds_since_last")},
    }
