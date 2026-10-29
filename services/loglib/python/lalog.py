"""Shared structured-logging library (Python flavour).

Enforces the common envelope from Section 4.1 and writes one JSON object per
line to stdout. Services never talk to Kafka: Fluent Bit / OTel Collector tails
container stdout and ships it, so a Kafka outage cannot block the application.

    from lalog import get_logger
    log = get_logger("payments")
    log.error("Timeout calling bank-gateway after %d ms (attempt %d/%d)", 3000, 3, 3,
              trace_id=tid, endpoint="/v1/charge", latency_ms=3012, status=504)
"""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
from datetime import datetime, timezone
from typing import IO, Any

__all__ = ["Logger", "get_logger"]

_LEVELS = ("DEBUG", "INFO", "WARN", "ERROR", "FATAL")
_lock = threading.Lock()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class Logger:
    def __init__(self, service: str, env: str | None = None, host: str | None = None,
                 stream: IO[str] | None = None, min_level: str = "DEBUG", sink=None):
        if not service:
            raise ValueError("service is required")
        self.service = service
        self.env = env or os.environ.get("LAP_ENV", "dev")
        self.host = host or os.environ.get("HOSTNAME") or socket.gethostname()
        self.stream = stream or sys.stdout
        self.min_idx = _LEVELS.index(min_level)
        self.sink = sink  # optional callable(dict) used by the in-process simulator

    def envelope(self, level: str, msg: str, ts: float | None = None, trace_id: str | None = None,
                 **attrs: Any) -> dict:
        rec = {"ts": _iso(ts if ts is not None else time.time()), "service": self.service,
               "env": self.env, "level": level, "host": attrs.pop("host", None) or self.host,
               "msg": msg}
        if trace_id:
            rec["trace_id"] = trace_id
        if attrs:
            rec["attrs"] = attrs
        return rec

    def log(self, level: str, msg: str, *args, ts: float | None = None, trace_id: str | None = None,
            **attrs) -> dict | None:
        if _LEVELS.index(level) < self.min_idx:
            return None
        if args:
            msg = msg % args
        rec = self.envelope(level, msg, ts=ts, trace_id=trace_id, **attrs)
        if self.sink is not None:
            self.sink(rec)
        else:
            line = json.dumps(rec, separators=(",", ":"), default=str)
            with _lock:
                self.stream.write(line + "\n")
                self.stream.flush()
        return rec

    def debug(self, msg, *a, **kw): return self.log("DEBUG", msg, *a, **kw)
    def info(self, msg, *a, **kw): return self.log("INFO", msg, *a, **kw)
    def warn(self, msg, *a, **kw): return self.log("WARN", msg, *a, **kw)
    def error(self, msg, *a, **kw): return self.log("ERROR", msg, *a, **kw)
    def fatal(self, msg, *a, **kw): return self.log("FATAL", msg, *a, **kw)
    warning = warn


def get_logger(service: str, **kw) -> Logger:
    return Logger(service, **kw)
