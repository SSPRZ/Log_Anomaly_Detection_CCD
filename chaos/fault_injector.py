"""Fault-injector API (Section 9.1). Every injected fault is a labelled incident.

    GET  /faults                       list (active and past) faults
    POST /faults                       {"kind": "downstream_timeout", "service": "payments", "duration_min": 10}
    POST /faults/{id}/stop             end a fault now
    GET  /labels                       ground truth for evaluation (JSON lines friendly)
"""
from __future__ import annotations

import threading
import time

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from services.simulator.faults import FAULT_KINDS, Fault, FaultSchedule


class FaultRequest(BaseModel):
    kind: str
    service: str | None = None
    duration_min: float | None = None
    params: dict = {}


def build_app(schedule: FaultSchedule) -> FastAPI:
    app = FastAPI(title="LAP fault injector")

    @app.get("/faults")
    def list_faults():
        now = time.time()
        return [{**f.to_dict(), "active": f.active(now)} for f in schedule.all()]

    @app.post("/faults")
    def inject(req: FaultRequest):
        if req.kind not in FAULT_KINDS:
            raise HTTPException(400, f"kind must be one of {FAULT_KINDS}")
        f = schedule.add(Fault.make(req.kind, time.time(), req.service, req.duration_min, **req.params))
        return f.to_dict()

    @app.post("/faults/{fault_id}/stop")
    def stop(fault_id: str):
        if not schedule.stop(fault_id, time.time()):
            raise HTTPException(404, "no such active fault")
        return {"ok": True}

    @app.get("/labels")
    def labels():
        return [{"id": f.id, "kind": f.kind, "service": f.service, "start": f.start, "end": f.end}
                for f in schedule.all()]

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    return app


def serve_in_thread(schedule: FaultSchedule, port: int = 8090) -> threading.Thread:
    import uvicorn

    server = uvicorn.Server(uvicorn.Config(build_app(schedule), host="0.0.0.0", port=port, log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    return th
