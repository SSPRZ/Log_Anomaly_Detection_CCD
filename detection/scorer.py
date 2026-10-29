"""anomaly-scorer: logs.features (+ logs.sequences, deploys.events) -> anomalies.scores.

Stateless with respect to Kafka (offsets committed after the produce is
flushed: at-least-once; the router de-duplicates), stateful only in its
in-memory baselines, which are bootstrapped from the champion bundle.

Per window:
    T1  robust z-scores (+ EWMA trend), damped for a while after a deploy
    T2  Isolation Forest of the service's group (champion)
    T3  share of traces with sequence violations attributed to the service
    R   hard rules (bypass debounce)
    ->  ensemble score, 2-of-3 debounce, explanation
The challenger (if any) is scored in shadow mode and recorded alongside.
"""
from __future__ import annotations

import json
import math
import time
from collections import defaultdict
from typing import Callable

from common import metrics as M
from detection.baseline import RobustBaseline
from detection.ensemble import Debouncer, Ensemble
from detection.explain import TemplateMovement, build_explanation, top_drivers
from detection.registry import Bundle, Registry
from detection.rules import Rules


class Scorer:
    def __init__(self, cfg: dict, registry: Registry | None = None, clock: Callable[[], float] = time.time,
                 template_text: dict | None = None):
        self.cfg = cfg
        d = cfg["detection"]
        self.registry = registry or Registry(cfg)
        self.clock = clock
        self.rules = Rules(cfg)
        self.debouncer = Debouncer(d["ensemble"]["debounce_windows"], d["ensemble"]["debounce_hits"])
        self.movement = TemplateMovement()
        self.template_text = template_text if template_text is not None else {}
        self.rewarm_s = float(d["deploy_rewarm_minutes"]) * 60
        self.damping = float(d["deploy_damping"])
        self.deploys: dict[str, float] = {}
        self.canary = cfg["observability"]["canary_service"]
        self.last_canary: float | None = None
        # sequence verdicts buffered per (service, minute)
        self.seq_traces: dict[tuple[str, float], int] = defaultdict(int)
        self.seq_viol: dict[tuple[str, float], list] = defaultdict(list)
        self.window = int(cfg["stream"]["window_seconds"])
        self._registry_mtime = -1.0
        self.champion: Bundle | None = None
        self.challenger: Bundle | None = None
        self.challenger_baseline: RobustBaseline | None = None
        self.fallback_baseline = RobustBaseline(cfg)
        self.reload(force=True)

    # ------------------------------------------------------------------ models
    def reload(self, force: bool = False) -> bool:
        mt = self.registry.mtime()
        off_path = self.registry.root / "service_thresholds.json"
        off_mt = off_path.stat().st_mtime if off_path.exists() else 0.0
        if not force and mt == self._registry_mtime and off_mt == getattr(self, "_offsets_mtime", 0.0):
            return False
        self._registry_mtime = mt
        self._offsets_mtime = off_mt
        self.offsets = json.loads(off_path.read_text(encoding="utf-8")) if off_path.exists() else {}
        idx = self.registry.index()
        champ_v = idx.get("champion")
        if champ_v and (self.champion is None or self.champion.version != champ_v):
            new = self.registry.load("champion")
            if new is not None:
                if self.champion is not None:
                    # keep the online-learned baseline across hot reloads
                    new.baseline = self.champion.baseline
                self.champion = new
                for g in new.forests:
                    M.MODEL_VERSION.labels(group=g).set(int(new.version[1:]))
        ch_v = idx.get("challenger")
        if ch_v and (self.challenger is None or self.challenger.version != ch_v):
            self.challenger = self.registry.load("challenger")
        elif not ch_v:
            self.challenger = None
        return True

    @property
    def baseline(self) -> RobustBaseline:
        return self.champion.baseline if self.champion else self.fallback_baseline

    # ------------------------------------------------------------------ side inputs
    def on_deploy(self, ev: dict) -> None:
        self.deploys[ev["service"]] = float(ev["ts"])

    def on_sequence(self, trace: dict) -> None:
        if not self.champion or not self.champion.sequence:
            return
        blamed = self.champion.sequence.attribute(trace)
        touched = {e["service"] for e in trace["events"]}
        if blamed:
            touched.add(blamed["service"])
        minute = trace["start"] - (trace["start"] % self.window)
        for svc in touched:
            self.seq_traces[(svc, minute)] += 1
        if blamed:
            self.seq_viol[(blamed["service"], minute)].append({"trace_id": trace["trace_id"], **blamed})

    def _sequence_score(self, svc: str, ws: float) -> tuple[float | None, list]:
        if not self.champion or not self.champion.sequence:
            return None, []
        # traces close ~30 s after their last event, so look at this and the previous minute
        n = v = 0
        viols: list = []
        for m in (ws - self.window, ws):
            n += self.seq_traces.get((svc, m), 0)
            vv = self.seq_viol.get((svc, m), [])
            v += len(vv)
            viols.extend(vv)
        for key in [k for k in self.seq_traces if k[1] < ws - 5 * self.window]:
            self.seq_traces.pop(key, None)
            self.seq_viol.pop(key, None)
        if n == 0:
            return None, []
        base = self.champion.sequence.base_rate.get(svc, 0.01)
        return self.champion.sequence.window_score(n, v, base), viols

    # ------------------------------------------------------------------ scoring
    def _tiers(self, bundle: Bundle | None, baseline: RobustBaseline, fv: dict, damping: float):
        z = baseline.zscores(fv, damping=damping)
        s_stats = RobustBaseline.score(z) if z else None
        forest = bundle.forest_for(fv["service"]) if bundle else None
        s_forest = forest.score_one(fv) if forest else None
        if s_forest is not None and damping < 1.0:
            s_forest *= damping
        return z, s_stats, s_forest

    def score(self, fv: dict) -> dict | None:
        """Score one feature vector; return the anomalies.scores record (None for revisions/canary)."""
        svc, ws = fv["service"], fv["window_start"]
        now = self.clock()
        if fv.get("is_canary") or svc == self.canary:
            self.last_canary = now
            M.CANARY_LAST.set(now)
            return {"service": svc, "window_start": ws, "window_end": fv["window_end"], "canary": True,
                    "emitted_at": now}
        if fv.get("revision", 0) > 0:
            return None  # late-data revisions update storage only; alerting used the on-time window
        self.reload()
        last_deploy = self.deploys.get(svc)
        damping = self.damping if last_deploy is not None and 0 <= ws - last_deploy < self.rewarm_s else 1.0

        z, s_stats, s_forest = self._tiers(self.champion, self.baseline, fv, damping)
        s_seq, viols = self._sequence_score(svc, ws)
        rule = self.rules.evaluate(fv)
        weights = self.champion.weights if self.champion else self.cfg["detection"]["ensemble"]["weights"]
        threshold = self.champion.threshold if self.champion else self.cfg["detection"]["ensemble"]["threshold"]
        threshold = min(0.95, threshold + float(self.offsets.get(svc, 0.0)))  # feedback-tuned per service
        ens = Ensemble(weights, threshold)
        tiers = {"stats": s_stats, "iforest": s_forest, "sequence": s_seq}
        score = ens.combine(tiers)
        over = score >= threshold
        debounced = self.debouncer.update(svc, over)
        is_anom = bool(debounced or rule)
        if rule:
            score = max(score, 0.9)

        moved = self.movement.moved(fv)
        explanation = build_explanation(fv, z, moved, rule, viols, self.template_text)
        drivers = top_drivers(z, fv)
        if rule:
            drivers = [f"rule:{r}" for r in rule["fired"]] + drivers

        shadow = None
        if self.challenger is not None:
            if self.challenger_baseline is None:
                self.challenger_baseline = self.challenger.baseline
            _, cs, cf = self._tiers(self.challenger, self.challenger_baseline, fv, damping)
            shadow = {"version": self.challenger.version,
                      "score": Ensemble(self.challenger.weights, self.challenger.threshold).combine(
                          {"stats": cs, "iforest": cf, "sequence": s_seq})}
            if not over:
                self.challenger_baseline.update(fv)

        # learn only from windows that look normal, so incidents don't become the new normal
        if not over and not rule:
            self.baseline.update(fv)
        self.movement.update(fv)

        latency = None
        if fv.get("max_event_ts"):
            latency = max(0.0, now - fv["max_event_ts"])
            M.E2E_LATENCY.observe(latency)
        M.SCORE.labels(service=svc).observe(score)
        return {
            "service": svc, "window_start": ws, "window_end": fv["window_end"],
            "score": round(score, 4), "over_threshold": over, "is_anomalous": is_anom,
            "tiers": {"stats": _r(s_stats), "iforest": _r(s_forest), "sequence": _r(s_seq), "rule": rule},
            "top_drivers": drivers, "explanation": explanation,
            "dominant_template": (moved[0]["template_id"] if moved else
                                  (fv["top_templates"][0]["template_id"] if fv.get("top_templates") else "")),
            "model_version": self.champion.version if self.champion else "untrained",
            "shadow": shadow, "deploy_damped": damping < 1.0,
            "emitted_at": now, "e2e_latency_s": latency,
        }


def _r(v):
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else round(float(v), 4)
