import hashlib
import hmac
import json
import time

import pytest

from alerting.feedback import verify_slack
from alerting.notifiers import SlackNotifier
from alerting.router import AlertRouter

T0 = 1_789_000_000.0


class Capture:
    def __init__(self, name):
        self.name, self.sent = name, []

    def send(self, alert, channel=None):
        self.sent.append((alert, channel))
        return "captured"


@pytest.fixture
def router(cfg):
    notifiers = {n: Capture(n) for n in ("dashboard", "slack", "pagerduty", "email")}
    return AlertRouter(cfg, store=None, notifiers=notifiers)


def score(service="payments", ws=T0, s=0.8, anomalous=True, rule=None, tpl="tA", damped=False):
    return {"service": service, "window_start": ws, "window_end": ws + 60, "score": s, "is_anomalous": anomalous,
            "tiers": {"stats": s, "iforest": 0.3, "sequence": None, "rule": rule}, "top_drivers": ["error_ratio z=+7.4"],
            "explanation": {"top_templates": [{"template_id": tpl, "template": "Timeout calling <*>"}],
                            "samples": ["ERROR x"]}, "dominant_template": tpl, "emitted_at": ws + 65,
            "model_version": "v0001", "deploy_damped": damped}


def test_dedup_within_15_minutes_and_new_alert_after(router):
    ev = router.handle(score(ws=T0))
    assert len(ev) == 1 and ev[0]["action"] == "opened"
    for k in range(1, 10):
        assert router.handle(score(ws=T0 + 60 * k)) == []      # same incident -> one alert
    assert len(router.open) == 1
    later = router.handle(score(ws=T0 + 60 * 9 + 16 * 60))      # quiet for > 15 min
    actions = [e["action"] for e in later]
    assert "resolved" in actions and "opened" in actions


def test_grouping_of_calling_services_into_one_incident(router):
    a = router.handle(score(service="payments", ws=T0))[0]
    b = router.handle(score(service="orders", ws=T0 + 60))[0]     # orders calls payments
    c = router.handle(score(service="gateway", ws=T0 + 60 * 4, tpl="tZ"))  # > 2 min later
    assert a["incident_id"] == b["incident_id"]
    assert c[0]["incident_id"] != a["incident_id"]


def test_severity_policy(router):
    assert router.severity(score(service="payments", s=0.95)) == "P1"
    assert router.severity(score(service="payments", s=0.7)) == "P2"
    assert router.severity(score(service="payments", s=0.7, rule={"fired": ["liveness"]})) == "P1"
    assert router.severity(score(service="notifications", s=0.7)) == "P3"      # tier-3, dashboard only
    assert router.severity(score(service="payments", s=0.95, damped=True)) == "P2"  # right after a deploy


def test_routing_channels(router):
    p1 = router.handle(score(s=0.95))[0]
    assert {c.split(":")[0] for c in p1["channels"]} == {"pagerduty", "slack", "email"}
    p3 = router.handle(score(service="notifications", s=0.7))[0]
    assert [c.split(":")[0] for c in p3["channels"]] == ["dashboard"]


def test_hard_rule_on_open_statistical_alert_opens_new_alert_in_same_incident(router):
    first = router.handle(score(ws=T0))[0]
    ev = router.handle(score(ws=T0 + 60, rule={"fired": ["liveness"], "details": []}))
    assert ev and ev[0]["action"] == "opened" and ev[0]["incident_id"] == first["incident_id"]


def test_maintenance_window_suppresses(router):
    router.rules["maintenance_windows"] = [{"service": "payments", "start": T0 - 10, "end": T0 + 3600}]
    assert router.handle(score(ws=T0)) == []


