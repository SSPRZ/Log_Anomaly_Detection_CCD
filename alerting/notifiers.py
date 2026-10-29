"""Delivery channels. Each returns a short status string recorded on the alert.

When ``alerting.alertmanager_url`` is set, alerts are posted to Prometheus
Alertmanager (silences, inhibition, maintenance windows) which fans out to
Slack / e-mail / PagerDuty with its own receivers; otherwise the router
delivers directly. Unconfigured channels are skipped, never fatal.
"""
from __future__ import annotations

import json
import os
import smtplib
import time
from email.message import EmailMessage

import requests

from common.config import resolve_path
from common.timeutil import iso


def _summary(a: dict) -> str:
    return (f"[{a['severity']}] {a['service']}: anomaly score {a['score']:.2f} "
            f"({', '.join(a['top_drivers'][:3]) or 'see details'})")


class ConsoleNotifier:
    name = "dashboard"

    def __init__(self, path: str = "data/alerts.log", echo: bool = False):
        self.path = resolve_path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.echo = echo

    def send(self, alert: dict) -> str:
        line = json.dumps(alert, default=str)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        if self.echo:
            print("ALERT", _summary(alert))
        return "recorded"


class SlackNotifier:
    name = "slack"

    def __init__(self, webhook_url: str | None, timeout: float = 5.0):
        self.url = webhook_url or os.environ.get("SLACK_WEBHOOK_URL", "")
        self.timeout = timeout

    def payload(self, a: dict, channel: str | None = None) -> dict:
        templates = "\n".join(f"• `{t}`" for t in a.get("top_templates", [])[:3]) or "_none_"
        samples = "\n".join(a.get("samples", [])[:5]) or "-"
        blocks = [
            {"type": "header", "text": {"type": "plain_text", "text": f"{a['severity']} · {a['service']} anomaly"}},
            {"type": "section", "fields": [
                {"type": "mrkdwn", "text": f"*Score*\n{a['score']:.2f}"},
                {"type": "mrkdwn", "text": f"*Window*\n{a['window']}"},
                {"type": "mrkdwn", "text": f"*Drivers*\n{', '.join(a['top_drivers'][:3]) or '-'}"},
                {"type": "mrkdwn", "text": f"*Incident*\n{a.get('incident_id', '-')}"}]},
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*Top templates*\n{templates}"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*Samples (redacted)*\n```{samples[:2500]}```"}},
            {"type": "actions", "block_id": f"feedback:{a['alert_id']}", "elements": [
                {"type": "button", "action_id": "real", "style": "danger", "value": a["alert_id"],
                 "text": {"type": "plain_text", "text": "Real issue"}},
                {"type": "button", "action_id": "false_alarm", "value": a["alert_id"],
                 "text": {"type": "plain_text", "text": "False alarm"}},
                {"type": "button", "action_id": "open", "url": a["links"]["logs"],
                 "text": {"type": "plain_text", "text": "Open logs"}}]},
        ]
        body = {"text": _summary(a), "blocks": blocks}
        if channel:
            body["channel"] = channel
        return body

    def send(self, alert: dict, channel: str | None = None) -> str:
        if not self.url:
            return "skipped:not_configured"
        try:
            r = requests.post(self.url, json=self.payload(alert, channel), timeout=self.timeout)
            return "sent" if r.ok else f"error:{r.status_code}"
        except requests.RequestException as e:
            return f"error:{type(e).__name__}"


class PagerDutyNotifier:
    name = "pagerduty"
    URL = "https://events.pagerduty.com/v2/enqueue"

    def __init__(self, routing_key: str | None, timeout: float = 5.0):
        self.key = routing_key or os.environ.get("PAGERDUTY_ROUTING_KEY", "")
        self.timeout = timeout

    def send(self, alert: dict) -> str:
        if not self.key:
            return "skipped:not_configured"
        body = {"routing_key": self.key, "event_action": "trigger", "dedup_key": alert["alert_id"],
                "payload": {"summary": _summary(alert), "source": alert["service"], "severity": "critical",
                            "custom_details": {k: alert[k] for k in ("top_drivers", "top_templates", "tiers")}},
                "links": [{"href": alert["links"]["logs"], "text": "Logs"}]}
        try:
            r = requests.post(self.URL, json=body, timeout=self.timeout)
            return "sent" if r.status_code in (200, 202) else f"error:{r.status_code}"
        except requests.RequestException as e:
            return f"error:{type(e).__name__}"


class EmailNotifier:
    name = "email"

    def __init__(self, smtp: dict):
        self.smtp = smtp or {}

    def send(self, alert: dict) -> str:
        if not self.smtp.get("host") or not self.smtp.get("recipients"):
            return "skipped:not_configured"
        msg = EmailMessage()
        msg["Subject"] = _summary(alert)
        msg["From"] = self.smtp["sender"]
        msg["To"] = ", ".join(self.smtp["recipients"])
        msg.set_content(json.dumps(alert, indent=2, default=str)
                        + f"\n\nReal issue: {alert['links']['feedback_real']}"
                        + f"\nFalse alarm: {alert['links']['feedback_false']}")
        try:
            with smtplib.SMTP(self.smtp["host"], int(self.smtp.get("port", 587)), timeout=10) as s:
                if self.smtp.get("starttls", True):
                    s.starttls()
                if self.smtp.get("username"):
                    s.login(self.smtp["username"], os.environ.get("SMTP_PASSWORD", ""))
                s.send_message(msg)
            return "sent"
        except Exception as e:
            return f"error:{type(e).__name__}"


class AlertmanagerNotifier:
    name = "alertmanager"

    def __init__(self, url: str, timeout: float = 5.0):
        self.url = url.rstrip("/")
        self.timeout = timeout

    def send(self, alert: dict, resolved: bool = False) -> str:
        body = [{
            "labels": {"alertname": "LogAnomaly", "service": alert["service"], "severity": alert["severity"],
                       "alert_id": alert["alert_id"], "incident_id": alert.get("incident_id", "")},
            "annotations": {"summary": _summary(alert), "drivers": ", ".join(alert["top_drivers"]),
                            "templates": " | ".join(alert.get("top_templates", [])),
                            "logs_url": alert["links"]["logs"]},
            "startsAt": alert["opened_at_iso"],
            **({"endsAt": iso(alert.get("updated_at") or time.time())} if resolved else {}),
            "generatorURL": alert["links"]["grafana"],
        }]
        try:
            r = requests.post(f"{self.url}/api/v2/alerts", json=body, timeout=self.timeout)
            return "sent" if r.ok else f"error:{r.status_code}"
        except requests.RequestException as e:
            return f"error:{type(e).__name__}"
