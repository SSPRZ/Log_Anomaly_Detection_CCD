"""End-to-end evaluation (milestones M4/M5).

1. Replay ``history_days`` of normal traffic (with benign deploys) through the
   whole pipeline into storage.
2. Run the nightly training job and promote the result.
3. Replay ``eval_days`` with a labelled fault suite (every kind from the
   Section 9.1 table), detection enabled.
4. Score incident-level precision / recall / F1, detection delay, alert hygiene
   and pipeline latency; compare with the static "50 errors in 5 minutes" rule
   on the same incidents. Writes evaluation/reports/<name>.{json,md}.
"""
from __future__ import annotations

import common.preload  # noqa: F401  (torch before pandas on Windows)

import argparse
import hashlib
import json
import pickle
import shutil
import time
from datetime import datetime, timezone

import numpy as np

from common.config import deep_merge, load_config, resolve_path
from detection.train import train
from evaluation.metrics import evaluate, static_threshold_alerts
from pipeline.lite import LiteStack, make_shop
from services.simulator.faults import FaultSchedule, random_suite
from storage.store import SQLiteStore

DEFAULT_START = datetime(2026, 9, 7, tzinfo=timezone.utc).timestamp()  # a Monday 00:00 UTC


def _code_hash() -> str:
    """Fingerprint of everything that shapes the replayed history."""
    h = hashlib.sha1()
    root = resolve_path(".")
    for p in sorted([*root.glob("services/simulator/*.py"), *root.glob("stream/*.py"), *root.glob("common/*.py"),
                     root / "config" / "topology.yaml"]):
        h.update(p.read_bytes())
    cfg = load_config()  # only the sections that affect stream processing, not detection tuning
    h.update(json.dumps({k: cfg[k] for k in ("stream", "ingest", "observability")}, sort_keys=True).encode())
    return h.hexdigest()[:8]


