import json

from common.timeutil import parse_ts
from stream.parse_enrich import ParseEnrich, parse_plain, unwrap


def test_valid_json_record_is_parsed_redacted_and_templated(cfg):
    pe = ParseEnrich(cfg)
    rec = {"ts": "2026-10-09T05:41:12.318Z", "service": "payments", "env": "prod", "level": "error",
           "host": "payments-7d9f-x2k", "trace_id": "4bf92f3577b34da6",
           "msg": "Charging card 4111 1111 1111 1111 for bob@example.com",
           "attrs": {"endpoint": "/v1/charge", "latency_ms": 3012, "status": 504}}
    out, dlq = pe.process(rec)
    assert dlq is None
    assert out["ts"] == parse_ts("2026-10-09T05:41:12.318Z")
    assert out["level"] == "ERROR"
    assert "<CARD>" in out["msg"] and "<EMAIL>" in out["msg"] and "4111" not in out["msg"]
    assert out["template_id"].startswith("t") and out["template_created"]
    assert out["attrs"]["latency_ms"] == 3012
    assert pe.take_dirty_templates()[0]["namespace"] == "payments"


def test_missing_fields_go_to_dlq(cfg):
    out, dlq = ParseEnrich(cfg).process({"ts": "2026-10-09T05:41:12Z", "level": "INFO", "msg": "x"})
    assert out is None and dlq["reason"].startswith("missing:") and "service" in dlq["reason"]


def test_bad_timestamp_and_level_go_to_dlq(cfg):
    pe = ParseEnrich(cfg)
    _, d1 = pe.process({"ts": "yesterday", "service": "a", "env": "e", "level": "INFO", "msg": "x"})
    _, d2 = pe.process({"ts": 1789000000, "service": "a", "env": "e", "level": "LOUD", "msg": "x"})
    assert d1["reason"] == "bad_ts" and d2["reason"].startswith("bad_level")


def test_oversized_record_goes_to_dlq(cfg):
    cfg["ingest"]["max_message_bytes"] = 200
    _, dlq = ParseEnrich(cfg).process({"ts": 1, "service": "a", "env": "e", "level": "INFO", "msg": "x" * 500})
    assert dlq["reason"].startswith("oversized")


def test_legacy_plain_text_profiles():
    a = parse_plain("2026-10-09 05:41:12,318 ERROR [payments] Timeout calling bank-gateway after 3000 ms")
    assert a["service"] == "payments" and a["level"] == "ERROR" and a["ts"] == "2026-10-09 05:41:12.318"
    b = parse_plain("[2026-10-09T05:41:12Z] [warn] orders: Slow query on table orders took 900 ms")
    assert b["service"] == "orders" and b["level"] == "warn"
    c = parse_plain('level=error ts=2026-10-09T05:41:12Z service=inventory msg="Reservation failed"')
    assert c["service"] == "inventory" and c["msg"] == "Reservation failed"
    assert parse_plain("just some text") is None


def test_fluent_bit_kubernetes_wrapper_is_unwrapped(cfg):
    line = json.dumps({"ts": "2026-10-09T05:41:12Z", "level": "INFO", "msg": "hi", "env": "prod"})
    raw = {"log": line, "kubernetes": {"pod_name": "orders-abc", "namespace_name": "shop",
                                       "labels": {"app.kubernetes.io/name": "orders"}}}
    u = unwrap(raw)
    assert u["service"] == "orders" and u["host"] == "orders-abc"
    legacy = {"log": "2026-10-09 05:41:12 WARN [inventory] low stock", "kubernetes": {"labels": {"app": "x"}}}
    out, dlq = ParseEnrich(cfg).process(legacy)
    assert dlq is None and out["service"] == "inventory" and out["level"] == "WARN"
