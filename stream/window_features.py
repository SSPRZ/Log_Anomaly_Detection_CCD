"""window-features job: logs.parsed -> logs.features (+ logs.sequences).

One-minute tumbling event-time windows per service with a bounded
out-of-orderness watermark and allowed lateness, mirroring Flink semantics:

* a window fires when the watermark passes its end;
* records later than that but inside ``allowed_lateness`` update the window
  and re-emit it with ``revision`` + 1;
* anything later goes to the DLQ as ``late``;
* a service that sent nothing in a closed window still gets a feature vector
  (total_count = 0, growing seconds_since_last) -- that is how silence is seen.

The feature vector follows the table in Section 4.3.
"""
from __future__ import annotations

import math
import zlib
from collections import Counter, defaultdict
from dataclasses import dataclass, field

import numpy as np

from common.timeutil import minute_of_week

SEVERITY_RANK = {"FATAL": 4, "ERROR": 3, "WARN": 2, "INFO": 1, "DEBUG": 0, "TRACE": 0}


@dataclass
class _Acc:
    start: float
    count: int = 0
    errors: int = 0
    warns: int = 0
    fatal: int = 0
    hosts: set = field(default_factory=set)
    templates: Counter = field(default_factory=Counter)
    template_level: dict = field(default_factory=dict)
    template_text: dict = field(default_factory=dict)
    latencies: list = field(default_factory=list)
    status_total: int = 0
    status_5xx: int = 0
    max_ts: float = 0.0
    samples: list = field(default_factory=list)   # (rank, ts, line)
    revision: int = 0
    emitted: bool = False

    def add(self, r: dict) -> None:
        self.count += 1
        lv = r["level"]
        if lv == "ERROR":
            self.errors += 1
        elif lv == "WARN":
            self.warns += 1
        elif lv == "FATAL":
            self.fatal += 1
            self.errors += 1
        if r.get("host"):
            self.hosts.add(r["host"])
        tid = r["template_id"]
        self.templates[tid] += 1
        self.template_level[tid] = max(self.template_level.get(tid, 0), SEVERITY_RANK.get(lv, 1))
        self.template_text[tid] = r["template"]
        attrs = r.get("attrs") or {}
        lat = attrs.get("latency_ms")
        if isinstance(lat, (int, float)):
            self.latencies.append(float(lat))
        st = attrs.get("status")
        if isinstance(st, (int, float)):
            self.status_total += 1
            if st >= 500:
                self.status_5xx += 1
        self.max_ts = max(self.max_ts, r["ts"])
        rank = SEVERITY_RANK.get(lv, 1)
        line = f"{r['level']} {r['host']} {r['msg']}"
        if len(self.samples) < 5:
            self.samples.append((rank, r["ts"], line))
        else:
            worst = min(range(5), key=lambda i: (self.samples[i][0], -self.samples[i][1]))
            if rank > self.samples[worst][0]:
                self.samples[worst] = (rank, r["ts"], line)