def test_canary_dead_mans_switch(router):
    router.handle({"service": "canary", "canary": True, "window_start": T0, "window_end": T0 + 60,
                   "emitted_at": T0 + 65})
    ev = router.tick(T0 + 65 + 200)
    assert ev and ev[0]["action"] == "pipeline_heartbeat_firing"
    back = router.handle({"service": "canary", "canary": True, "window_start": T0 + 300, "window_end": T0 + 360,
                          "emitted_at": T0 + 365})
    assert back[-1]["action"] == "pipeline_heartbeat_resolved"


def test_slack_payload_has_feedback_buttons(router):
    ev = router.handle(score())[0]
    body = SlackNotifier("http://example.invalid").payload(ev, "#alerts-payments")
    actions = [b for b in body["blocks"] if b["type"] == "actions"][0]["elements"]
    assert [a["action_id"] for a in actions][:2] == ["real", "false_alarm"]
    assert actions[0]["value"] == ev["alert_id"] and body["channel"] == "#alerts-payments"


def test_slack_signature_verification():
    secret, body, ts = "s3cret", b"payload=%7B%7D", str(int(time.time()))
    sig = "v0=" + hmac.new(secret.encode(), f"v0:{ts}:{body.decode()}".encode(), hashlib.sha256).hexdigest()
    assert verify_slack(secret, body, ts, sig)
    assert not verify_slack(secret, body, ts, sig[:-1] + "0")
    assert not verify_slack(secret, body, str(int(time.time()) - 3600), sig)  # replay


def test_feedback_api_records_verdicts(cfg, monkeypatch):
    from fastapi.testclient import TestClient

    import alerting.feedback as fb
    from storage.store import SQLiteStore

    store = SQLiteStore(cfg["storage"]["sqlite_path"])
    monkeypatch.setattr(fb, "_store", store)
    client = TestClient(fb.app)
    assert client.post("/feedback/al-1", json={"verdict": "real", "user": "ana"}).status_code == 200
    assert client.post("/feedback/al-1", json={"verdict": "maybe"}).status_code == 400
    assert store.feedback().iloc[0].verdict == "real"
    assert json.loads(json.dumps(client.get("/healthz").json())) == {"ok": True}


def test_open_alerts_are_resent_to_alertmanager(router):
    am = Capture("alertmanager")
    am.send = lambda alert, resolved=False: am.sent.append((alert["alert_id"], resolved)) or "sent"
    router.alertmanager = am
    router.handle(score(ws=T0, s=0.95))              # P1 -> routed beyond the dashboard
    first = len(am.sent)
    router.tick(T0 + 65 + 30)                        # < 60 s later: no re-send
    assert len(am.sent) == first
    router.tick(T0 + 65 + 61)                        # keeps the alert alive in Alertmanager
    assert len(am.sent) == first + 1 and am.sent[-1][1] is False


def test_failing_channel_does_not_crash_router(router):
    def boom(*a, **k):
        raise RuntimeError("smtp down")
    router.notifiers["email"].send = boom
    ev = router.handle(score(s=0.95))[0]
    assert "email:error:RuntimeError" in ev["channels"]


def test_resolve_to_alertmanager_and_restore_open_alerts_after_restart(cfg):
    from storage.store import SQLiteStore

    store = SQLiteStore(cfg["storage"]["sqlite_path"])
    notifiers = {n: Capture(n) for n in ("dashboard", "slack", "pagerduty", "email")}
    r1 = AlertRouter(cfg, store=store, notifiers=notifiers)
    am = Capture("alertmanager")
    am.send = lambda alert, resolved=False: am.sent.append((alert, resolved)) or "sent"
    r1.alertmanager = am
    first = r1.handle(score(ws=T0))[0]
    r2 = AlertRouter(cfg, store=store, notifiers=notifiers)          # "restart"
    assert first["alert_id"] in r2.open
    assert r2.handle(score(ws=T0 + 60)) == []                         # replayed score: no duplicate
    r1.tick(T0 + 60 * 20)                                             # quiet > 15 min -> resolved
    resolved = [a for a, res in am.sent if res]
    assert resolved and "updated_at" in resolved[0]
