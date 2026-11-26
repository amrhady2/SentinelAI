from __future__ import annotations

import os
from urllib.parse import urlparse

import pandas as pd
import plotly.express as px
import httpx
import streamlit as st

DEFAULT_API_URL = "http://127.0.0.1:8000"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}

st.set_page_config(page_title="SentinelAI Dashboard", page_icon="🛰️", layout="wide")
st.title("🛰️ SentinelAI")
st.caption("Intelligent Observability & Event Watchdog · read-only local dashboard")


def local_api_url() -> str:
    value = os.getenv("SENTINEL_API_URL", DEFAULT_API_URL).rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in LOCAL_HOSTS:
        raise ValueError("SENTINEL_API_URL must point to localhost, 127.0.0.1, or ::1.")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("SENTINEL_API_URL must be the local API origin, without a path or query.")
    return value


def api_get(base_url: str, path: str, **params):
    """Read data from the local FastAPI API; this dashboard makes GET calls only."""
    response = httpx.get(
        f"{base_url}{path}",
        params=params,
        timeout=(1, 5),
        follow_redirects=False,
    )
    response.raise_for_status()
    return response.json()


try:
    API_URL = local_api_url()
except ValueError as exc:
    st.error(str(exc))
    st.stop()

with st.sidebar:
    st.header("Local data source")
    st.caption(API_URL)
    st.caption("Read-only: this page uses GET calls to the FastAPI API.")
    if st.button("Refresh dashboard", width="stretch"):
        st.cache_data.clear()
        st.rerun()


@st.cache_data(ttl=20, show_spinner=False)
def load_dashboard_data(base_url: str) -> tuple[dict, dict]:
    paths = {
        "api_health": ("/health", {}),
        "services": ("/api/v1/services", {}),
        "health_scores": ("/api/v1/health/scores", {"limit": 1000}),
        "anomalies": ("/api/v1/anomalies", {"limit": 1000}),
        "incidents": ("/api/v1/incidents", {"limit": 1000}),
        "alerts": ("/api/v1/alerts", {"limit": 1000}),
    }
    data: dict = {}
    errors: dict = {}
    for name, (path, params) in paths.items():
        try:
            data[name] = api_get(base_url, path, **params)
        except httpx.HTTPError as exc:
            errors[name] = str(exc)
    return data, errors


with st.spinner("Reading the local SentinelAI API…"):
    dashboard_data, api_errors = load_dashboard_data(API_URL)

if "api_health" not in dashboard_data:
    st.error(f"Cannot reach the local API at {API_URL}. Start FastAPI first: {api_errors.get('api_health', 'request failed')}")
    st.stop()

if api_errors:
    with st.expander("Some API sections could not be loaded"):
        for section, error in api_errors.items():
            st.write(f"**{section}:** {error}")

services = dashboard_data.get("services", [])
health_scores = dashboard_data.get("health_scores", [])
anomalies = dashboard_data.get("anomalies", [])
incidents = dashboard_data.get("incidents", [])
alerts = dashboard_data.get("alerts", [])

open_incident_count = sum(1 for item in incidents if item.get("status") == "open")
summary = st.columns(4)
summary[0].metric("Services", len(services))
summary[1].metric("Open incidents", open_incident_count)
summary[2].metric("Anomalies", len(anomalies))
summary[3].metric("Simulated alerts", len(alerts))

st.subheader("Service health trends")
if health_scores:
    health_frame = pd.DataFrame(health_scores)
    health_frame["window_end"] = pd.to_datetime(health_frame["window_end"], utc=True, errors="coerce")
    health_frame["calculated_at"] = pd.to_datetime(health_frame["calculated_at"], utc=True, errors="coerce")
    health_frame = health_frame.dropna(subset=["window_end", "score"]).sort_values(["service", "window_end", "id"])
    if not health_frame.empty:
        health_chart = px.line(
            health_frame,
            x="window_end",
            y="score",
            color="service",
            markers=True,
            hover_data={"anomaly_count": True, "window_minutes": True, "calculated_at": True},
            labels={"window_end": "Latest event time", "score": "Health score", "service": "Service"},
        )
        health_chart.update_yaxes(range=[0, 100])
        health_chart.update_layout(legend_title_text="", margin=dict(l=10, r=10, t=20, b=10))
        st.plotly_chart(health_chart, width="stretch")
        latest = health_frame.sort_values(["service", "window_end", "id"]).groupby("service", as_index=False).tail(1)
        st.dataframe(
            latest[["service", "score", "anomaly_count", "window_minutes", "window_end"]],
            width="stretch",
            hide_index=True,
        )
    else:
        st.info("Health history is present, but no valid timestamps were returned.")
else:
    st.info("No stored health history yet. Ingest logs and run detection to create health snapshots.")

st.subheader("Anomalies")
if anomalies:
    anomaly_frame = pd.DataFrame(anomalies)
    anomaly_columns = [
        column for column in (
            "id", "timestamp", "affected_service", "detection_method", "metric",
            "observed_value", "expected_baseline", "score", "score_label", "severity",
        ) if column in anomaly_frame.columns
    ]
    st.dataframe(anomaly_frame[anomaly_columns], width="stretch", hide_index=True)
    anomaly_ids = [item["id"] for item in anomalies]
    selected_anomaly_id = st.selectbox("Inspect structured anomaly evidence", anomaly_ids)
    selected_anomaly = next(item for item in anomalies if item["id"] == selected_anomaly_id)
    st.json(selected_anomaly.get("evidence", {}))
else:
    st.info("No anomalies have been recorded.")

st.subheader("Incidents")
if incidents:
    incident_frame = pd.DataFrame(incidents)
    display_columns = [
        column for column in (
            "id", "service_group", "severity", "status", "first_seen", "last_seen",
            "anomaly_count", "anomaly_ids", "explanation",
        ) if column in incident_frame.columns
    ]
    st.dataframe(incident_frame[display_columns], width="stretch", hide_index=True)
    for incident in incidents:
        title = f"#{incident['id']} · {incident['severity'].upper()} · {incident['service_group']} · {incident['status']}"
        with st.expander(title):
            st.write(incident["explanation"])
            st.caption(f"Anomaly IDs: {', '.join(map(str, incident['anomaly_ids'])) or 'None'}")
else:
    st.info("No incidents have been recorded.")

st.subheader("Alert delivery history")
if alerts:
    alert_frame = pd.DataFrame(alerts)
    alert_columns = [
        column for column in ("id", "incident_id", "channel", "timestamp", "delivery_status")
        if column in alert_frame.columns
    ]
    st.dataframe(alert_frame[alert_columns], width="stretch", hide_index=True)
    alert_ids = [item["id"] for item in alerts]
    selected_alert_id = st.selectbox("Inspect simulated alert payload", alert_ids)
    selected_alert = next(item for item in alerts if item["id"] == selected_alert_id)
    st.json(selected_alert.get("payload", {}))
else:
    st.info("No simulated alert delivery attempts have been recorded.")

st.caption("Dashboard is read-only and local-only. Detection and incident evidence remain in the FastAPI service.")
