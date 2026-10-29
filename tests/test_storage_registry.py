import pandas as pd
import pytest

from detection.baseline import RobustBaseline
from detection.registry import Bundle, Registry
from storage.store import SQLiteStore
from tests.conftest import parsed


def test_sqlite_store_roundtrip_and_ttl(tmp_path):
    s = SQLiteStore(tmp_path / "x.db", {"logs": 14, "features_1m": 90, "anomalies": 365})
    now = 1_789_000_000.0
    s.insert_logs([parsed(ts=now - 20 * 86400, msg="old"), parsed(ts=now, msg="Timeout calling bank")])
    s.upsert_templates([{"template_id": "t1", "namespace": "payments", "pattern": "a <*>", "size": 2,
                         "first_seen": 1.0, "last_seen": 5.0}])
    s.upsert_templates([{"template_id": "t1", "namespace": "payments", "pattern": "a <*>", "size": 9,
                         "first_seen": 1.0, "last_seen": 3.0}])
    t = s.templates().iloc[0]
    assert t["size"] == 9 and t.last_seen == 5.0  # last_seen never goes backwards
    fv = {"service": "payments", "window_start": now, "window_end": now + 60, "total_count": 5,
          "template_hist": [1] * 64, "template_counts": {"t1": 5}, "top_templates": []}
    s.upsert_features([fv, dict(fv, total_count=7, revision=1)])  # revision replaces
    f = s.features(with_payload=True)
    assert len(f) == 1 and f.total_count.iloc[0] == 7 and f.template_counts.iloc[0] == {"t1": 5}
    assert len(s.search_logs(text="Timeout")) == 1
    s.purge_ttl(now)
    assert len(s.search_logs()) == 1
    with pytest.raises(ValueError):
        s.add_feedback("a", "maybe")


def test_registry_champion_challenger_rollback(cfg):
    reg = Registry(cfg)
    base = RobustBaseline(cfg)

    def bundle(v):
        return Bundle(v, base, {}, None, {"stats": 1.0}, 0.5, {"train_start": 0, "train_end": 1})

    reg.register(bundle("v0001"), {"f1": 0.8}, "champion")
    reg.register(bundle("v0002"), {"f1": 0.85}, "challenger")
    idx = reg.index()
    assert idx["champion"] == "v0001" and idx["challenger"] == "v0002"
    assert reg.next_version() == "v0003"
    assert reg.promote() == "v0002" and reg.index()["challenger"] is None
    assert reg.rollback() == "v0001"
    assert reg.load("champion").version == "v0001"


def test_training_gate_end_to_end_on_synthetic_features(cfg):
    from detection.train import train
    from tests.test_detection import history

    store = SQLiteStore(cfg["storage"]["sqlite_path"])
    df = pd.concat([history("payments", 2, 1), history("orders", 2, 2)])
    rows = df.to_dict("records")
    for r in rows:
        r["template_counts"], r["top_templates"] = {}, []
    store.upsert_features(rows)
    res = train(cfg, store, days=2, promote=True, verbose=False)
    m = res["metrics"]
    assert res["alias"] == "champion"
    assert m["f1"] > 0.7 and m["alerts_per_service_day"] <= 5
    assert Registry(cfg).load("champion").forests  # T2 trained for the core group
