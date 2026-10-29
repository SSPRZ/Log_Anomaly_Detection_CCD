"""Validation of the shared log envelope (Section 4.1) before records enter the pipeline.

The canonical schema is ingestion/schemas/log_envelope.avsc (registered in the
Schema Registry with BACKWARD compatibility). This module applies the same
rules in-process so invalid records are diverted to logs.dlq with a reason.
"""
from __future__ import annotations

import json

from common.timeutil import parse_ts

LEVELS = {"TRACE", "DEBUG", "INFO", "WARN", "WARNING", "ERROR", "FATAL", "CRITICAL"}
LEVEL_ALIASES = {"WARNING": "WARN", "CRITICAL": "FATAL", "ERR": "ERROR", "INFORMATION": "INFO"}
REQUIRED = ("ts", "service", "env", "level", "msg")


class EnvelopeError(ValueError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def normalise_level(level: str) -> str:
    lv = str(level).strip().upper()
    return LEVEL_ALIASES.get(lv, lv)


def validate(record: dict, max_bytes: int = 1_048_576, required=REQUIRED) -> dict:
    """Return a normalised copy of ``record`` or raise EnvelopeError."""
    if not isinstance(record, dict):
        raise EnvelopeError("not_an_object")
    size = len(json.dumps(record, default=str))
    if size > max_bytes:
        raise EnvelopeError(f"oversized:{size}")
    missing = [f for f in required if record.get(f) in (None, "")]
    if missing:
        raise EnvelopeError("missing:" + ",".join(missing))
    level = normalise_level(record["level"])
    if level not in LEVELS and level not in LEVEL_ALIASES.values():
        raise EnvelopeError(f"bad_level:{record['level']}")
    try:
        ts = parse_ts(record["ts"])
    except (ValueError, TypeError):
        raise EnvelopeError("bad_ts") from None
    attrs = record.get("attrs") or {}
    if not isinstance(attrs, dict):
        raise EnvelopeError("bad_attrs")
    out = dict(record)
    out["ts"] = ts
    out["level"] = level
    out["attrs"] = attrs
    out["service"] = str(record["service"])
    out["msg"] = str(record["msg"])
    return out
