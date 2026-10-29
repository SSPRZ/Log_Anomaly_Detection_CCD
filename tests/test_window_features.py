from stream.window_features import Sessionizer, WindowFeatures
from tests.conftest import parsed

T0 = 1_789_000_020.0 - (1_789_000_020.0 % 60)  # minute aligned


def feed(wf, recs):
    out = []
    for r in recs:
        out.extend(wf.add(r))
    return out


def test_window_fires_after_watermark_and_computes_features(cfg):
    wf = WindowFeatures(cfg)
    recs = [parsed(ts=T0 + i, level="ERROR" if i % 4 == 0 else "INFO", template_id=f"t{i % 3}",
                   host=f"h{i % 2}", latency_ms=100 + i, status=500 if i % 4 == 0 else 200) for i in range(40)]
    assert feed(wf, recs) == []                     # window still open
    out = feed(wf, [parsed(ts=T0 + 66)])            # watermark = 66 - 5 > 60
    assert len(out) == 1
    f = out[0]
    assert f["window_start"] == T0 and f["total_count"] == 40
    assert f["error_count"] == 10 and abs(f["error_ratio"] - 0.25) < 1e-9
    assert f["distinct_hosts"] == 2 and f["unique_templates"] == 3
    assert f["p50_latency_ms"] == 119.5 and abs(f["ratio_5xx"] - 0.25) < 1e-9
    assert sum(f["template_hist"]) == 40 and len(f["template_hist"]) == 64
    assert f["samples"][0].startswith("ERROR")      # most severe lines first


def test_late_record_within_lateness_emits_revision_beyond_goes_to_dlq(cfg):
    wf = WindowFeatures(cfg)
    feed(wf, [parsed(ts=T0 + 1), parsed(ts=T0 + 70)])
    rev = feed(wf, [parsed(ts=T0 + 30)])            # 40 s late, inside 30 s allowed lateness of window end
    assert len(rev) == 1 and rev[0]["revision"] == 1 and rev[0]["total_count"] == 2
    feed(wf, [parsed(ts=T0 + 200)])
    late = feed(wf, [parsed(ts=T0 + 5)])
    assert late[0].get("_late") and wf.late_dropped == 1


def test_silent_service_still_gets_windows_with_growing_silence(cfg):
    wf = WindowFeatures(cfg)
    feed(wf, [parsed(service="quiet", ts=T0 + 10), parsed(service="busy", ts=T0 + 11)])
    out = []
    for k in range(1, 5):  # only "busy" keeps logging
        out += feed(wf, [parsed(service="busy", ts=T0 + 60 * k + s) for s in range(0, 60, 5)])
    quiet = [f for f in out if f["service"] == "quiet"]
    assert [f["total_count"] for f in quiet][:1] == [1]
    assert all(f["total_count"] == 0 for f in quiet[1:])
    gaps = [f["seconds_since_last"] for f in quiet[1:]]
    assert gaps == sorted(gaps) and gaps[-1] >= 150


def test_watermark_is_min_over_active_sources(cfg):
    wf = WindowFeatures(cfg)
    feed(wf, [parsed(service="b", ts=T0 + 95), parsed(service="a", ts=T0 + 100)])
    assert wf.watermark == T0 + 95 - 5             # held back by the slower source
    feed(wf, [parsed(service="b", ts=T0 + 90)])     # out-of-order record never moves it backwards
    assert wf.watermark == T0 + 95 - 5
    # a source that falls idle (> idle_timeout behind the newest) stops holding the watermark back
    feed(wf, [parsed(service="a", ts=T0 + 100 + wf.idle_timeout + 10)])
    assert wf.watermark == T0 + 100 + wf.idle_timeout + 10 - 5


def test_new_error_templates_and_week_delta(cfg):
    wf = WindowFeatures(cfg)
    feed(wf, [parsed(ts=T0 + i, template_id="known") for i in range(10)])
    recs = [parsed(ts=T0 + 60 + i, level="ERROR", template_id="boom", msg="Boom") for i in range(4)]
    out = feed(wf, recs + [parsed(ts=T0 + 200)])
    f = [x for x in out if x["window_start"] == T0 + 60][0]
    assert f["new_templates"] == 1
    assert f["new_error_templates"] == [{"template_id": "boom", "template": "Boom", "count": 4}]
    wk = feed(wf, [parsed(ts=T0 + 7 * 86400 + 1), parsed(ts=T0 + 7 * 86400 + 80)])
    first = [x for x in wk if x["window_start"] == T0 + 7 * 86400][0]
    assert first["count_delta_week"] == 1 - 10


def test_sessionizer_closes_traces_after_gap(cfg):
    s = Sessionizer(cfg)
    s.add(parsed(ts=T0, trace_id="abc", service="gateway", template_id="g1"))
    s.add(parsed(ts=T0 + 0.1, trace_id="abc", service="orders", template_id="o1"))
    assert s.flush(T0 + 10) == []
    out = s.flush(T0 + 31)
    assert len(out) == 1 and [e["template_id"] for e in out[0]["events"]] == ["g1", "o1"]
    assert out[0]["entry_service"] == "gateway"
