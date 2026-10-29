"""Single-process "lite" stack: simulator -> LocalBus -> all stages -> SQLite.

Two modes:
* ``simulate(start, end)`` -- accelerated event-time replay (days in minutes),
  used for bootstrapping training history and for evaluation.
* ``run_realtime()`` -- wall-clock pacing with the fault-injector API, for
  live demos with the dashboard.
"""
from __future__ import annotations

import sys
import threading
import time

from common.bus import LocalBus
from pipeline.stages import ArchiveSink, FeatureStage, ParseStage, RouterStage, ScorerStage, StoreSink
from services.simulator.faults import FaultSchedule
from services.simulator.workload import EventStream, Shop
from storage.store import Store


class LiteStack:
    def __init__(self, cfg: dict, store: Store, archive_uri: str | None = None, echo: bool = False,
                 realtime: bool = False):
        self.cfg = cfg
        self.store = store
        self.bus = LocalBus(cfg["bus"].get("partitions"))
        self.t = cfg["bus"]["topics"]
        self.parse = ParseStage(cfg, self.bus, store)
        self.features = FeatureStage(cfg, self.bus)
        self.sink = StoreSink(cfg, self.bus, store)
        self.archive = ArchiveSink(cfg, self.bus, archive_uri) if archive_uri else None
        self.realtime = realtime
        self.echo = echo
        self.scorer: ScorerStage | None = None
        self.router: RouterStage | None = None

    def clock(self) -> float:
        # in accelerated mode "now" is the newest event time seen by the window job,
        # i.e. the moment the watermark crossed -- so latency reflects windowing, not the replay speed
        return time.time() if self.realtime else self.features.job.max_ts

    def enable_detection(self) -> None:
        """Start scorer + router (joining at the latest offsets, like a fresh deployment)."""
        self.scorer = ScorerStage(self.cfg, self.bus, self.store, clock=self.clock, from_beginning=False)
        self.router = RouterStage(self.cfg, self.bus, self.store, echo=self.echo, from_beginning=False)

    def stages(self):
        s = [self.parse, self.features]
        if self.scorer:
            s += [self.scorer, self.router]
        s.append(self.sink)
        if self.archive:
            s.append(self.archive)
        return s

    def pump(self) -> int:
        total = 0
        while True:
            n = sum(st.step(5000) for st in self.stages())
            total += n
            if n == 0:
                return total

    def flush(self) -> None:
        """Write everything buffered (sink batches, template snapshot, archive files) to storage."""
        self.parse.finish()
        self.sink.finish()
        if self.archive:
            self.archive.finish()

    def on_deploy(self, ev: dict) -> None:
        self.bus.produce(self.t["deploys"], ev, key=ev["service"])

    # ------------------------------------------------------------------ accelerated
    def simulate(self, shop: Shop, start: float, end: float, progress: bool = True, pump_every_s: float = 10.0,
                 label: str = "") -> int:
        """Replay [start, end) as fast as possible; stages are pumped every ``pump_every_s`` of event time,
        which bounds how far partitions can drift apart (as Kafka consumers polling continuously would)."""
        shop.on_deploy = self.on_deploy
        stream = EventStream(shop, self.cfg["observability"]["canary_interval_seconds"])
        n = 0
        t_wall = time.time()
        last_report = last_pump = start
        pumps = 0
        for arrival, rec in stream.generate(start, end):
            self.bus.produce(self.t["raw"], rec, key=rec["service"])
            n += 1
            if arrival - last_pump >= pump_every_s:
                last_pump = arrival
                self.pump()
                pumps += 1
                if pumps % 500 == 0:
                    self.bus.truncate_consumed()
                if progress and arrival - last_report >= 6 * 3600:
                    last_report = arrival
                    done = (arrival - start) / max(1.0, end - start)
                    rate = n / max(1e-6, time.time() - t_wall)
                    sys.stdout.write(f"\r  {label} {done:6.1%}  {n:>9,} records  {rate:>7,.0f} rec/s")
                    sys.stdout.flush()
        self.pump()
        self.features.finish(end + self.cfg["stream"]["out_of_orderness_seconds"])
        self.pump()
        self.flush()
        if progress:
            sys.stdout.write(f"\r  {label} 100.0%  {n:>9,} records in {time.time() - t_wall:.0f}s{' ' * 20}\n")
        return n

    # ------------------------------------------------------------------ realtime
    def run_realtime(self, shop: Shop, duration_s: float | None = None, api_port: int | None = 8090,
                     metrics_port: int | None = None) -> None:
        from common import metrics as M

        shop.on_deploy = self.on_deploy
        if metrics_port:
            M.serve(metrics_port)
        if api_port:
            from chaos.fault_injector import serve_in_thread

            serve_in_thread(shop.faults, api_port)
        stop = threading.Event()
        stream = EventStream(shop, self.cfg["observability"]["canary_interval_seconds"])
        start = time.time()
        end = start + duration_s if duration_s else float("inf")

        def produce():
            t = start - (start % 60)
            while not stop.is_set() and t < end:
                for arrival, rec in stream.generate(t, t + 60):
                    delay = arrival - time.time()
                    if delay > 0:
                        time.sleep(min(delay, 1.0)) if delay < 1.0 else stop.wait(delay)
                    if stop.is_set():
                        return
                    self.bus.produce(self.t["raw"], rec, key=rec["service"])
                t += 60

        th = threading.Thread(target=produce, daemon=True)
        th.start()
        last_wm_push = time.time()
        try:
            while th.is_alive():
                if self.pump() == 0:
                    time.sleep(0.2)
                # idle-source watermark: keep windows closing even if traffic stops entirely
                if time.time() - last_wm_push > 5:
                    self.features.finish(time.time() - self.cfg["stream"]["out_of_orderness_seconds"] - 5)
                    last_wm_push = time.time()
                    self.bus.truncate_consumed()
        except KeyboardInterrupt:
            pass
        finally:
            stop.set()
            self.pump()
            self.flush()


def make_shop(cfg: dict, faults: FaultSchedule | None = None, seed: int = 42, base_rpm: float = 24.0) -> Shop:
    return Shop(faults=faults or FaultSchedule(), seed=seed, base_rpm=base_rpm, env=cfg.get("env", "prod"))
