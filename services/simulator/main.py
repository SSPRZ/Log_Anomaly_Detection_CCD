"""Simulated services in real time.

    python lap.py simulate --sink stdout            # JSON lines on stdout (Fluent Bit tails them)
    python lap.py simulate --sink kafka             # produce straight to logs.raw (no shipper)
    python lap.py simulate --services payments      # only emit one service's logs (one container each)

Also exposes the fault-injector API (default :8090) and POST /checkout so
Locust can drive traffic (chaos/locust/locustfile.py); with --self-drive the
built-in seasonal traffic model is used instead.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time

from common.config import load_config
from services.simulator.faults import FaultSchedule
from services.simulator.workload import EventStream, Shop


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--sink", choices=["stdout", "kafka"], default="stdout")
    ap.add_argument("--services", default="", help="comma-separated subset to emit (default: all)")
    ap.add_argument("--rpm", type=float, default=24.0)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--api-port", type=int, default=8090)
    ap.add_argument("--no-canary", action="store_true")
    a = ap.parse_args(argv)
    cfg = load_config()
    only = {s for s in a.services.split(",") if s}
    faults = FaultSchedule()
    bus = None
    if a.sink == "kafka" or cfg["bus"]["backend"] == "kafka":
        # logs go to stdout for the shipper, but deploy events (CI's job in production) go straight to Kafka
        from common.bus import make_bus

        bus = make_bus(cfg)
    topic = cfg["bus"]["topics"]["raw"]

    def on_deploy(ev):
        if bus is not None:
            bus.produce(cfg["bus"]["topics"]["deploys"], ev, key=ev["service"])
        else:
            sys.stderr.write(json.dumps({"deploy": ev}) + "\n")

    shop = Shop(faults, seed=a.seed, base_rpm=a.rpm, env=cfg.get("env", "prod"), on_deploy=on_deploy)
    if a.api_port:
        _serve_api(shop, faults, a.api_port)
    stream = EventStream(shop, 0 if a.no_canary else cfg["observability"]["canary_interval_seconds"])
    out = sys.stdout
    t = time.time()
    t -= t % 60
    while True:
        for arrival, rec in stream.generate(t, t + 60):
            if only and rec["service"] not in only and rec["service"] != "canary":
                continue
            delay = arrival - time.time()
            if delay > 0:
                time.sleep(delay)
            if a.sink == "kafka":
                bus.produce(topic, rec, key=rec["service"])
            else:
                out.write(json.dumps(rec, separators=(",", ":")) + "\n")
                out.flush()
        t += 60


def _serve_api(shop: Shop, faults: FaultSchedule, port: int) -> None:
    import uvicorn
    from fastapi import FastAPI

    from chaos.fault_injector import build_app

    app: FastAPI = build_app(faults)
    extra: list[dict] = []
    lock = threading.Lock()

    @app.post("/checkout")
    def checkout():
        """One externally driven checkout (Locust)."""
        with lock:
            shop._checkout(extra, time.time())
            recs, extra[:] = list(extra), []
        for r in recs:
            sys.stdout.write(json.dumps(r, separators=(",", ":")) + "\n")
        sys.stdout.flush()
        return {"records": len(recs)}

    server = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()


if __name__ == "__main__":
    main()
