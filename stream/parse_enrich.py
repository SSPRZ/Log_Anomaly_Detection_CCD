"""parse-enrich job: logs.raw -> logs.parsed (or logs.dlq).

1. Unwrap shipper envelopes (Fluent Bit puts the container line in ``log``
   and Kubernetes metadata in ``kubernetes``).
2. Parse JSON or legacy plain-text lines via regex profiles.
3. Validate the envelope; normalise timestamps to UTC epoch and levels.
4. Redact PII in the message and attributes.
5. Assign a stable template ID with Drain (keyed by service).
"""
from __future__ import annotations

import json
import re

from common.envelope import EnvelopeError, validate
from stream.drain import Drain
from stream.redaction import redact_text, redact_value

# Legacy plain-text profiles. Named groups: ts, level, service (optional), msg.
PROFILES: list[tuple[str, re.Pattern]] = [
    # 2026-10-09 05:41:12,318 ERROR [payments] Timeout calling ...
    ("log4j", re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?)\s+"
                         r"(?P<level>[A-Z]+)\s+\[(?P<service>[\w.-]+)\]\s+(?P<msg>.*)$")),
    # [2026-10-09T05:41:12Z] [error] payments: Timeout ...
    ("bracketed", re.compile(r"^\[(?P<ts>[^\]]+)\]\s+\[(?P<level>\w+)\]\s+(?P<service>[\w.-]+):\s+(?P<msg>.*)$")),
    # level=error ts=2026-10-09T05:41:12Z service=payments msg="Timeout ..."
    ("logfmt", re.compile(r"^(?=.*\blevel=(?P<level>\w+))(?=.*\bts=(?P<ts>\S+))(?=.*\bmsg=\"(?P<msg>[^\"]*)\")"
                          r"(?:(?=.*\bservice=(?P<service>[\w.-]+)))?.*$")),
]


def parse_plain(line: str, default_service: str | None = None) -> dict | None:
    for name, pattern in PROFILES:
        m = pattern.match(line.strip())
        if m:
            d = m.groupdict()
            if d.get("ts") and "," in d["ts"]:
                d["ts"] = d["ts"].replace(",", ".")
            d["service"] = d.get("service") or default_service
            d["_profile"] = name
            return d
    return None


def unwrap(raw: dict) -> dict:
    """Fluent Bit / OTel shapes -> a flat envelope candidate."""
    if "log" in raw and isinstance(raw["log"], str) and "msg" not in raw:
        k8s = raw.get("kubernetes") or {}
        labels = k8s.get("labels") or {}
        default_service = labels.get("app.kubernetes.io/name") or labels.get("app") or k8s.get("container_name")
        line = raw["log"].strip()
        rec: dict | None = None
        if line.startswith("{"):
            try:
                rec = json.loads(line)
            except ValueError:
                rec = None
        if rec is None:
            rec = parse_plain(line, default_service) or {"msg": line, "service": default_service}
        rec.setdefault("service", default_service)
        rec.setdefault("env", raw.get("env") or k8s.get("namespace_name"))
        rec.setdefault("host", k8s.get("pod_name") or raw.get("host"))
        return rec
    if "_raw" in raw:  # non-JSON bytes from Kafka
        return parse_plain(raw["_raw"]) or {"msg": raw["_raw"]}
    return raw


class ParseEnrich:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        d = cfg["stream"]["drain"]
        self._drain_args = dict(depth=d["depth"], sim_threshold=d["sim_threshold"],
                                max_children=d["max_children"], max_clusters=d["max_clusters"])
        self.drains: dict[str, Drain] = {}
        self.max_bytes = cfg["ingest"]["max_message_bytes"]
        self.required = tuple(cfg["ingest"]["required_fields"])
        self.default_env = cfg.get("env", "dev")
        self.dirty_templates: dict[str, dict] = {}
        self.redactions = 0

    def drain_for(self, service: str) -> Drain:
        d = self.drains.get(service)
        if d is None:
            d = self.drains[service] = Drain(namespace=service, **self._drain_args)
        return d

    def restore(self, rows: list[dict]) -> None:
        by_ns: dict[str, list[dict]] = {}
        for r in rows:
            by_ns.setdefault(r["namespace"], []).append(r)
        for ns, rs in by_ns.items():
            self.drain_for(ns).restore(rs)

    def process(self, raw: dict) -> tuple[dict | None, dict | None]:
        """Return (parsed, dlq_record); exactly one is non-None."""
        try:
            candidate = unwrap(raw)
            if candidate.get("env") in (None, "") and "env" in self.required:
                candidate["env"] = self.default_env
            rec = validate(candidate, self.max_bytes, self.required)
        except EnvelopeError as e:
            return None, {"reason": e.reason, "stage": "parse-enrich", "record": _truncate(raw)}
        except Exception as e:  # never let one bad record stop the job
            return None, {"reason": f"exception:{type(e).__name__}", "stage": "parse-enrich",
                          "record": _truncate(raw)}

        msg, n1 = redact_text(rec["msg"])
        attrs, n2 = redact_value(rec.get("attrs") or {})
        self.redactions += n1 + n2
        cluster, created = self.drain_for(rec["service"]).add(msg, rec["ts"])
        self.dirty_templates[cluster.id] = {"template_id": cluster.id, "namespace": rec["service"],
                                            "pattern": cluster.template, "size": cluster.size,
                                            "first_seen": cluster.first_seen, "last_seen": cluster.last_seen}
        parsed = {
            "ts": rec["ts"], "service": rec["service"], "env": rec["env"], "level": rec["level"],
            "host": rec.get("host") or "", "trace_id": rec.get("trace_id") or "", "msg": msg,
            "attrs": attrs, "template_id": cluster.id, "template": cluster.template,
            "template_created": created, "redactions": n1 + n2,
        }
        return parsed, None

    def take_dirty_templates(self) -> list[dict]:
        rows = list(self.dirty_templates.values())
        self.dirty_templates.clear()
        return rows


def _truncate(raw, limit: int = 4096):
    s = json.dumps(raw, default=str)
    return s if len(s) <= limit else s[:limit] + "...<truncated>"
