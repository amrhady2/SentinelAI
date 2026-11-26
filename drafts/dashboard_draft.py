from __future__ import annotations

import json
import os

import pandas as pd
import plotly.express as px
import requests
import streamlit as st

API_URL = os.getenv("SENTINEL_API_URL", "http://127.0.0.1:8000")
st.set_page_config(page_title="SentinelAI", page_icon="🛰️", layout="wide")
st.title("🛰️ SentinelAI")
st.caption("Intelligent Observability & Event Watchdog · AI-assisted insights are evidence-based; detection is deterministic.")


def api_get(path: str, **params):
    response = requests.get(f"{API_URL}{path}", params=params, timeout=5)
    response.raise_for_status()
    return response.json()


def api_post(path: str, payload=None):
    response = requests.post(f"{API_URL}{path}", json=payload, timeout=15)
    response.raise_for_status()
    return response.json()


with st.sidebar:
    st.header("Controls")
    st.caption(f"API: {API_URL}")
    if st.button("Load synthetic demo data", type="primary", use_container_width=True):
        try:
            result = api_post("/api/v1/demo/seed")
            st.success(f"Inserted {result['inserted']} synthetic events")
        except requests.RequestException as exc:
            st.error(f"API unavailable: {exc}")
    st.divider()
    st.subheader("Ingest logs")
    uploaded = st.file_uploader("Upload JSON or CSV", type=["json", "csv"])
    if uploaded is not None and st.button("Ingest uploaded logs", use_container_width=True):
        try:
            if uploaded.name.lower().endswith(".csv"):
                frame = pd.read_csv(uploaded)
                records = frame.where(pd.notnull(frame), None).to_dict(orient="records")
            else:
                raw = json.load(uploaded)
                records = raw if isinstance(raw, list) else raw.get("events", [])
            mapped = []
            for row in records:
                mapped.append({
                    "timestamp": row.get("timestamp"),
                    "service": row.get("service", "unknown"),
                    "level": row.get("level", row.get("severity", "info")),
                    "message": row.get("message", row.get("msg", "(no message)")),
                    "latency_ms": row.get("latency_ms"),
                    "trace_id": row.get("trace_id"),
                    "attributes": row.get("attributes", {}),
                })
            result = api_post("/api/v1/logs/ingest", {"events": mapped})
            st.success(f"Accepted {result['accepted']} events; new incidents: {result['incidents_created']}")
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            st.error(f"Could not ingest file: {exc}")
    st.divider()
    refresh = st.button("Refresh dashboard", use_container_width=True)

try:
    summary = api_get("/api/v1/metrics/summary", window_minutes=60)
    series = api_get("/api/v1/metrics/timeseries", interval_minutes=5, points=24)
    incidents = api_get("/api/v1/incidents", limit=100)
    alerts = api_get("/api/v1/alerts", limit=100)
    logs = api_get("/api/v1/logs", limit=20)
except requests.RequestException as exc:
    st.error(f"Cannot reach SentinelAI API at {API_URL}. Start the API first: `{exc}`")
    st.stop()

cols = st.columns(5)
cols[0].metric("Health score", f"{summary['health_score']}/100", summary["health_label"].title())
cols[1].metric("Events · 60 min", summary["event_count"])
cols[2].metric("Errors", summary["error_count"], f"{summary['error_rate']:.1%} error rate")
cols[3].metric("Open incidents", summary["open_incident_count"])
cols[4].metric("Services", summary["service_count"])

left, right = st.columns([1.5, 1])
with left:
    st.subheader("Service health trend")
    frame = pd.DataFrame(series)
    if not frame.empty:
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
        chart = px.line(frame, x="timestamp", y=["error_count", "event_count"], markers=True,
                        labels={"value": "Events per 5 min", "timestamp": "Time", "variable": "Series"})
        chart.update_layout(legend_title_text="", margin=dict(l=10, r=10, t=20, b=10))
        st.plotly_chart(chart, use_container_width=True)
        latency = frame.dropna(subset=["p95_latency_ms"])
        if not latency.empty:
            st.caption("p95 latency by 5-minute bucket")
            st.line_chart(latency.set_index("timestamp")["p95_latency_ms"])
with right:
    st.subheader("Incident breakdown")
    if incidents:
        inc_frame = pd.DataFrame(incidents)
        counts = inc_frame.groupby(["severity", "status"]).size().reset_index(name="count")
        st.plotly_chart(px.bar(counts, x="severity", y="count", color="status", barmode="group"), use_container_width=True)
    else:
        st.info("No incidents yet. Load synthetic demo data or ingest logs to begin.")

st.subheader("Incidents")
if incidents:
    for incident in incidents:
        label = f"#{incident['id']} · {incident['severity'].upper()} · {incident['service']} · {incident['status']}"
        with st.expander(label, expanded=incident["status"] == "open"):
            st.write(incident["explanation"])
            st.caption(f"First seen: {incident['first_seen']} · Last seen: {incident['last_seen']} · Events: {incident['event_count']}")
            c1, c2, c3 = st.columns([1, 1, 3])
            for status, col in [("acknowledged", c1), ("resolved", c2)]:
                if incident["status"] != status and col.button(status.title(), key=f"{status}-{incident['id']}"):
                    try:
                        response = requests.patch(f"{API_URL}/api/v1/incidents/{incident['id']}", json={"status": status}, timeout=5)
                        response.raise_for_status()
                        st.rerun()
                    except requests.RequestException as exc:
                        st.error(f"Update failed: {exc}")
else:
    st.info("No incidents to display.")

with st.expander("Simulated webhook alert log"):
    if alerts:
        st.caption("Alerts are recorded locally; this MVP does not send network webhooks.")
        st.dataframe(pd.DataFrame(alerts), use_container_width=True, hide_index=True)
    else:
        st.write("No simulated alerts yet.")

with st.expander("Recent log events"):
    if logs:
        st.dataframe(pd.DataFrame(logs), use_container_width=True, hide_index=True)
    else:
        st.write("No logs ingested yet.")
