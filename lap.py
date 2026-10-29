"""lap -- command-line entry point for the Log Anomaly Platform.

    python lap.py demo                 # lite stack: replay history, train, inject faults, report
    python lap.py bootstrap --days 7   # replay normal history into storage (lite)
    python lap.py train [--promote]    # nightly training job + evaluation gate
    python lap.py live                 # lite stack in real time + fault-injector API on :8090
    python lap.py dashboard            # Streamlit UI on :8501
    python lap.py evaluate             # full M5 evaluation (see evaluation/run_eval.py)
    python lap.py stage <name>         # run one stage against Kafka (full stack / Kubernetes)
    python lap.py simulate             # emit simulated service logs (stdout or Kafka) in real time
    python lap.py rollback | promote   # model lifecycle
    python lap.py drift | tune         # PSI drift report | feedback-driven threshold tuning
"""
from __future__ import annotations

import common.preload  # noqa: F401  (torch before pandas on Windows)

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("GIT_PYTHON_REFRESH", "quiet")

from common.config import load_config  # noqa: E402


def cmd_bootstrap(a):
    from pipeline.lite import LiteStack, make_shop
    from storage.store import make_store

    cfg = load_config()
    store = make_store(cfg)
    end = time.time() - (time.time() % 60)
    start = end - a.days * 86400
    stack = LiteStack(cfg, store, archive_uri="data/archive" if a.archive else None)
    n = stack.simulate(make_shop(cfg, seed=a.seed, base_rpm=a.rpm), start, end, label="bootstrap")
    print(f"bootstrapped {n:,} records ending {datetime.fromtimestamp(end, timezone.utc):%Y-%m-%d %H:%M} UTC")


def cmd_train(a):
    from detection.train import train

    res = train(load_config(), days=a.days, promote=a.promote)
    print(json.dumps({k: v for k, v in res.items() if k != "metrics"}, indent=2))


def cmd_live(a):
    from pipeline.lite import LiteStack, make_shop
    from services.simulator.faults import FaultSchedule
    from storage.store import make_store

    cfg = load_config()
    store = make_store(cfg)
    stack = LiteStack(cfg, store, echo=True, realtime=True)
    stack.enable_detection()
    shop = make_shop(cfg, FaultSchedule(), seed=a.seed, base_rpm=a.rpm)
    print(f"live: fault injector on http://localhost:{a.api_port}/docs, metrics on :{a.metrics_port}; Ctrl+C stops")
    stack.run_realtime(shop, duration_s=a.minutes * 60 if a.minutes else None, api_port=a.api_port,
                       metrics_port=a.metrics_port)


def cmd_demo(a):
    from evaluation.run_eval import run

    run(history_days=a.history_days, eval_days=a.eval_days, per_kind=a.per_kind, seed=a.seed, base_rpm=a.rpm,
        data_dir="data/demo", name=a.name or "demo")
    print("\nOpen the dashboard on the demo data with:  python lap.py dashboard --db data/demo/lap.db")


def cmd_evaluate(a):
    from evaluation.run_eval import main

    main(a.rest)


def cmd_dashboard(a):
    if a.db:
        os.environ["LAP__STORAGE__SQLITE_PATH"] = str(Path(a.db).resolve())
    if a.registry:
        os.environ["LAP__DETECTION__REGISTRY_DIR"] = str(Path(a.registry).resolve())
    elif a.db:
        os.environ["LAP__DETECTION__REGISTRY_DIR"] = str(Path(a.db).resolve().parent / "models")
    if a.fault_api:
        os.environ["LAP_FAULT_API"] = a.fault_api
    subprocess.call([sys.executable, "-m", "streamlit", "run", str(ROOT / "dashboard" / "app.py"),
                     "--server.port", str(a.port), "--server.address", "0.0.0.0", "--server.headless", "true",
                     # streamlit installed with pip --target looks like a source checkout to itself
                     "--global.developmentMode", "false",
                     "--browser.gatherUsageStats", "false"])


