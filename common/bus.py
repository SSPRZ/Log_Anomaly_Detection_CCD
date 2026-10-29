"""Message bus abstraction.

Two interchangeable backends with the same semantics the pipeline relies on:

* ``KafkaBus``  -- Apache Kafka via confluent-kafka (acks=all, idempotent
  producer, zstd, manual commits => at-least-once).
* ``LocalBus``  -- an in-process, partitioned, offset-tracking log used by the
  single-process "lite" stack and by tests. It keeps Kafka's contract that
  matters here: per-key ordering inside a partition, consumer groups with
  committed offsets, and replay by resetting offsets.

Values are JSON objects. Keys are strings (service name, alert id, ...).
"""
from __future__ import annotations

import json
import threading
import time
import zlib
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable


@dataclass
class Record:
    topic: str
    partition: int
    offset: int
    key: str | None
    value: dict
    timestamp: float = field(default_factory=time.time)  # broker/produce wall time


def partition_for(key: str | None, partitions: int) -> int:
    if key is None:
        return 0
    return zlib.crc32(key.encode("utf-8")) % partitions


class Consumer:
    def poll(self, max_records: int = 500, timeout: float = 0.0) -> list[Record]:
        raise NotImplementedError

    def commit(self) -> None:
        raise NotImplementedError

    def lag(self) -> int:
        return 0

    def close(self) -> None:
        pass


class Bus:
    def produce(self, topic: str, value: dict, key: str | None = None) -> None:
        raise NotImplementedError

    def flush(self, timeout: float = 10.0) -> None:
        pass

    def consumer(self, group: str, topics: Iterable[str], from_beginning: bool = True) -> Consumer:
        raise NotImplementedError

    def close(self) -> None:
        pass


# --------------------------------------------------------------------------- local


class LocalBus(Bus):
    def __init__(self, partitions: dict[str, int] | None = None, default_partitions: int = 3):
        self._partitions_cfg = partitions or {}
        self._default = default_partitions
        self._logs: dict[str, list[list[Record]]] = {}
        self._next: dict[tuple[str, int], int] = defaultdict(int)  # next offset per partition (survives truncation)
        self._committed: dict[tuple[str, str, int], int] = defaultdict(int)
        self._lock = threading.RLock()
        self.produced = defaultdict(int)

    def _topic(self, topic: str) -> list[list[Record]]:
        if topic not in self._logs:
            n = int(self._partitions_cfg.get(topic, self._default))
            self._logs[topic] = [[] for _ in range(n)]
        return self._logs[topic]

    def produce(self, topic: str, value: dict, key: str | None = None) -> None:
        with self._lock:
            parts = self._topic(topic)
            p = partition_for(key, len(parts))
            off = self._next[(topic, p)]
            self._next[(topic, p)] = off + 1
            parts[p].append(Record(topic, p, off, key, value))
            self.produced[topic] += 1

    def consumer(self, group: str, topics: Iterable[str], from_beginning: bool = True) -> Consumer:
        return _LocalConsumer(self, group, list(topics), from_beginning)

    def end_offsets(self, topic: str) -> list[int]:
        with self._lock:
            return [self._next[(topic, i)] for i in range(len(self._topic(topic)))]

    def reset_offsets(self, group: str, topic: str, to: int = 0) -> None:
        """Replay support: rewind a consumer group (Kafka: kafka-consumer-groups --reset-offsets)."""
        with self._lock:
            for p in range(len(self._topic(topic))):
                self._committed[(group, topic, p)] = to

    def truncate_consumed(self) -> None:
        """Retention for long simulations: drop records every known group has committed."""
        with self._lock:
            for topic, parts in self._logs.items():
                for p, log in enumerate(parts):
                    groups = [g for (g, t, pp) in self._committed if t == topic and pp == p]
                    if not groups or not log:
                        continue
                    low = min(self._committed[(g, topic, p)] for g in groups)
                    base = log[0].offset
                    drop = low - base
                    if drop > 0:
                        del log[:drop]


