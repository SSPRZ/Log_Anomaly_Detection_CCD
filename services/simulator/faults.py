"""Fault definitions and the schedule that the workload consults.

Each injected fault doubles as a ground-truth label for evaluation
(evaluation/labels.py): the proposal's "fault injector API ... giving us
ground truth to measure against".
"""
from __future__ import annotations

import random
import threading
import uuid
from dataclasses import asdict, dataclass, field

FAULT_KINDS = ("downstream_timeout", "crash_loop", "silent", "bad_deploy", "memory_leak", "skip_step")

DEFAULT_DURATION_MIN = {
    "downstream_timeout": 20, "crash_loop": 15, "silent": 20,
    "bad_deploy": 30, "memory_leak": 120, "skip_step": 30,
}

# The service each kind targets by default in the demo system, mirroring the
# table in Section 9.1.
DEFAULT_TARGET = {
    "downstream_timeout": "payments", "crash_loop": "inventory", "silent": "notifications",
    "bad_deploy": "orders", "memory_leak": "gateway", "skip_step": "orders",
}

DEFAULT_PARAMS = {
    "downstream_timeout": {"fraction": 0.4},
    "crash_loop": {"up_s": 40, "down_s": 50},
    "silent": {},
    "bad_deploy": {"fraction": 0.25, "version": "v2.4.0"},
    "memory_leak": {"growth": 5.0},
    "skip_step": {"fraction": 0.35},
}


@dataclass
class Fault:
    kind: str
    service: str
    start: float
    end: float
    params: dict = field(default_factory=dict)
    id: str = field(default_factory=lambda: "flt-" + uuid.uuid4().hex[:8])

    def active(self, ts: float) -> bool:
        return self.start <= ts < self.end

    def progress(self, ts: float) -> float:
        return min(1.0, max(0.0, (ts - self.start) / max(1.0, self.end - self.start)))

    def p(self, name: str, default=None):
        return self.params.get(name, DEFAULT_PARAMS.get(self.kind, {}).get(name, default))

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def make(cls, kind: str, start: float, service: str | None = None, duration_min: float | None = None,
             **params) -> "Fault":
        if kind not in FAULT_KINDS:
            raise ValueError(f"unknown fault kind {kind!r}; choose from {FAULT_KINDS}")
        dur = 60.0 * (duration_min or DEFAULT_DURATION_MIN[kind])
        return cls(kind, service or DEFAULT_TARGET[kind], start, start + dur, params)


class FaultSchedule:
    """Thread-safe list of faults; the fault-injector API mutates it at runtime."""

    def __init__(self, faults: list[Fault] | None = None):
        self._faults: list[Fault] = list(faults or [])
        self._lock = threading.Lock()

    def add(self, fault: Fault) -> Fault:
        with self._lock:
            self._faults.append(fault)
        return fault

    def stop(self, fault_id: str, at: float) -> bool:
        with self._lock:
            for f in self._faults:
                if f.id == fault_id and f.end > at:
                    f.end = at
                    return True
        return False

    def active(self, service: str, ts: float) -> list[Fault]:
        with self._lock:
            return [f for f in self._faults if f.service == service and f.active(ts)]

    def first(self, service: str, kind: str, ts: float) -> Fault | None:
        for f in self.active(service, ts):
            if f.kind == kind:
                return f
        return None

    def all(self) -> list[Fault]:
        with self._lock:
            return list(self._faults)


def random_suite(start: float, hours: float, per_kind: int = 2, seed: int = 7,
                 kinds=FAULT_KINDS, min_gap_min: float = 45) -> list[Fault]:
    """Spread ``per_kind`` incidents of every kind over a window without overlap."""
    rng = random.Random(seed)
    plan = [k for k in kinds for _ in range(per_kind)]
    rng.shuffle(plan)
    services = list(DEFAULT_TARGET.values())
    faults: list[Fault] = []
    slot = (hours * 3600) / max(1, len(plan))
    for i, kind in enumerate(plan):
        dur = DEFAULT_DURATION_MIN[kind] * 60
        latest = max(0.0, slot - dur - min_gap_min * 60)
        t0 = start + i * slot + 10 * 60 + rng.uniform(0, latest)
        target = DEFAULT_TARGET[kind]
        if kind in ("crash_loop", "silent", "bad_deploy", "downstream_timeout") and rng.random() < 0.4:
            # vary the victim so models are not just memorising one service
            target = rng.choice({"downstream_timeout": ["payments"],
                                 "crash_loop": services, "silent": services,
                                 "bad_deploy": ["orders", "payments", "inventory"]}[kind])
        faults.append(Fault.make(kind, t0, service=target))
    return faults
