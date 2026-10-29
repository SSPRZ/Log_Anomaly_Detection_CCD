"""Feedback endpoints: Slack interactive buttons and a plain HTTP API.

    POST /slack/actions        Slack interactivity request URL (signature verified)
    POST /feedback/{alert_id}  {"verdict": "real" | "false_alarm", "user": "...", "comment": "..."}
    GET  /alerts               recent alerts as JSON
    GET  /healthz

Run: uvicorn alerting.feedback:app --port 8088
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from common.config import get_config
from storage.store import make_store

app = FastAPI(title="LAP feedback")
_store = None


def store():
    global _store
    if _store is None:
        _store = make_store(get_config())
    return _store


class Verdict(BaseModel):
    verdict: str
    user: str = ""
    comment: str = ""


def verify_slack(secret: str, body: bytes, ts: str, sig: str, now: float | None = None) -> bool:
    if not secret or not ts or not sig:
        return False
    if abs((now or time.time()) - int(ts)) > 300:  # replay protection
        return False
    base = f"v0:{ts}:{body.decode()}".encode()
    expected = "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/alerts")
def alerts(limit: int = 50):
    return json.loads(store().alerts(limit=limit).to_json(orient="records"))


@app.post("/feedback/{alert_id}")
def feedback(alert_id: str, v: Verdict):
    if v.verdict not in ("real", "false_alarm"):
        raise HTTPException(400, "verdict must be real or false_alarm")
    store().add_feedback(alert_id, v.verdict, v.user, v.comment)
    return {"ok": True}


@app.post("/slack/actions")
async def slack_actions(request: Request):
    body = await request.body()
    secret = os.environ.get("SLACK_SIGNING_SECRET", "")
    if not verify_slack(secret, body, request.headers.get("X-Slack-Request-Timestamp", ""),
                        request.headers.get("X-Slack-Signature", "")):
        raise HTTPException(401, "bad signature")
    payload = json.loads(parse_qs(body.decode())["payload"][0])
    user = payload.get("user", {}).get("username", "")
    for act in payload.get("actions", []):
        if act.get("action_id") in ("real", "false_alarm"):
            store().add_feedback(act["value"], act["action_id"], user, "via slack")
    return {"text": "Thanks -- feedback recorded."}