def cmd_stage(a):
    from pipeline.run_stage import run_stage

    run_stage(a.name)


def cmd_simulate(a):
    from services.simulator.main import main

    main(a.rest)


def cmd_rollback(_a):
    from detection.registry import Registry

    print("champion is now", Registry(load_config()).rollback())


def cmd_promote(a):
    from detection.registry import Registry
    from detection.train import promote_after_shadow

    if a.after_shadow:  # CronJob: only once the challenger has shadowed for shadow_days
        v = promote_after_shadow(load_config())
        print(f"promoted {v}" if v else "no challenger ready for promotion")
        return
    print("champion is now", Registry(load_config()).promote(a.version))


def cmd_drift(a):
    from detection.drift import main

    main(["--hours", str(a.hours)])


def cmd_tune(a):
    from alerting.tuning import main

    main(["--days", str(a.days)])


def main(argv=None):
    ap = argparse.ArgumentParser(prog="lap", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)

    p = sp.add_parser("bootstrap"); p.add_argument("--days", type=float, default=7)
    p.add_argument("--rpm", type=float, default=24); p.add_argument("--seed", type=int, default=42)
    p.add_argument("--archive", action="store_true"); p.set_defaults(fn=cmd_bootstrap)

    p = sp.add_parser("train"); p.add_argument("--days", type=float, default=None)
    p.add_argument("--promote", action="store_true"); p.set_defaults(fn=cmd_train)

    p = sp.add_parser("live"); p.add_argument("--minutes", type=float, default=None)
    p.add_argument("--rpm", type=float, default=24); p.add_argument("--seed", type=int, default=43)
    p.add_argument("--api-port", type=int, default=8090); p.add_argument("--metrics-port", type=int, default=9108)
    p.set_defaults(fn=cmd_live)

    p = sp.add_parser("demo"); p.add_argument("--history-days", type=float, default=7)
    p.add_argument("--eval-days", type=float, default=2); p.add_argument("--per-kind", type=int, default=2)
    p.add_argument("--rpm", type=float, default=24); p.add_argument("--seed", type=int, default=7)
    p.add_argument("--name", default=None); p.set_defaults(fn=cmd_demo)

    # pass-through commands: their own parsers handle the options (REMAINDER cannot capture leading "--x")
    p = sp.add_parser("evaluate", add_help=False); p.set_defaults(fn=cmd_evaluate, passthrough=True)
    p = sp.add_parser("dashboard"); p.add_argument("--port", type=int, default=8501)
    p.add_argument("--db", default=None, help="SQLite file to browse (e.g. data/demo/lap.db)")
    p.add_argument("--registry", default=None)
    p.add_argument("--fault-api", default=None, help="fault injector the Demo controls call (default :8090)")
    p.set_defaults(fn=cmd_dashboard)
    p = sp.add_parser("stage"); p.add_argument("name", choices=["parse-enrich", "window-features", "anomaly-scorer",
                                                                 "alert-router", "clickhouse-sink", "s3-sink"])
    p.set_defaults(fn=cmd_stage)
    p = sp.add_parser("simulate", add_help=False); p.set_defaults(fn=cmd_simulate, passthrough=True)
    p = sp.add_parser("rollback"); p.set_defaults(fn=cmd_rollback)
    p = sp.add_parser("promote"); p.add_argument("version", nargs="?")
    p.add_argument("--after-shadow", action="store_true"); p.set_defaults(fn=cmd_promote)
    p = sp.add_parser("drift"); p.add_argument("--hours", type=float, default=24); p.set_defaults(fn=cmd_drift)
    p = sp.add_parser("tune"); p.add_argument("--days", type=float, default=7); p.set_defaults(fn=cmd_tune)

    a, rest = ap.parse_known_args(argv)
    if rest and not getattr(a, "passthrough", False):
        ap.error(f"unrecognized arguments: {' '.join(rest)}")
    a.rest = rest
    a.fn(a)


if __name__ == "__main__":
    main()