class WindowFeatures:
    def __init__(self, cfg: dict):
        s = cfg["stream"]
        self.window = int(s["window_seconds"])
        self.ooo = float(s["out_of_orderness_seconds"])
        self.lateness = float(s["allowed_lateness_seconds"])
        self.horizon = float(s["new_template_horizon_days"]) * 86400
        self.buckets = int(s["template_hash_buckets"])
        self.canary = cfg["observability"]["canary_service"]
        self.max_ts = 0.0
        self.idle_timeout = float(s.get("idle_timeout_seconds", 60))
        self.source_max: dict[str, float] = {}
        self._wm = 0.0
        self.windows: dict[str, dict[float, _Acc]] = defaultdict(dict)
        self.last_seen: dict[str, float] = {}
        self.first_seen: dict[str, float] = {}
        self.next_window: dict[str, float] = {}         # next window start to close per service
        self.template_seen: dict[str, float] = {}       # template -> last sighting (event time)
        self.template_new_since: dict[str, float] = {}  # template -> when it (re)appeared after the horizon
        self.week_counts: dict[tuple[str, int], int] = {}
        self.late_dropped = 0

    @property
    def watermark(self) -> float:
        return self._wm

    def _compute_watermark(self) -> float:
        """Flink-style: min over active sources (services) of their max event time, minus out-of-orderness.

        A source whose newest record is more than ``idle_timeout`` behind the
        overall newest is treated as idle and excluded (``withIdleness``), so a
        silent service cannot stall the watermark -- that silence is exactly
        what the liveness features need to observe.
        """
        if not self.source_max:
            return self.max_ts - self.ooo
        active = [t for t in self.source_max.values() if t >= self.max_ts - self.idle_timeout]
        return min(active) - self.ooo

    def add(self, r: dict) -> list[dict]:
        """Add one parsed record; return any feature vectors that became final (or revised)."""
        out: list[dict] = []
        svc, ts = r["service"], r["ts"]
        wstart = ts - (ts % self.window)
        wm = self._wm
        if svc not in self.first_seen:
            self.first_seen[svc] = ts
            # a service first seen with a late record starts at the first still-open window
            self.next_window[svc] = wstart if wstart + self.window > wm else wm - (wm % self.window)
        if ts < wm and wstart + self.window <= wm:
            # window already fired
            if wstart + self.window + self.lateness <= wm:
                self.late_dropped += 1
                return [{"_late": True, "record": r}]
            acc = self.windows[svc].get(wstart)
            if acc is None:
                acc = self.windows[svc][wstart] = _Acc(wstart)
            self._observe_template(r)
            acc.add(r)
            if acc.emitted:
                acc.revision += 1
                out.append(self._features(svc, acc))
            return out
        self.last_seen[svc] = max(self.last_seen.get(svc, 0.0), ts)
        acc = self.windows[svc].get(wstart)
        if acc is None:
            acc = self.windows[svc][wstart] = _Acc(wstart)
        self._observe_template(r)
        acc.add(r)
        if ts > self.source_max.get(svc, 0.0):
            self.source_max[svc] = ts
            if ts > self.max_ts:
                self.max_ts = ts
            new_wm = self._compute_watermark()
            if new_wm > self._wm:
                self._wm = new_wm
                out.extend(self.advance())
        return out

    def _observe_template(self, r: dict) -> None:
        tid, ts = r["template_id"], r["ts"]
        prev = self.template_seen.get(tid)
        if prev is None or ts - prev > self.horizon:
            self.template_new_since[tid] = ts
        if prev is None or ts > prev:
            self.template_seen[tid] = ts

    def advance(self, watermark: float | None = None) -> list[dict]:
        """Close every window whose end is <= watermark, for every known service."""
        if watermark is not None and watermark > self._wm:
            self._wm = watermark
        wm = self._wm
        out = []
        for svc in list(self.next_window):
            while self.next_window[svc] + self.window <= wm:
                wstart = self.next_window[svc]
                acc = self.windows[svc].get(wstart) or _Acc(wstart)
                self.windows[svc][wstart] = acc
                acc.emitted = True
                out.append(self._features(svc, acc))
                self.next_window[svc] = wstart + self.window
            # purge windows past allowed lateness
            for ws in [w for w, a in self.windows[svc].items() if a.emitted and w + self.window + self.lateness < wm]:
                del self.windows[svc][ws]
        return out

    def _features(self, svc: str, a: _Acc) -> dict:
        wend = a.start + self.window
        total = a.count
        probs = np.array(list(a.templates.values()), dtype=float)
        entropy = float(-(probs / probs.sum() * np.log2(probs / probs.sum())).sum()) if total else 0.0
        hist = [0] * self.buckets
        for tid, c in a.templates.items():
            hist[zlib.crc32(tid.encode()) % self.buckets] += c
        # "new" = first sighting after >= horizon of absence, within this window
        new_tids = [t for t in a.templates if a.start <= self.template_new_since.get(t, -1) < wend + self.lateness]
        # rule input: recently-new ERROR/FATAL templates (appeared in the last 10 minutes)
        new_err = [{"template_id": t, "template": a.template_text[t], "count": a.templates[t]}
                   for t in a.templates
                   if a.template_level.get(t, 0) >= 3 and wend - self.template_new_since.get(t, -1e18) <= 600]
        mow = minute_of_week(a.start)
        prev_week = self.week_counts.get((svc, mow))
        if not a.revision:
            self.week_counts[(svc, mow)] = total
        lat = np.array(a.latencies) if a.latencies else None
        last = self.last_seen.get(svc, a.start)
        since_last = max(0.0, wend - last) if total == 0 else max(0.0, wend - a.max_ts)
        if total == 0 and last < a.start:
            since_last = wend - last
        top = a.templates.most_common(5)
        return {
            "service": svc, "window_start": a.start, "window_end": wend, "revision": a.revision,
            "total_count": total,
            "count_delta_week": (total - prev_week) if prev_week is not None else None,
            "distinct_hosts": len(a.hosts),
            "error_count": a.errors, "warn_count": a.warns,
            "error_ratio": (a.errors / total) if total else 0.0,
            "fatal": int(a.fatal > 0),
            "unique_templates": len(a.templates),
            "new_templates": len(new_tids),
            "template_entropy": entropy,
            "template_hist": hist,
            "p50_latency_ms": float(np.percentile(lat, 50)) if lat is not None else None,
            "p95_latency_ms": float(np.percentile(lat, 95)) if lat is not None else None,
            "p99_latency_ms": float(np.percentile(lat, 99)) if lat is not None else None,
            "ratio_5xx": (a.status_5xx / a.status_total) if a.status_total else 0.0,
            "seconds_since_last": since_last,
            "service_age_s": wend - self.first_seen.get(svc, a.start),
            "max_event_ts": a.max_ts or None,
            "template_counts": dict(a.templates),
            "template_text": {t: a.template_text[t] for t, _ in top} | {d["template_id"]: d["template"] for d in new_err},
            "top_templates": [{"template_id": t, "count": c} for t, c in top],
            "new_error_templates": new_err,
            "samples": [s[2] for s in sorted(a.samples, key=lambda s: (-s[0], s[1]))],
            "is_canary": svc == self.canary,
        }


