"""Data validation for the training set (Section 5.3 "Data validation (pandera)").

    python evaluation/validate_features.py            # validate features in the configured store
    python evaluation/validate_features.py --synthetic  # validate a freshly simulated hour (CI)

Uses pandera when installed; otherwise the same checks run as plain pandas
assertions so the gate never silently disappears.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import common.preload  # noqa: E402,F401
import pandas as pd  # noqa: E402

from common.config import load_config  # noqa: E402

CHECKS = {
    "total_count": lambda s: (s >= 0).all(),
    "distinct_hosts": lambda s: (s >= 0).all(),
    "error_count": lambda s: (s >= 0).all(),
    "error_ratio": lambda s: s.between(0, 1).all(),
    "ratio_5xx": lambda s: s.between(0, 1).all(),
    "template_entropy": lambda s: (s >= 0).all(),
    "seconds_since_last": lambda s: (s >= 0).all(),
    "p95_latency_ms": lambda s: (s.dropna() >= 0).all(),
}


def validate(df: pd.DataFrame) -> list[str]:
    errors = []
    try:
        import pandera as pa

        schema = pa.DataFrameSchema({
            "service": pa.Column(str, nullable=False),
            "window_start": pa.Column(float, pa.Check.ge(0)),
            "total_count": pa.Column(float, pa.Check.ge(0)),
            "error_ratio": pa.Column(float, pa.Check.in_range(0, 1)),
            "ratio_5xx": pa.Column(float, pa.Check.in_range(0, 1)),
            "distinct_hosts": pa.Column(float, pa.Check.ge(0)),
            "p95_latency_ms": pa.Column(float, pa.Check.ge(0), nullable=True),
        }, coerce=True)
        schema.validate(df, lazy=True)
    except ImportError:
        pass
    except Exception as e:  # pandera SchemaErrors
        errors.append(str(e)[:2000])
    for col, fn in CHECKS.items():
        if col in df and not fn(df[col].astype(float)):
            errors.append(f"check failed: {col}")
    if df.duplicated(["service", "window_start"]).any():
        errors.append("duplicate (service, window_start) rows")
    return errors


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--synthetic", action="store_true")
    a = ap.parse_args(argv)
    cfg = load_config()
    if a.synthetic:
        from pipeline.lite import LiteStack, make_shop
        from storage.store import SQLiteStore

        store = SQLiteStore(":memory:")
        LiteStack(cfg, store).simulate(make_shop(cfg), 1_788_739_200.0, 1_788_739_200.0 + 3600, progress=False)
    else:
        from storage.store import make_store

        store = make_store(cfg)
    df = store.features()
    errs = validate(df)
    print(f"validated {len(df)} feature rows: {'ok' if not errs else 'FAILED'}")
    for e in errs:
        print("  -", e)
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
