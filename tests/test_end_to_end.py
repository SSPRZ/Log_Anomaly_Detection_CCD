"""Integration test: simulated services -> bus -> every stage -> storage -> alerts (lite stack).

Replays 10 h of normal traffic, trains, then makes notifications silent and
injects a bank-gateway timeout; both must raise explained alerts and nothing
may be lost on the way (every raw record is parsed or dead-lettered).
"""
import json

from detection.train import train
from pipeline.lite import LiteStack, make_shop
from services.simulator.faults import Fault, FaultSchedule
from storage.store import SQLiteStore

START = 1_788_739_200.0 + 8 * 3600  # Monday 08:00 UTC


def test_pipeline_detects_injected_faults(cfg):
    cfg["detection"]["sequence"]["enabled"] = True
    store = SQLiteStore(cfg["storage"]["sqlite_path"])
    hist_end = START + 10 * 3600
    faults = [Fault.make("silent", hist_end + 20 * 60, service="notifications", duration_min=10),
              Fault.make("downstream_timeout", hist_end + 50 * 60, service="payments", duration_min=10)]
    shop = make_shop(cfg, FaultSchedule(faults), seed=3, base_rpm=30)
    stack = LiteStack(cfg, store)
    n_hist = stack.simulate(shop, START, hist_end, progress=False)

    # no loss: everything produced to logs.raw was parsed or dead-lettered
    assert stack.bus.produced["logs.parsed"] + stack.bus.produced.get("logs.dlq", 0) >= n_hist
    assert stack.bus.produced.get("logs.dlq", 0) / n_hist < 0.001

    res = train(cfg, store, end=hist_end, days=10 / 24, promote=True, verbose=False)
    assert res["alias"] == "champion"

    stack.enable_detection()
    stack.simulate(shop, hist_end, hist_end + 75 * 60, progress=False)
    alerts = store.alerts(start=hist_end)
    assert len(alerts), "no alerts raised"

    def hit(service, f):
        return alerts[(alerts.service == service) & (alerts.first_window >= f.start - 60)
                      & (alerts.first_window <= f.end + 300)]

    silent = hit("notifications", faults[0])
    assert len(silent) >= 1
    p = json.loads(silent.iloc[0].payload)
    assert any("liveness" in d or "seconds_since_last" in d or "total_count" in d for d in p["top_drivers"])

    timeout = alerts[alerts.first_window.between(faults[1].start - 60, faults[1].end + 300)
                     & alerts.service.isin(["payments", "orders", "gateway"])]
    assert len(timeout) >= 1
    tp = json.loads(timeout.iloc[0].payload)
    assert tp["top_drivers"] and tp["samples"]  # every alert carries an explanation

    an = store.anomalies(start=hist_end)
    assert an.e2e_latency_s.quantile(0.95) < 60
    # the raw PII never reaches storage
    logs = store.search_logs(limit=100000)
    assert not logs.msg.str.contains(r"@example\.com|\d{4} \d{4} \d{4} \d{4}", regex=True).any()


def test_bad_deploy_injected_mid_minute_is_announced():
    """Real-time injection lands after the current minute was generated; it must still emit a deploy event."""
    from services.simulator.workload import Shop

    events = []
    faults = FaultSchedule()
    shop = Shop(faults, seed=1, benign_deploys=False, on_deploy=events.append)
    t0 = START
    shop.minute(t0)                                       # minute already generated...
    faults.add(Fault.make("bad_deploy", t0 + 30, service="orders", duration_min=5))  # ...then injected
    shop.minute(t0 + 60)
    assert [e["service"] for e in events] == ["orders"] and events[0]["ts"] == t0 + 60