class _LocalConsumer(Consumer):
    def __init__(self, bus: LocalBus, group: str, topics: list[str], from_beginning: bool):
        self.bus, self.group, self.topics = bus, group, topics
        self._position: dict[tuple[str, int], int] = {}
        with bus._lock:
            for t in topics:
                parts = bus._topic(t)
                for p in range(len(parts)):
                    key = (group, t, p)
                    if key not in bus._committed and not from_beginning:
                        bus._committed[key] = (parts[p][-1].offset + 1) if parts[p] else 0
                    self._position[(t, p)] = bus._committed[key]
        self._rr = 0

    def poll(self, max_records: int = 500, timeout: float = 0.0) -> list[Record]:
        out: list[Record] = []
        with self.bus._lock:
            slots = list(self._position)
            n = len(slots)
            for i in range(n):
                t, p = slots[(self._rr + i) % n]
                log = self.bus._topic(t)[p]
                if not log:
                    continue
                base = log[0].offset
                pos = max(self._position[(t, p)], base)
                idx = pos - base
                take = log[idx: idx + max(1, (max_records - len(out)))]
                if take:
                    out.extend(take)
                    self._position[(t, p)] = take[-1].offset + 1
                if len(out) >= max_records:
                    break
            self._rr = (self._rr + 1) % max(n, 1)
        if not out and timeout:
            time.sleep(min(timeout, 0.05))
        return out

    def commit(self) -> None:
        with self.bus._lock:
            for (t, p), pos in self._position.items():
                self.bus._committed[(self.group, t, p)] = pos

    def lag(self) -> int:
        with self.bus._lock:
            total = 0
            for (t, p), pos in self._position.items():
                log = self.bus._topic(t)[p]
                end = (log[-1].offset + 1) if log else pos
                total += max(0, end - self.bus._committed[(self.group, t, p)])
            return total


# --------------------------------------------------------------------------- kafka


class KafkaBus(Bus):
    def __init__(self, cfg: dict):
        from confluent_kafka import Producer  # imported lazily: optional dependency

        k = cfg["bus"]["kafka"]
        self._kcfg = k
        self._producer = Producer({
            "bootstrap.servers": k["bootstrap_servers"],
            "client.id": k.get("client_id", "lap"),
            "acks": k.get("acks", "all"),
            "enable.idempotence": bool(k.get("enable_idempotence", True)),
            "compression.type": k.get("compression_type", "zstd"),
            "linger.ms": int(k.get("linger_ms", 20)),
            **k.get("extra", {}),  # e.g. security.protocol=SSL, ssl.* for mTLS
        })
        self.errors = 0

    def _on_delivery(self, err, _msg):
        if err is not None:
            self.errors += 1

    def produce(self, topic: str, value: dict, key: str | None = None) -> None:
        payload = json.dumps(value, separators=(",", ":"), default=str).encode("utf-8")
        while True:
            try:
                self._producer.produce(topic, payload, key=key, on_delivery=self._on_delivery)
                break
            except BufferError:  # local queue full: back-pressure instead of dropping
                self._producer.poll(0.1)
        self._producer.poll(0)

    def flush(self, timeout: float = 10.0) -> None:
        self._producer.flush(timeout)

    def consumer(self, group: str, topics: Iterable[str], from_beginning: bool = True) -> Consumer:
        return _KafkaConsumer(self._kcfg, group, list(topics), from_beginning)

    def close(self) -> None:
        self.flush()


class _KafkaConsumer(Consumer):
    def __init__(self, k: dict, group: str, topics: list[str], from_beginning: bool):
        from confluent_kafka import Consumer as KConsumer

        self._c = KConsumer({
            "bootstrap.servers": k["bootstrap_servers"],
            "group.id": group,
            "enable.auto.commit": False,
            "auto.offset.reset": "earliest" if from_beginning else "latest",
            "isolation.level": "read_committed",
            **k.get("extra", {}),
        })
        self._c.subscribe(topics)

    def poll(self, max_records: int = 500, timeout: float = 0.0) -> list[Record]:
        msgs = self._c.consume(num_messages=max_records, timeout=timeout or 0.1)
        out = []
        for m in msgs:
            if m.error():
                continue
            try:
                value = json.loads(m.value())
            except (TypeError, ValueError):
                value = {"_raw": m.value().decode("utf-8", "replace") if m.value() else ""}
            key = m.key().decode("utf-8") if m.key() else None
            out.append(Record(m.topic(), m.partition(), m.offset(), key, value, m.timestamp()[1] / 1000.0))
        return out

    def commit(self) -> None:
        try:
            self._c.commit(asynchronous=False)
        except Exception:  # nothing to commit yet
            pass

    def close(self) -> None:
        self._c.close()


def make_bus(cfg: dict) -> Bus:
    backend = cfg["bus"]["backend"]
    if backend == "kafka":
        return KafkaBus(cfg)
    if backend == "local":
        return LocalBus(cfg["bus"].get("partitions"))
    raise ValueError(f"unknown bus backend {backend!r}")
