"""LAP dashboard: anomaly timeline, explained alerts with feedback, log explorer, models, pipeline health.

    python lap.py dashboard [--db data/demo/lap.db]

Grafana (deploy/compose/grafana) shows the same tables natively from
ClickHouse in the full stack; this UI also captures responder feedback.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import common.preload  # noqa: E402,F401
import pandas as pd  # noqa: E402
import plotly.express as px  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import requests  # noqa: E402
import streamlit as st  # noqa: E402

from common.config import load_config  # noqa: E402
from detection.registry import Registry  # noqa: E402
from storage.store import make_store  # noqa: E402

st.set_page_config(page_title="Log Anomaly Platform", page_icon="🛰️", layout="wide")
SEV_COLOR = {"P1": "#d62728", "P2": "#ff7f0e", "P3": "#7f7f7f"}


@st.cache_resource
def resources():
    cfg = load_config()
    return cfg, make_store(cfg), Registry(cfg)


cfg, store, registry = resources()


def ts(x) -> str:
    return datetime.fromtimestamp(float(x), timezone.utc).strftime("%Y-%m-%d %H:%M")


def to_dt(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, unit="s", utc=True)


qp = st.query_params
pages = ["Overview", "Alerts", "Log explorer", "Templates", "Models", "Pipeline health"]
default_page = {"alerts": "Alerts", "logs": "Log explorer"}.get(qp.get("page", ""), "Overview")
page = st.sidebar.radio("View", pages, index=pages.index(default_page))
st.sidebar.caption(f"Storage: `{cfg['storage']['backend']}` · `{cfg['storage'].get('sqlite_path', '')}`")

# --------------------------------------------------------------------------- demo controls
# Inject labelled faults into the running platform (lap.py live / the compose "shop") from the page itself.
FAULT_API = os.environ.get("LAP_FAULT_API", "http://localhost:8090").rstrip("/")
FAULTS = {
    "Bank-gateway timeouts": ("downstream_timeout", "payments"),
    "Service goes silent": ("silent", "notifications"),
    "Bad deploy (new exception)": ("bad_deploy", "orders"),
    "Crash loop": ("crash_loop", "inventory"),
    "Memory leak (slow drift)": ("memory_leak", "gateway"),
    "Skipped workflow step": ("skip_step", "orders"),
}
ALL_SERVICES = ["gateway", "orders", "payments", "inventory", "notifications"]
with st.sidebar.expander("Demo controls", expanded=True):
    choice = st.selectbox("Fault", list(FAULTS))
    kind, default_svc = FAULTS[choice]
    svc_choice = st.selectbox("Service", ALL_SERVICES, index=ALL_SERVICES.index(default_svc),
                              disabled=kind == "skip_step")
    minutes = st.slider("Duration (minutes)", 2, 30, 10)

    def inject() -> None:
        # on_click runs exactly once per click (a plain `if st.button` can fire twice when the
        # click lands during the auto-refresh sleep); the guard also absorbs double clicks
        if time.time() - st.session_state.get("injected_at", 0.0) < 5:
            return
        st.session_state["injected_at"] = time.time()
        try:
            r = requests.post(f"{FAULT_API}/faults", timeout=5,
                              json={"kind": kind, "service": "orders" if kind == "skip_step" else svc_choice,
                                    "duration_min": minutes})
            r.raise_for_status()
            st.session_state["last_fault"] = r.json()
            st.session_state.pop("inject_error", None)
        except requests.RequestException:
            st.session_state["inject_error"] = True

    st.button("Inject fault", type="primary", use_container_width=True, on_click=inject)
    if st.session_state.get("inject_error"):
        st.error(f"Can’t reach the fault injector at {FAULT_API}. Start the live platform first (demo.bat).")
    try:
        active = [f for f in requests.get(f"{FAULT_API}/faults", timeout=2).json() if f.get("active")]
    except requests.RequestException:
        active = None
    if active is None:
        st.caption(f"Fault injector offline ({FAULT_API})")
    elif not active:
        st.caption("No active faults")
    for f in active or []:
        left = max(0, int(f["end"] - time.time()))
        c1, c2 = st.columns([3, 2])
        c1.caption(f"**{f['kind']}** on {f['service']} · {left // 60}:{left % 60:02d} left")
        if c2.button("Stop", key=f"stop-{f['id']}"):
            requests.post(f"{FAULT_API}/faults/{f['id']}/stop", timeout=5)
            st.rerun()
    auto_refresh = st.toggle("Auto-refresh every 10 s", value=True)


def refresh_later() -> None:
    """Keep the page live during a demo: re-run the script every 10 s."""
    if auto_refresh:
        time.sleep(10)
        st.rerun()


last = st.session_state.get("last_fault")
if last and time.time() < last["end"]:
    ago = int(time.time() - last["start"])
    st.info(f"**{last['kind']}** injected on **{last['service']}** {ago // 60}:{ago % 60:02d} ago. "
            "Alerts usually appear within 2–3 minutes (one-minute windows plus a 2-of-3 debounce).")

feats_all = store.features()
if feats_all.empty:
    st.info("No data yet. Start the live platform (demo.bat) or run `python lap.py demo` first.")
    refresh_later()
    st.stop()
t_min, t_max = float(feats_all.window_start.min()), float(feats_all.window_start.max()) + 60
services = sorted(s for s in feats_all.service.unique() if s != cfg["observability"]["canary_service"])

max_hours = int((t_max - t_min) / 3600) + 1
if max_hours > 1:
    hours = st.sidebar.slider("Time range (hours, ending at newest data)", 1, max_hours, min(48, max_hours))
else:  # under an hour of data (e.g. a live demo that just started): show all of it
    hours = 1
    st.sidebar.caption(f"Showing all data ({int((t_max - t_min) / 60)} min so far)")
start = max(t_min, t_max - hours * 3600)
sel_services = st.sidebar.multiselect("Services", services, default=services)


# --------------------------------------------------------------------------- overview
if page == "Overview":
    st.title("Log anomaly detection")
    an = store.anomalies(start=start, end=t_max)
    al = store.alerts(start=start, end=t_max)
    dlq = store.dlq(5000)
    c = st.columns(5)
    c[0].metric("Windows scored", f"{len(an):,}")
    c[1].metric("Anomalous windows", int(an.is_anomalous.sum()) if len(an) else 0)
    c[2].metric("Alerts", len(al), help="after de-duplication and grouping")
    lat = an.e2e_latency_s.dropna() if len(an) else pd.Series(dtype=float)
    c[3].metric("e2e latency p95", f"{lat.quantile(.95):.1f} s" if len(lat) else "-", help="target < 60 s")
    c[4].metric("DLQ records", len(dlq))

    if len(an):
        an = an[an.service.isin(sel_services)]
        an["time"] = to_dt(an.window_start)
        champ = registry.load("champion")
        thr = champ.threshold if champ else cfg["detection"]["ensemble"]["threshold"]
        fig = px.line(an.sort_values("time"), x="time", y="score", color="service", height=420,
                      title="Ensemble anomaly score per service-minute")
        fig.add_hline(y=thr, line_dash="dash", annotation_text=f"threshold {thr:.2f}")
        if len(al):
            for a in al[al.service.isin(sel_services)].itertuples():
                fig.add_vrect(x0=to_dt(pd.Series([a.first_window]))[0], x1=to_dt(pd.Series([a.last_window + 60]))[0],
                              fillcolor=SEV_COLOR.get(a.severity, "#999"), opacity=0.15, line_width=0)
        st.plotly_chart(fig, use_container_width=True)

    f = feats_all[(feats_all.window_start >= start) & feats_all.service.isin(sel_services)].copy()
    f["time"] = to_dt(f.window_start)
    cols = st.columns(2)
    for i, (feat, title) in enumerate([("total_count", "Log volume / min"), ("error_ratio", "Error ratio"),
                                       ("p95_latency_ms", "p95 latency (ms)"), ("unique_templates", "Unique templates")]):
        cols[i % 2].plotly_chart(px.line(f, x="time", y=feat, color="service", title=title, height=280),
                                 use_container_width=True)

# --------------------------------------------------------------------------- alerts
elif page == "Alerts":
    st.title("Alerts")
    if qp.get("alert") and qp.get("verdict") in ("real", "false_alarm"):
        store.add_feedback(qp["alert"], qp["verdict"], "link")
        st.success(f"Feedback recorded for {qp['alert']}: {qp['verdict']}")
    al = store.alerts(start=start, end=t_max)
    fb = store.feedback()
    if al.empty:
        st.info("No alerts in this range.")
        st.stop()
    latest_fb = fb.sort_values("ts").groupby("alert_id").tail(1).set_index("alert_id")["verdict"] if len(fb) else {}
    al = al[al.service.isin(sel_services)].copy()
    al["verdict"] = al.alert_id.map(lambda a: latest_fb.get(a, "") if len(fb) else "")
    al["window"] = al.first_window.map(ts) + " → " + (al.last_window + 60).map(lambda x: ts(x)[11:])
    shown = al[["alert_id", "incident_id", "severity", "service", "status", "window", "score", "verdict"]]
    st.dataframe(shown, use_container_width=True, hide_index=True)
    share = (al.verdict != "").mean() if len(al) else 0
    st.caption(f"Feedback captured on {share:.0%} of alerts (target ≥ 60%).")

    pick = st.selectbox("Alert details", al.alert_id.tolist(),
                        index=al.alert_id.tolist().index(qp["alert"]) if qp.get("alert") in al.alert_id.tolist() else 0)
    a = al[al.alert_id == pick].iloc[0]
    p = json.loads(a.payload)
    left, right = st.columns([3, 2])
    with left:
        st.subheader(f"{a.severity} · {a.service} · score {a.score:.2f}")
        st.write(f"**Window** {p.get('window')} · **Incident** `{a.incident_id}` · **Model** `{p.get('model_version')}`")
        st.write("**Top drivers**: " + (", ".join(p.get("top_drivers", [])) or "-"))
        st.write("**Tier scores**")
        st.json(p.get("tiers", {}), expanded=False)
        st.write("**Templates that moved most**")
        for t in p.get("top_templates", []):
            st.code(t, language=None)
        st.write("**Sample log lines (redacted)**")
        st.code("\n".join(p.get("samples", [])) or "-", language=None)
    with right:
        st.write("**Responder feedback**")
        b1, b2 = st.columns(2)
        if b1.button("✅ Real issue", key=f"r{pick}"):
            store.add_feedback(pick, "real", "dashboard")
            st.rerun()
        if b2.button("🚫 False alarm", key=f"f{pick}"):
            store.add_feedback(pick, "false_alarm", "dashboard")
            st.rerun()
        st.write("**Channels**: " + ", ".join(json.loads(a.channels or "[]")))
        an = store.anomalies(start=a.first_window - 30 * 60, end=a.last_window + 30 * 60, service=a.service)
        if len(an):
            an["time"] = to_dt(an.window_start)
            fig = go.Figure()
            for col in ("score", "stats", "iforest", "sequence"):
                if an[col].notna().any():
                    fig.add_scatter(x=an.time, y=an[col], name=col, mode="lines")
            fig.update_layout(height=300, title="Scores around the alert", margin=dict(t=40, b=10))
            st.plotly_chart(fig, use_container_width=True)

# --------------------------------------------------------------------------- logs
elif page == "Log explorer":
    st.title("Log explorer")
    c = st.columns([3, 2, 1, 1])
    text = c[0].text_input("Message contains", "")
    svc = c[1].selectbox("Service", ["(all)"] + services,
                         index=(services.index(qp["service"]) + 1) if qp.get("service") in services else 0)
    level = c[2].selectbox("Level", ["(all)", "FATAL", "ERROR", "WARN", "INFO", "DEBUG"])
    limit = c[3].number_input("Limit", 50, 5000, 500, step=50)
    s0 = float(qp.get("start", start))
    e0 = float(qp.get("end", t_max))
    logs = store.search_logs(text=text, service=None if svc == "(all)" else svc,
                             level=None if level == "(all)" else level, start=s0, end=e0, limit=int(limit))
    if len(logs):
        logs.insert(0, "time", to_dt(logs.ts))
        st.dataframe(logs.drop(columns=["ts"]), use_container_width=True, hide_index=True, height=600)
    else:
        st.info("No matching logs (raw logs are retained for 14 days).")

# --------------------------------------------------------------------------- templates
elif page == "Templates":
    st.title("Log templates (Drain)")
    tp = store.templates()
    if len(tp):
        tp["first_seen"] = tp.first_seen.map(ts)
        tp["last_seen"] = tp.last_seen.map(ts)
        st.dataframe(tp.sort_values("size", ascending=False), use_container_width=True, hide_index=True, height=650)

# --------------------------------------------------------------------------- models
elif page == "Models":
    st.title("Model registry")
    idx = registry.index()
    c = st.columns(3)
    c[0].metric("Champion", idx.get("champion") or "-")
    c[1].metric("Challenger (shadow)", idx.get("challenger") or "-")
    c[2].metric("Versions", len(idx.get("versions", {})))
    rows = [{"version": v, "status": m.get("status"), "created": ts(m["created"]),
             **{k: (round(x, 3) if isinstance(x, float) else x) for k, x in m.get("metrics", {}).items()
                if k in ("precision", "recall", "f1", "alerts_per_service_day", "gate_passed", "champion_f1")}}
            for v, m in sorted(idx.get("versions", {}).items())]
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    b1, b2 = st.columns(2)
    if idx.get("challenger") and b1.button("Promote challenger"):
        registry.promote(reason="dashboard")
        st.rerun()
    if b2.button("Roll back champion"):
        try:
            st.success(f"Champion is now {registry.rollback()}")
        except ValueError as e:
            st.warning(str(e))
    champ = registry.load("champion")
    if champ:
        st.write(f"**Ensemble** weights `{champ.weights}` · threshold `{champ.threshold:.2f}` · "
                 f"sequence backend `{champ.sequence.backend if champ.sequence else 'none'}` · "
                 f"forests: " + ", ".join(f"`{g}` ({f.backend})" for g, f in champ.forests.items()))
    st.subheader("History")
    st.dataframe(pd.DataFrame(idx.get("history", [])), use_container_width=True, hide_index=True)

# --------------------------------------------------------------------------- pipeline
elif page == "Pipeline health":
    st.title("Pipeline health")
    f = feats_all[feats_all.window_start >= start].copy()
    f["time"] = to_dt(f.window_start)
    vol = f.groupby("time").total_count.sum().reset_index()
    st.plotly_chart(px.area(vol, x="time", y="total_count", title="Ingested records per minute", height=300),
                    use_container_width=True)
    canary = f[f.service == cfg["observability"]["canary_service"]]
    st.write(f"Canary windows: {len(canary)} · newest {ts(canary.window_start.max()) if len(canary) else '-'}")
    an = store.anomalies(start=start, end=t_max)
    if len(an) and an.e2e_latency_s.notna().any():
        st.plotly_chart(px.histogram(an, x="e2e_latency_s", nbins=40, height=280,
                                     title="End-to-end latency (newest log in window → score)"),
                        use_container_width=True)
    dlq = store.dlq(5000)
    st.subheader(f"Dead-letter queue ({len(dlq)} recent)")
    if len(dlq):
        st.dataframe(dlq.groupby(["stage", "reason"]).size().rename("records").reset_index(), hide_index=True)
        st.dataframe(dlq.head(50), use_container_width=True, hide_index=True)
    dep = store.deploys()
    st.subheader("Deploy events (baselines re-warm after these)")
    if len(dep):
        dep["time"] = dep.ts.map(ts)
        st.dataframe(dep.drop(columns=["ts"]).tail(50), use_container_width=True, hide_index=True)

refresh_later()