def run(history_days: float = 7, eval_days: float = 2, per_kind: int = 2, seed: int = 7, base_rpm: float = 24,
        data_dir: str = "data/eval", name: str | None = None, start: float = DEFAULT_START,
        cfg_overrides: dict | None = None, keep: bool = True, use_cache: bool = True) -> dict:
    t_wall = time.time()
    data = resolve_path(data_dir)
    if data.exists():
        shutil.rmtree(data)
    data.mkdir(parents=True)
    cfg = load_config(overrides=deep_merge({
        "storage": {"backend": "sqlite", "sqlite_path": str(data / "lap.db")},
        "detection": {"registry_dir": str(data / "models"),
                      "mlflow": {"tracking_uri": (data / "mlruns").as_uri()}},
        "bus": {"backend": "local"},
    }, cfg_overrides or {}))
    hist_end = start + history_days * 86400
    eval_end = hist_end + eval_days * 86400
    faults = random_suite(hist_end, eval_days * 24, per_kind=per_kind, seed=seed)
    schedule = FaultSchedule(faults)

    # The normal-traffic history depends only on the simulator/stream code and these parameters,
    # so it is cached (DB + stream-job state) to make model iterations cheap.
    cache = resolve_path("data/cache") / f"history-{history_days:g}d-s{seed}-r{base_rpm:g}-{int(start)}-{_code_hash()}"
    if use_cache and (cache / "state.pkl").exists():
        print(f"[eval] 1/4 reusing cached history {cache.name}")
        shutil.copy(cache / "lap.db", data / "lap.db")
        store = SQLiteStore(cfg["storage"]["sqlite_path"], cfg["storage"]["retention_days"])
        stack = LiteStack(cfg, store)
        with open(cache / "state.pkl", "rb") as fh:
            st = pickle.load(fh)
        stack.parse.job, stack.features.job, stack.features.sessions, shop = (
            st["parse"], st["features"], st["sessions"], st["shop"])
        shop.faults = schedule
        n_hist = st["n"]
    else:
        store = SQLiteStore(cfg["storage"]["sqlite_path"], cfg["storage"]["retention_days"])
        shop = make_shop(cfg, schedule, seed=seed, base_rpm=base_rpm)
        stack = LiteStack(cfg, store)
        print(f"[eval] 1/4 replaying {history_days:g} days of history")
        n_hist = stack.simulate(shop, start, hist_end, label="history")
        if use_cache:
            cache.mkdir(parents=True, exist_ok=True)
            store._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            shutil.copy(data / "lap.db", cache / "lap.db")
            shop.faults, shop.on_deploy = None, None  # locks / callbacks are not picklable
            with open(cache / "state.pkl", "wb") as fh:
                pickle.dump({"parse": stack.parse.job, "features": stack.features.job,
                             "sessions": stack.features.sessions, "shop": shop, "n": n_hist}, fh)
            shop.faults = schedule
    print("[eval] 2/4 training")
    tr = train(cfg, store, end=hist_end, days=history_days, promote=True, seed=seed)
    print(f"[eval] 3/4 replaying {eval_days:g} days with {len(faults)} injected incidents")
    stack.enable_detection()
    n_eval = stack.simulate(shop, hist_end, eval_end, label="evaluation")

    print("[eval] 4/4 scoring")
    services = [s for s in shop.services]
    alerts = store.alerts(start=hist_end, end=eval_end)
    ours = evaluate(alerts, faults, hist_end, eval_end, len(services))
    feats = store.features(start=hist_end, end=eval_end)
    feats = feats[feats.service.isin(services)]
    static = evaluate(static_threshold_alerts(feats, 50), faults, hist_end, eval_end, len(services))
    static10 = evaluate(static_threshold_alerts(feats, 10), faults, hist_end, eval_end, len(services))
    an = store.anomalies(start=hist_end, end=eval_end)
    lat = an["e2e_latency_s"].dropna().to_numpy()
    tiers_df = an[an.is_anomalous == 1]
    report = {
        "name": name or datetime.now(timezone.utc).strftime("eval-%Y%m%d-%H%M%S"),
        "config": {"history_days": history_days, "eval_days": eval_days, "per_kind": per_kind, "seed": seed,
                   "base_rpm": base_rpm, "records_history": n_hist, "records_eval": n_eval},
        "model": {"version": tr["version"], "train_metrics": tr["metrics"]},
        "platform": {k: v for k, v in ours.items() if not k.startswith("_")},
        "static_threshold_baseline": {k: v for k, v in static.items() if not k.startswith("_")},
        "static_threshold_sensitive": {k: v for k, v in static10.items() if not k.startswith("_")},
        "latency": {"e2e_p50_s": float(np.percentile(lat, 50)) if len(lat) else None,
                    "e2e_p95_s": float(np.percentile(lat, 95)) if len(lat) else None,
                    "windows_scored": int(len(an))},
        "explanations": {"anomalous_windows": int(len(tiers_df)),
                         "with_top_drivers": int((tiers_df["top_drivers"].map(lambda s: s not in ("[]", "", None))).sum())
                         if len(tiers_df) else 0},
        "incidents": [{"id": f.id, "kind": f.kind, "service": f.service,
                       "start": datetime.fromtimestamp(f.start, timezone.utc).isoformat(),
                       "minutes": round((f.end - f.start) / 60)} for f in faults],
        "alerts": [{"alert_id": a["alert_id"], "service": a["service"], "severity": a["severity"],
                    "first_window": datetime.fromtimestamp(a["first_window"], timezone.utc).isoformat(),
                    "score": round(a["score"], 3), "incident_match": m}
                   for a, m in zip(alerts.to_dict("records"), ours["_alert_match"])],
        "wall_seconds": round(time.time() - t_wall, 1),
    }
    out_dir = resolve_path("evaluation/reports")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{report['name']}.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    (out_dir / f"{report['name']}.md").write_text(to_markdown(report), encoding="utf-8")
    print(to_markdown(report))
    store.close()
    if not keep:
        shutil.rmtree(data, ignore_errors=True)
    return report


def _fmt(v, pct=False):
    if v is None:
        return "-"
    if pct:
        return f"{v:.2f}"
    return f"{v:.1f}" if isinstance(v, float) else str(v)


