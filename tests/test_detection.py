import numpy as np
import pandas as pd
import pytest

from detection.baseline import RobustBaseline
from detection.ensemble import Debouncer, Ensemble
from detection.iforest import GroupForest
from detection.rules import Rules
from detection.sequence import MarkovModel, SequenceDetector

T0 = 1_788_739_200.0  # Monday 00:00 UTC


def history(service="payments", days=2, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for m in range(int(days * 1440)):
        ts = T0 + m * 60
        hod = (m % 1440) / 60
        vol = 60 + 30 * np.sin(2 * np.pi * (hod - 10) / 24)
        n = rng.poisson(vol)
        err = rng.binomial(n, 0.01)
        rows.append({"service": service, "window_start": ts, "window_end": ts + 60, "total_count": n,
                     "count_delta_week": None, "distinct_hosts": 2, "error_count": err, "warn_count": rng.poisson(1),
                     "error_ratio": err / max(n, 1), "unique_templates": 6 + rng.integers(0, 2), "new_templates": 0,
                     "template_entropy": 2.0 + rng.normal(0, 0.05), "p50_latency_ms": 100 + rng.normal(0, 5),
                     "p95_latency_ms": 180 + rng.normal(0, 10), "p99_latency_ms": 240 + rng.normal(0, 15),
                     "ratio_5xx": 0.0, "seconds_since_last": rng.uniform(0, 5), "fatal": 0,
                     "template_hist": list(rng.multinomial(max(n, 1), [1 / 64] * 64))})
    return pd.DataFrame(rows)


@pytest.fixture(scope="module")
def fitted():
    from common.config import load_config
    cfg = load_config()
    df = history(days=3)
    return cfg, df, RobustBaseline(cfg).fit(df)


def test_normal_windows_score_low_and_spikes_high(fitted):
    cfg, df, b = fitted
    normal = df.iloc[-300:].to_dict("records")
    scores = [RobustBaseline.score(b.zscores(r, use_trend=False)) for r in normal]
    assert np.mean(np.array(scores) > 0.5) < 0.02
    spike = dict(normal[-1], error_count=40, error_ratio=0.4, ratio_5xx=0.3)
    z = b.zscores(spike, use_trend=False)
    assert z["error_ratio"] > 6 and RobustBaseline.score(z) > 0.9


def test_direction_rules_ignore_harmless_deviations(fitted):
    _, df, b = fitted
    r = df.iloc[-1].to_dict()
    better = dict(r, p95_latency_ms=10.0, error_ratio=0.0)  # faster and cleaner than usual is fine
    z = b.zscores(better, use_trend=False)
    assert z["p95_latency_ms"] == 0 and z["error_ratio"] == 0
    dropped = dict(r, total_count=0, distinct_hosts=0)
    z2 = b.zscores(dropped, use_trend=False)
    assert z2["total_count"] < -3 and z2["distinct_hosts"] < -3


def test_trend_detector_is_quiet_on_normal_data_and_fires_on_creep(fitted):
    _, df, b = fitted
    b.ewma = {}
    recs = df.iloc[-600:].to_dict("records")
    normal = [b.zscores(r)["p50_latency_ms_trend"] for r in recs[:300]]
    assert np.percentile(normal, 99) < 3.5
    creep = []
    for i, r in enumerate(recs[300:]):  # latency creeps up ~0.15 ms per minute ... then more
        creep.append(b.zscores(dict(r, p50_latency_ms=r["p50_latency_ms"] + 0.2 * i))["p50_latency_ms_trend"])
    assert max(creep) > 5


def test_baseline_state_roundtrip(fitted):
    cfg, df, b = fitted
    r = df.iloc[-10].to_dict()
    b2 = RobustBaseline(cfg).load_state(b.state())
    assert b2.zscores(r, use_trend=False) == b.zscores(r, use_trend=False)


def test_iforest_flags_unusual_combinations_and_onnx_matches(tmp_path, fitted):
    cfg, df, _ = fitted
    f = GroupForest("core", cfg["detection"]["features"], n_estimators=100).fit(df)
    normal = f.score_df(df.iloc[-500:])
    assert np.mean(normal > 0.5) < 0.05
    # every feature individually plausible (~2.5 sigma), jointly very rare
    odd = df.iloc[-1].to_dict()
    odd.update(p50_latency_ms=113.0, p95_latency_ms=205.0, p99_latency_ms=278.0, template_entropy=1.87,
               unique_templates=8, warn_count=4)
    assert f.score_one(odd) > 0.5 > f.score_one(df.iloc[-2].to_dict())
    f.save(tmp_path / "f")
    g = GroupForest.load(tmp_path / "f")
    assert abs(g.score_one(odd) - f.score_one(odd)) < 1e-3
    assert g.backend in ("onnx", "sklearn")


def test_ensemble_reweights_missing_tiers_and_keeps_strong_single_signal():
    e = Ensemble({"stats": 0.5, "iforest": 0.3, "sequence": 0.2}, 0.55)
    assert e.combine({"stats": 0.6, "iforest": None, "sequence": None}) == pytest.approx(0.6)
    assert e.combine({"stats": 0.2, "iforest": 0.2, "sequence": 0.2}) == pytest.approx(0.2)
    assert e.combine({"stats": 0.99, "iforest": 0.0, "sequence": 0.0}) >= 0.84


def test_debounce_two_of_three():
    d = Debouncer(3, 2)
    assert [d.update("s", x) for x in (True, False, True, True, False, False, True)] == \
           [False, False, True, True, False, False, False]


def test_rules(cfg):
    r = Rules(cfg)
    base = {"service_age_s": 86400, "total_count": 10, "seconds_since_last": 1, "new_error_templates": []}
    assert r.evaluate(base) is None
    hit = r.evaluate(dict(base, new_error_templates=[{"template_id": "t", "template": "Boom", "count": 3}]))
    assert hit["fired"] == ["new_error_template"]
    assert r.evaluate(dict(base, total_count=0, seconds_since_last=120))["fired"] == ["liveness"]
    assert r.evaluate(dict(base, service_age_s=60, total_count=0, seconds_since_last=120)) is None  # warm-up


def _trace(i, skip=False):
    steps = [("gateway", "recv"), ("orders", "create"), ("inventory", "reserve"), ("payments", "charge"),
             ("payments", "authorized"), ("orders", "confirmed"), ("gateway", "done")]
    if skip:
        steps = [s for s in steps if s[0] != "payments"]
    return {"trace_id": f"t{i}", "start": T0 + i,
            "events": [{"ts": T0 + i + k * 0.01, "service": s, "template_id": t} for k, (s, t) in enumerate(steps)]}


@pytest.mark.parametrize("backend", ["markov", "torch"])
def test_sequence_model_detects_skipped_step_and_blames_expected_service(backend):
    if backend == "torch":
        pytest.importorskip("torch")
    det = SequenceDetector(h=4, top_k=2, backend=backend, epochs=3).fit([_trace(i) for i in range(300)])
    assert det.attribute(_trace(1)) is None
    blamed = det.attribute(_trace(2, skip=True))
    assert blamed is not None and blamed["service"] == "payments" and blamed["observed_service"] == "orders"
    # a trace whose entry step is missing is blamed on the service that should have started it
    headless = _trace(3)
    headless["events"] = headless["events"][1:]
    assert det.attribute(headless)["service"] == "gateway"
    assert SequenceDetector.window_score(20, 8, 0.01) > 0.9
    assert SequenceDetector.window_score(20, 0, 0.01) == 0.0


def test_markov_state_roundtrip():
    m = MarkovModel(2, 1).fit([["a", "b", "c"]] * 3)
    m2 = MarkovModel.from_state(m.state())
    assert m2.topk(["a", "b"]) == ["c"] == m.topk(["a", "b"])


def test_trend_detector_recovers_quickly_after_an_acute_incident(fitted):
    _, df, b = fitted
    b.ewma = {}
    recs = df.iloc[-400:].to_dict("records")
    for r in recs[:100]:
        b.zscores(r)
    for r in recs[100:110]:  # 10-minute acute error burst (T1 territory)
        b.zscores(dict(r, error_count=40, error_ratio=0.35))
    after = [b.zscores(r)["error_ratio_trend"] for r in recs[110:115]]
    assert max(after) < 3  # no 20-minute tail that would keep the alert open
