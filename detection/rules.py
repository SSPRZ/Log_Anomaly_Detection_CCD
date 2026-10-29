"""Hard rules: cheap, high-precision guardrails that models may smooth over.

* new_error_template -- a template first seen (after a 7-day absence) in the
  last 10 minutes, at ERROR/FATAL level, occurring >= N times in the window.
* liveness           -- a service that has been reporting goes silent for
  longer than the timeout.

Hard rules bypass the 2-of-3 debounce.
"""
from __future__ import annotations


class Rules:
    def __init__(self, cfg: dict, warmup_s: float = 6 * 3600):
        r = cfg["detection"]["rules"]
        self.min_count = int(r["new_error_template_min_count"])
        self.liveness = float(r["liveness_timeout_seconds"])
        self.warmup_s = warmup_s

    def evaluate(self, fv: dict) -> dict | None:
        if fv.get("service_age_s", 0) < self.warmup_s:
            return None  # every template is "new" while a service warms up
        hits = []
        for t in fv.get("new_error_templates") or []:
            if t["count"] >= self.min_count:
                hits.append({"rule": "new_error_template", "template_id": t["template_id"],
                             "template": t["template"], "count": t["count"]})
        since = fv.get("seconds_since_last") or 0.0
        if fv.get("total_count", 0) == 0 and since >= self.liveness:
            hits.append({"rule": "liveness", "seconds_since_last": since})
        if not hits:
            return None
        return {"fired": [h["rule"] for h in hits], "details": hits}