def to_markdown(r: dict) -> str:
    p, s = r["platform"], r["static_threshold_baseline"]
    s10 = r.get("static_threshold_sensitive") or s
    lines = [
        f"# Evaluation report: {r['name']}", "",
        f"History {r['config']['history_days']:g} d ({r['config']['records_history']:,} records), "
        f"evaluation {r['config']['eval_days']:g} d ({r['config']['records_eval']:,} records), "
        f"{p['incidents']} injected incidents, model {r['model']['version']}.", "",
        "| Metric | Platform | Static >50 errors / 5 min (today) | Static >10 errors / 5 min | Target |",
        "|---|---|---|---|---|",
        f"| Precision | {_fmt(p['precision'], 1)} | {_fmt(s['precision'], 1)} | {_fmt(s10['precision'], 1)} | >= 0.80 |",
        f"| Recall | {_fmt(p['recall'], 1)} | {_fmt(s['recall'], 1)} | {_fmt(s10['recall'], 1)} | >= 0.70 |",
        f"| F1 | {_fmt(p['f1'], 1)} | {_fmt(s['f1'], 1)} | {_fmt(s10['f1'], 1)} | |",
        f"| Alerts | {p['alerts']} ({p['fp_alerts']} false) | {s['alerts']} ({s['fp_alerts']} false) | "
        f"{s10['alerts']} ({s10['fp_alerts']} false) | |",
        f"| Alerts / service / day | {_fmt(p['alerts_per_service_day'], 1)} | "
        f"{_fmt(s['alerts_per_service_day'], 1)} | {_fmt(s10['alerts_per_service_day'], 1)} | < 5 |",
        f"| Median detection delay (s, from fault start) | {_fmt(p['detection_delay_s_median'])} | "
        f"{_fmt(s['detection_delay_s_median'])} | {_fmt(s10['detection_delay_s_median'])} | |",
        f"| e2e latency p95 (s, newest log in window -> score) | {_fmt(r['latency']['e2e_p95_s'])} | - | - | < 60 |",
        "", "## Recall by fault kind", "",
        "| Fault | Incidents | Platform detected | Median delay (s) | Static >50 detected | Static >10 detected |",
        "|---|---|---|---|---|---|",
    ]
    for k, v in p["per_kind"].items():
        sv, sv10 = s["per_kind"].get(k, {}), s10["per_kind"].get(k, {})
        lines.append(f"| {k} | {v['incidents']} | {v['detected']} | {_fmt(v['median_delay_s'])} | "
                     f"{sv.get('detected', 0)} | {sv10.get('detected', 0)} |")
    lines += ["", "## Training-time evaluation gate", "",
              "| Metric | Value |", "|---|---|"]
    for k, v in r["model"]["train_metrics"].items():
        lines.append(f"| {k} | {_fmt(v, 1) if isinstance(v, float) else v} |")
    lines += ["", "## Alerts", "", "| Alert | Service | Severity | Window start | Score | Matched incident |",
              "|---|---|---|---|---|---|"]
    for a in r["alerts"]:
        lines.append(f"| {a['alert_id']} | {a['service']} | {a['severity']} | {a['first_window'][5:16]} | "
                     f"{a['score']} | {a['incident_match'] or '**false alarm**'} |")
    lines += ["", "## Injected incidents", "", "| Id | Kind | Service | Start | Minutes |", "|---|---|---|---|---|"]
    for i in r["incidents"]:
        lines.append(f"| {i['id']} | {i['kind']} | {i['service']} | {i['start'][5:16]} | {i['minutes']} |")
    lines.append(f"\n_Wall time {r['wall_seconds']} s._\n")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--history-days", type=float, default=7)
    ap.add_argument("--eval-days", type=float, default=2)
    ap.add_argument("--per-kind", type=int, default=2)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--rpm", type=float, default=24)
    ap.add_argument("--name", default=None)
    ap.add_argument("--data-dir", default="data/eval")
    a = ap.parse_args(argv)
    run(a.history_days, a.eval_days, a.per_kind, a.seed, a.rpm, a.data_dir, a.name)


if __name__ == "__main__":
    main()
