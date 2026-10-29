import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import common.preload  # noqa: E402,F401  (torch before pandas on Windows)
import pytest  # noqa: E402

from common.config import deep_merge, load_config  # noqa: E402


@pytest.fixture
def cfg(tmp_path):
    return load_config(overrides={
        "storage": {"backend": "sqlite", "sqlite_path": str(tmp_path / "lap.db")},
        "detection": {"registry_dir": str(tmp_path / "models"), "mlflow": {"enabled": False},
                      "iforest": {"n_estimators": 25}, "sequence": {"backend": "markov"}},
        "bus": {"backend": "local"},
    })


@pytest.fixture
def make_cfg(tmp_path):
    def _make(**overrides):
        base = {
            "storage": {"backend": "sqlite", "sqlite_path": str(tmp_path / "lap.db")},
            "detection": {"registry_dir": str(tmp_path / "models"), "mlflow": {"enabled": False}},
        }
        return load_config(overrides=deep_merge(base, overrides))
    return _make


def parsed(service="payments", ts=1_789_000_000.0, level="INFO", msg="hello", template_id="t1", host="h1",
           trace_id="", **attrs):
    return {"ts": ts, "service": service, "env": "test", "level": level, "host": host, "trace_id": trace_id,
            "msg": msg, "attrs": attrs, "template_id": template_id, "template": msg, "template_created": False}
