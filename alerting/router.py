"""alert-router: anomalies.scores -> alerts.outbound (+ notifications).

* De-duplication: anomalies of the same service and dominant template within
  ``dedup_minutes`` join one open alert (a service with an open alert on a
  different template also joins it, so one incident is one alert).
* Grouping: alerts in services that call each other, opened within
  ``incident_group_minutes``, share an incident_id.
* Severity: P1 (score >= 0.9 or hard rule on a tier-1 service) pages via
  PagerDuty; P2 goes to the team Slack channel; P3 is dashboard-only.
* Routing rules, maintenance windows and silences live in alerting/rules.yaml.
* Dead-man's switch: no canary score for ``canary_deadman_seconds`` -> page.

All times are event times carried on the score records (``now`` = the
scorer's emitted_at), so the router behaves identically in real time and in
accelerated simulation.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import yaml

from common import metrics as M
from common.config import resolve_path
from common.timeutil import iso, parse_ts
from alerting.notifiers import (AlertmanagerNotifier, ConsoleNotifier, EmailNotifier, PagerDutyNotifier,
                                SlackNotifier)
from storage.store import Store

SEV_RANK = {"P1": 3, "P2": 2, "P3": 1}


class AlertRouter:
    def __init__(self, cfg: dict, store: Store | None = None, notifiers: dict | None = None, echo: bool = False):
        a = cfg["alerting"]
        self.cfg = cfg
        self.store = store
        self.rules = yaml.safe_load(open(resolve_path(a["rules_file"]), encoding="utf-8"))
        topo = yaml.safe_load(open(resolve_path("config/topology.yaml"), encoding="utf-8"))
        self.services = topo["services"]
        self.neighbours: dict[str, set] = {}
        for src, dsts in (topo.get("calls") or {}).items():
            for d in dsts:
                self.neighbours.setdefault(src, set()).add(d)
                self.neighbours.setdefault(d, set()).add(src)
        self.dedup_s = float(a["dedup_minutes"]) * 60
        self.group_s = float(a["incident_group_minutes"]) * 60
        self.dashboard_url = a["dashboard_url"]
        self.grafana_url = a["grafana_url"]
        self.open: dict[str, dict] = {}       # alert_id -> alert
        self.notifiers = notifiers or {
            "dashboard": ConsoleNotifier(echo=echo),
            "slack": SlackNotifier(a.get("slack_webhook_url")),
            "pagerduty": PagerDutyNotifier(a.get("pagerduty_routing_key")),
            "email": EmailNotifier(a.get("smtp")),
        }
        self.alertmanager = AlertmanagerNotifier(a["alertmanager_url"]) if a.get("alertmanager_url") else None
        self.seq = 0
        o = cfg["observability"]
        self.deadman_s = float(o["canary_deadman_seconds"])
        self.last_canary: float | None = None
        self.pipeline_alert_open = False
        self.sent: list[dict] = []
        self.am_resend_s = 60.0       # Alertmanager expires alerts not re-sent within resolve_timeout
        self._am_last_resend = 0.0
        self._restore_open_alerts()

    def _restore_open_alerts(self) -> None:
        """After a restart, keep de-duplicating against alerts that are still open in storage.

        The router commits offsets after delivery, so a crash replays a few scores; without
        this, every replayed anomaly would open a duplicate alert.
        """
        if self.store is None:
            return
        try:
            df = self.store.alerts(limit=500)
        except Exception:
            return
        for r in df[df.status == "open"].to_dict("records") if len(df) else []:
            payload = json.loads(r["payload"]) if isinstance(r["payload"], str) else (r["payload"] or {})
            self.open[r["alert_id"]] = {
                "alert_id": r["alert_id"], "incident_id": r["incident_id"], "service": r["service"],
                "severity": r["severity"], "status": "open", "opened_at": r["opened_at"],
                "updated_at": r["updated_at"], "first_window": r["first_window"], "last_window": r["last_window"],
                "score": r["score"], "dominant_template": r.get("dominant_template") or "", "n_windows": 1,
                "rules": [], "payload": payload,
                "channels": json.loads(r["channels"]) if isinstance(r.get("channels"), str) else []}

    @staticmethod
    def _safe(fn, *args, **kwargs) -> str:
        """A failing channel must never take the router down (it would replay and duplicate alerts)."""
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            print(f"[alert-router] notifier error: {type(e).__name__}: {e}", flush=True)
            return f"error:{type(e).__name__}"

    # ------------------------------------------------------------------ policy
    def severity(self, rec: dict) -> str:
        tier = int(self.services.get(rec["service"], {}).get("tier", 2))
        rule = (rec.get("tiers") or {}).get("rule")
        sev = self.rules["severity"]
        if rec["score"] >= sev["p1_min_score"] and not rec.get("deploy_damped"):
            return "P1" if tier <= 2 or rule else "P2"
        if rule and tier == 1:
            return "P1"
        if tier >= sev["p3_max_tier"] and not rule:
            return "P3"
        return "P2"

    def channels(self, alert: dict) -> list[str]:
        out: list[str] = []
        for route in self.rules["routes"]:
            m = route.get("match", {})
            if all(alert.get(k) == v for k, v in m.items()):
                out.extend(c for c in route["channels"] if c not in out)
                if not route.get("continue"):
                    break
        return out or ["dashboard"]

    def _suppressed(self, rec: dict, ts: float) -> str | None:
        for w in self.rules.get("maintenance_windows") or []:
            if w.get("service") in (None, rec["service"]) and parse_ts(w["start"]) <= ts < parse_ts(w["end"]):
                return "maintenance"
        for s in self.rules.get("silences") or []:
            if s.get("service") in (None, rec["service"]) and \
                    s.get("template_id") in (None, rec.get("dominant_template")) and ts < parse_ts(s["until"]):
                return "silenced"
        return None

    # ------------------------------------------------------------------ main entry
    def handle(self, rec: dict) -> list[dict]:
        """Process one score record; return the alert events emitted to alerts.outbound."""
        now = float(rec.get("emitted_at") or rec["window_end"])
        events = self.tick(now)
        if rec.get("canary"):
            self.last_canary = now
            if self.pipeline_alert_open:
                self.pipeline_alert_open = False
                events.append(self._pipeline_event("resolved", now))
            return events
        if not rec.get("is_anomalous"):
            return events
        ts = rec["window_start"]
        reason = self._suppressed(rec, ts)
        if reason:
            return events
        svc = rec["service"]
        tpl = rec.get("dominant_template") or ""
        rule = (rec.get("tiers") or {}).get("rule")
        existing = None
        for al in self.open.values():
            if al["service"] == svc and ts - al["last_window"] <= self.dedup_s:
                if al["dominant_template"] == tpl or existing is None:
                    existing = al
        if existing and rule and not existing.get("rules"):
            # a hard rule (new ERROR template, liveness) on top of a statistical alert is a new,
            # high-precision signal: open a fresh alert in the same incident rather than burying it
            return events + self._open(rec, now, incident_id=existing["incident_id"])
        if existing:
            return events + self._update(existing, rec, now)
        return events + self._open(rec, now)

    def _alert_id(self, ts: float) -> str:
        self.seq += 1
        day = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y%m%d")
        return f"al-{day}-{self.seq:04d}-{uuid.uuid4().hex[:4]}"

    def _incident_for(self, svc: str, ts: float) -> str:
        for al in self.open.values():
            if al["service"] in self.neighbours.get(svc, set()) and abs(ts - al["first_window"]) <= self.group_s:
                return al["incident_id"]
        return "inc-" + uuid.uuid4().hex[:8]

    def _payload(self, al: dict, rec: dict) -> dict:
        ex = rec.get("explanation") or {}
        q = f"service={al['service']}&start={int(al['first_window'])}&end={int(al['last_window'] + 60)}"
        return {
            "alert_id": al["alert_id"], "incident_id": al["incident_id"], "severity": al["severity"],
            "service": al["service"], "status": al["status"],
            "window": f"{iso(al['first_window'])[11:16]}–{iso(al['last_window'] + 60)[11:16]} UTC",
            "score": round(al["score"], 2), "tiers": rec.get("tiers"),
            "top_drivers": rec.get("top_drivers", []),
            "top_templates": [t.get("template", t["template_id"]) for t in ex.get("top_templates", [])][:3],
            "samples": ex.get("samples", [])[:5],
            "opened_at_iso": iso(al["opened_at"]),
            "links": {"grafana": f"{self.grafana_url}/d/lap-anomalies?var-service={al['service']}",
                      "logs": f"{self.dashboard_url}/?page=logs&{q}",
                      "feedback_real": f"{self.dashboard_url}/?page=alerts&alert={al['alert_id']}&verdict=real",
                      "feedback_false": f"{self.dashboard_url}/?page=alerts&alert={al['alert_id']}&verdict=false_alarm"},
            "model_version": rec.get("model_version"),
        }

    def _open(self, rec: dict, now: float, incident_id: str | None = None) -> list[dict]:
        ts = rec["window_start"]
        rule = (rec.get("tiers") or {}).get("rule")
        al = {"alert_id": self._alert_id(ts), "incident_id": incident_id or self._incident_for(rec["service"], ts),
              "service": rec["service"], "severity": self.severity(rec), "status": "open",
              "opened_at": now, "updated_at": now, "first_window": ts, "last_window": ts,
              "score": rec["score"], "dominant_template": rec.get("dominant_template") or "", "n_windows": 1,
              "rules": sorted(rule["fired"]) if rule else []}
        al["payload"] = self._payload(al, rec)
        self.open[al["alert_id"]] = al
        return [self._deliver(al, "opened")]

    def _update(self, al: dict, rec: dict, now: float) -> list[dict]:
        al["last_window"] = max(al["last_window"], rec["window_start"])
        al["updated_at"] = now
        al["n_windows"] += 1
        rule = (rec.get("tiers") or {}).get("rule")
        if rule:
            al["rules"] = sorted(set(al.get("rules", [])) | set(rule["fired"]))
        escalate = False
        if rec["score"] > al["score"]:
            al["score"] = rec["score"]
            al["payload"] = self._payload(al, rec)
        new_sev = self.severity(rec)
        if SEV_RANK[new_sev] > SEV_RANK[al["severity"]]:
            al["severity"] = new_sev
            al["payload"] = self._payload(al, rec)
            escalate = True
        al["payload"]["window"] = self._payload(al, rec)["window"]
        if escalate:
            return [self._deliver(al, "escalated")]
        self._persist(al, al.get("channels", []))
        return []

    def _deliver(self, al: dict, action: str) -> dict:
        chans = self.channels(al)
        statuses = []
        p = al["payload"]
        p["severity"], p["status"] = al["severity"], al["status"]
        if self.alertmanager is not None and set(chans) - {"dashboard"}:
            statuses.append(f"alertmanager:{self._safe(self.alertmanager.send, p, resolved=al['status'] == 'resolved')}")
            chans_direct = ["dashboard"]
        else:
            chans_direct = chans
        for c in chans_direct:
            n = self.notifiers.get(c)
            if n is None:
                continue
            if c == "slack":
                ch = self.services.get(al["service"], {}).get("slack_channel")
                statuses.append(f"slack:{self._safe(n.send, p, channel=ch)}")
            else:
                statuses.append(f"{c}:{self._safe(n.send, p)}")
        if "dashboard" not in chans_direct:
            self._safe(self.notifiers["dashboard"].send, p)
        al["channels"] = statuses
        self._persist(al, statuses)
        if action in ("opened", "escalated"):
            M.ALERTS.labels(service=al["service"], severity=al["severity"]).inc()
        ev = {"action": action, **p, "channels": statuses}
        self.sent.append(ev)
        return ev

    def _persist(self, al: dict, statuses: list[str]) -> None:
        if self.store is not None:
            self.store.upsert_alert({**al, "payload": al["payload"], "channels": statuses})

    # ------------------------------------------------------------------ housekeeping
    def tick(self, now: float) -> list[dict]:
        events = []
        if self.alertmanager is not None and now - self._am_last_resend >= self.am_resend_s:
            # like Prometheus, keep firing alerts alive in Alertmanager until they resolve
            for al in self.open.values():
                if set(self.channels(al)) - {"dashboard"}:
                    self._safe(self.alertmanager.send, al["payload"])
            self._am_last_resend = now
        for aid in list(self.open):
            al = self.open[aid]
            if now - (al["last_window"] + 60) > self.dedup_s:
                al["status"] = "resolved"
                al["updated_at"] = now
                al["payload"]["status"] = "resolved"
                self._persist(al, al.get("channels", []))
                if self.alertmanager is not None:
                    self._safe(self.alertmanager.send, {**al["payload"], "updated_at": now}, resolved=True)
                events.append({"action": "resolved", "alert_id": aid, "service": al["service"]})
                del self.open[aid]
        if self.last_canary is not None and not self.pipeline_alert_open and now - self.last_canary > self.deadman_s:
            self.pipeline_alert_open = True
            events.append(self._pipeline_event("firing", now))
        return events

    def _pipeline_event(self, state: str, now: float) -> dict:
        p = self.rules.get("pipeline", {})
        ev = {"action": f"pipeline_heartbeat_{state}", "severity": "P1", "service": "lap-pipeline",
              "summary": f"No canary score for {int(now - (self.last_canary or now))} s" if state == "firing"
              else "Canary scores flowing again", "ts": now}
        alert = {"alert_id": f"pipeline-{int(now)}", "service": "lap-pipeline", "severity": "P1", "score": 1.0,
                 "top_drivers": [ev["summary"]], "window": iso(now), "incident_id": "-", "samples": [],
                 "links": {"logs": self.grafana_url, "grafana": self.grafana_url}}
        for c in p.get("channels", ["slack"]):
            n = self.notifiers.get(c)
            if n is not None and c == "slack":
                self._safe(n.send, alert, channel=p.get("slack_channel"))
        self._safe(self.notifiers["dashboard"].send, {**alert, **ev})
        self.sent.append(ev)
        return ev
