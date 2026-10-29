"""Run one pipeline stage as a long-lived process against Kafka (full stack / Kubernetes).

    LAP__BUS__BACKEND=kafka LAP__STORAGE__BACKEND=clickhouse python lap.py stage window-features

Each stage is one consumer group and can be scaled horizontally up to the
partition count of its input topics (HPA on consumer lag in Kubernetes).
"""
from __future__ import annotations

import os
import signal
import time

from common import metrics as M
from common.bus import make_bus
from common.config import load_config
from storage.store import make_store


def run_stage(name: str) -> None:
    from pipeline import stages as S

    cfg = load_config()
    bus = make_bus(cfg)
    needs_store = name in ("parse-enrich", "anomaly-scorer", "alert-router", "clickhouse-sink")
    store = make_store(cfg) if needs_store else None
    if name == "parse-enrich":
        stage = S.ParseStage(cfg, bus, store)
    elif name == "window-features":
        stage = S.FeatureStage(cfg, bus, checkpoint_path=os.environ.get("LAP_CHECKPOINT", "data/checkpoints/wf.pkl"))
    elif name == "anomaly-scorer":
        stage = S.ScorerStage(cfg, bus, store)
    elif name == "alert-router":
        stage = S.RouterStage(cfg, bus, store, echo=True)
    elif name == "clickhouse-sink":
        stage = S.StoreSink(cfg, bus, store)
    elif name == "s3-sink":
        stage = S.ArchiveSink(cfg, bus, os.environ.get("LAP_ARCHIVE_URI", "data/archive"))
    else:
        raise SystemExit(f"unknown stage {name}")

    M.serve(int(cfg["observability"]["metrics_port"]))
    running = True

    def stop(*_):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(f"[{name}] consuming {stage.inputs} as group '{stage.group}' on {cfg['bus']['backend']}", flush=True)
    idle_s = float(cfg["stream"]["idle_timeout_seconds"])
    last_record = last_idle_push = time.time()
    while running:
        n = stage.step(2000, timeout=0.5)
        if n:
            last_record = time.time()
        if name == "window-features" and time.time() - last_record > idle_s and time.time() - last_idle_push > 5:
            # idle-source handling: only when the input is genuinely quiet (not while replaying a
            # backlog) may windows close on wall-clock time, otherwise caught-up records turn "late"
            stage.finish(time.time() - cfg["stream"]["out_of_orderness_seconds"] - idle_s)
            last_idle_push = time.time()
    if hasattr(stage, "checkpoint"):
        stage.checkpoint()
    if name in ("s3-sink", "clickhouse-sink", "parse-enrich"):
        stage.finish()  # flush buffered rows / template snapshot before exiting
    bus.close()
