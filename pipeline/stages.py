"""Pipeline stages. Each ``step()`` polls its consumer group, processes, produces, then commits."""
from __future__ import annotations

import pickle
import time
from typing import Callable

from common import metrics as M
from common.bus import Bus
from common.config import resolve_path
from detection.scorer import Scorer
from alerting.router import AlertRouter
from pipeline.archive import ParquetArchive
from storage.store import Store
from stream.parse_enrich import ParseEnrich
from stream.window_features import Sessionizer, WindowFeatures


class Stage:
    name = "stage"
    group = "stage"
    inputs: list[str] = []

    from_beginning = True

    def __init__(self, cfg: dict, bus: Bus):
        self.cfg, self.bus = cfg, bus
        self.t = cfg["bus"]["topics"]
        self.consumer = bus.consumer(self.group, [self.t[i] for i in self.inputs], self.from_beginning)
        self.processed = 0

    def handle(self, rec) -> None:
        raise NotImplementedError

    def after_batch(self) -> None:
        pass

    def step(self, max_records: int = 2000, timeout: float = 0.0) -> int:
        batch = self.consumer.poll(max_records, timeout)
        for r in batch:
            self.handle(r)
        if batch:
            M.RECORDS_IN.labels(stage=self.name).inc(len(batch))
            self.after_batch()
            self.bus.flush()
            self.consumer.commit()   # at-least-once: commit only after downstream produce is flushed
            self.processed += len(batch)
        M.CONSUMER_LAG.labels(group=self.group).set(self.consumer.lag())
        return len(batch)

    def _out(self, topic_key: str, value: dict, key: str | None) -> None:
        self.bus.produce(self.t[topic_key], value, key=key)
        M.RECORDS_OUT.labels(stage=self.name, topic=self.t[topic_key]).inc()


class ParseStage(Stage):
    name, group, inputs = "parse-enrich", "parse-enrich", ["raw"]

    def __init__(self, cfg, bus, store: Store | None = None):
        super().__init__(cfg, bus)
        self.job = ParseEnrich(cfg)
        self.store = store
        if store is not None:  # stable template IDs across restarts
            df = store.templates()
            if not df.empty:
                self.job.restore(df.to_dict("records"))

    def handle(self, r):
        parsed, dlq = self.job.process(r.value)
        if parsed is not None:
            self._out("parsed", parsed, parsed["service"])
        else:
            M.DLQ.labels(stage=self.name, reason=dlq["reason"].split(":")[0]).inc()
            self._out("dlq", {**dlq, "ts": time.time()}, r.key)

    template_flush_s = 5.0  # snapshot the template dictionary at most this often (one insert, not one per poll)

    def after_batch(self):
        if time.monotonic() - getattr(self, "_tpl_flushed", 0.0) >= self.template_flush_s:
            self.finish()

    def finish(self, _wm: float | None = None) -> None:
        if self.store is not None:
            rows = self.job.take_dirty_templates()
            if rows:
                self.store.upsert_templates(rows)
        self._tpl_flushed = time.monotonic()


class FeatureStage(Stage):
    name, group, inputs = "window-features", "window-features", ["parsed"]

    def __init__(self, cfg, bus, checkpoint_path: str | None = None, checkpoint_every_s: float = 30.0):
        super().__init__(cfg, bus)
        self.job = WindowFeatures(cfg)
        self.sessions = Sessionizer(cfg)
        # keyed state survives restarts (Flink: checkpoints to object storage every 30 s)
        self.checkpoint_path = resolve_path(checkpoint_path) if checkpoint_path else None
        self.checkpoint_every_s = checkpoint_every_s
        self._last_ckpt = time.time()
        if self.checkpoint_path and self.checkpoint_path.exists():
            with open(self.checkpoint_path, "rb") as fh:
                self.job, self.sessions = pickle.load(fh)

    def checkpoint(self) -> None:
        if not self.checkpoint_path:
            return
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.checkpoint_path.with_suffix(".tmp")
        with open(tmp, "wb") as fh:
            pickle.dump((self.job, self.sessions), fh, protocol=pickle.HIGHEST_PROTOCOL)
        tmp.replace(self.checkpoint_path)
        self._last_ckpt = time.time()

    def step(self, max_records: int = 2000, timeout: float = 0.0) -> int:
        n = super().step(max_records, timeout)
        if self.checkpoint_path and time.time() - self._last_ckpt > self.checkpoint_every_s:
            self.checkpoint()
        return n

    def _emit(self, out):
        for fv in out:
            if fv.get("_late"):
                M.LATE.labels(kind="dropped").inc()
                self._out("dlq", {"stage": self.name, "reason": "late", "record": fv["record"], "ts": time.time()},
                          fv["record"]["service"])
            else:
                self._out("features", fv, fv["service"])

    def handle(self, r):
        self._emit(self.job.add(r.value))
        if self.cfg["detection"]["sequence"]["enabled"]:
            for tr in self.sessions.add(r.value):
                self._out("sequences", tr, tr["entry_service"])

    def after_batch(self):
        M.WATERMARK.set(self.job.watermark)

    def finish(self, watermark: float) -> None:
        """Advance the watermark (idle source / end of a replay) and flush everything."""
        self._emit(self.job.advance(watermark))
        for tr in self.sessions.flush(watermark):
            self._out("sequences", tr, tr["entry_service"])
        self.bus.flush()