class Sessionizer:
    """Session windows keyed by trace_id -> ordered template sequences for the sequence model."""

    def __init__(self, cfg: dict):
        self.gap = float(cfg["stream"]["session_gap_seconds"])
        self.open: dict[str, list] = {}
        self.last: dict[str, float] = {}
        self.max_ts = 0.0
        self.ooo = float(cfg["stream"]["out_of_orderness_seconds"])
        self._since_sweep = 0

    def add(self, r: dict) -> list[dict]:
        tid = r.get("trace_id")
        if r["ts"] > self.max_ts:
            self.max_ts = r["ts"]
        if tid:
            self.open.setdefault(tid, []).append((r["ts"], r["service"], r["template_id"]))
            self.last[tid] = max(self.last.get(tid, 0.0), r["ts"])
        self._since_sweep += 1
        if self._since_sweep >= 200:
            self._since_sweep = 0
            return self.flush(self.max_ts - self.ooo)
        return []

    def flush(self, watermark: float | None = None) -> list[dict]:
        wm = math.inf if watermark is None else watermark
        out = []
        for tid in [t for t, last in self.last.items() if last + self.gap <= wm]:
            events = sorted(self.open.pop(tid))
            del self.last[tid]
            out.append({"trace_id": tid, "start": events[0][0], "end": events[-1][0],
                        "entry_service": events[0][1],
                        "events": [{"ts": e[0], "service": e[1], "template_id": e[2]} for e in events]})
        return out