class ScorerStage(Stage):
    name, group, inputs = "anomaly-scorer", "anomaly-scorer", ["sequences", "deploys", "features"]

    def __init__(self, cfg, bus, store: Store | None = None, clock: Callable[[], float] = time.time,
                 from_beginning: bool = True):
        self.from_beginning = from_beginning
        super().__init__(cfg, bus)
        self.scorer = Scorer(cfg, clock=clock)
        self.store = store
        self._tpl_loaded = 0.0

    def handle(self, r):
        if r.topic == self.t["sequences"]:
            self.scorer.on_sequence(r.value)
        elif r.topic == self.t["deploys"]:
            self.scorer.on_deploy(r.value)
        else:
            out = self.scorer.score(r.value)
            if out is not None:
                self._out("scores", out, out["service"])

    def after_batch(self):
        # template texts for explanations (log_templates table)
        if self.store is not None and time.time() - self._tpl_loaded > 30:
            df = self.store.templates()
            if not df.empty:
                self.scorer.template_text.update(dict(zip(df.template_id, df.pattern)))
            self._tpl_loaded = time.time()


class RouterStage(Stage):
    name, group, inputs = "alert-router", "alert-router", ["scores"]

    def __init__(self, cfg, bus, store: Store | None = None, echo: bool = False, from_beginning: bool = True):
        self.from_beginning = from_beginning
        super().__init__(cfg, bus)
        self.router = AlertRouter(cfg, store, echo=echo)

    def handle(self, r):
        for ev in self.router.handle(r.value):
            self._out("alerts", ev, ev.get("alert_id") or ev.get("service"))


class StoreSink(Stage):
    """Kafka Connect ClickHouse-sink equivalent: topics -> tables (idempotent upserts where keyed).

    Rows are buffered across polls and written in large batches (``flush_rows`` or every
    ``flush_seconds``); consumer offsets are committed only after a successful flush, so a
    crash re-delivers the unflushed tail (at-least-once). One insert per small poll would
    create a MergeTree part per poll and bury ClickHouse in merges.
    """
    name, group, inputs = "clickhouse-sink", "clickhouse-sink", ["parsed", "features", "scores", "dlq", "deploys"]

    def __init__(self, cfg, bus, store: Store, store_logs: bool = True, flush_rows: int = 50_000,
                 flush_seconds: float = 5.0):
        super().__init__(cfg, bus)
        self.store = store
        self.store_logs = store_logs
        self.flush_rows, self.flush_seconds = flush_rows, flush_seconds
        self._buf: dict[str, list] = {}
        self._n = 0
        self._last_flush = time.monotonic()

    def handle(self, r):
        self._buf.setdefault(r.topic, []).append(r.value)

    def step(self, max_records: int = 2000, timeout: float = 0.0) -> int:
        batch = self.consumer.poll(max_records, timeout)
        for r in batch:
            self.handle(r)
        if batch:
            M.RECORDS_IN.labels(stage=self.name).inc(len(batch))
            self.processed += len(batch)
            self._n += len(batch)
        if self._n and (self._n >= self.flush_rows or time.monotonic() - self._last_flush >= self.flush_seconds):
            self.flush()
        M.CONSUMER_LAG.labels(group=self.group).set(self.consumer.lag())
        return len(batch)

    def flush(self) -> None:
        if not self._n:
            return
        b, t = self._buf, self.t
        self._buf, self._n = {}, 0
        try:
            if self.store_logs and b.get(t["parsed"]):
                self.store.insert_logs(b[t["parsed"]])
            if b.get(t["features"]):
                self.store.upsert_features(b[t["features"]])
            scores = [s for s in b.get(t["scores"], []) if not s.get("canary")]
            if scores:
                self.store.insert_anomalies(scores)
            if b.get(t["dlq"]):
                self.store.insert_dlq(b[t["dlq"]])
            if b.get(t["deploys"]):
                self.store.insert_deploys(b[t["deploys"]])
        except Exception:
            M.STORE_ERRORS.labels(table="any").inc()
            raise  # nothing committed: the process restarts and re-reads from the last committed offset
        self.consumer.commit()
        self._last_flush = time.monotonic()

    def finish(self, _wm: float | None = None) -> None:
        self.flush()


class ArchiveSink(Stage):
    """S3 sink: hourly Parquet files; offsets are committed only once the files are written."""
    name, group, inputs = "s3-sink", "s3-sink", ["raw", "features", "scores", "alerts"]

    def __init__(self, cfg, bus, uri: str = "data/archive", flush_seconds: float = 60.0, flush_rows: int = 200_000):
        super().__init__(cfg, bus)
        self.archive = ParquetArchive(uri)
        self.flush_seconds, self.flush_rows = flush_seconds, flush_rows
        self._n = 0
        self._last_flush = time.monotonic()

    def handle(self, r):
        self.archive.add(r.topic, r.value)

    def step(self, max_records: int = 2000, timeout: float = 0.0) -> int:
        batch = self.consumer.poll(max_records, timeout)
        for r in batch:
            self.handle(r)
        if batch:
            M.RECORDS_IN.labels(stage=self.name).inc(len(batch))
            self.processed += len(batch)
            self._n += len(batch)
        if self._n and (self._n >= self.flush_rows or time.monotonic() - self._last_flush >= self.flush_seconds):
            self.finish()
        return len(batch)

    def finish(self, _wm: float | None = None):
        self.archive.flush()
        self.consumer.commit()
        self._n = 0
        self._last_flush = time.monotonic()
