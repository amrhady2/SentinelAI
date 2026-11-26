from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine
import pytest

from app import main as main_module
from app.database import get_session
from app.models import Incident, LogEvent


@pytest.fixture
def api_client(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(main_module, "create_db_and_tables", lambda: None)

    def override_session():
        with Session(engine) as session:
            yield session

    main_module.app.dependency_overrides[get_session] = override_session
    try:
        with TestClient(main_module.app) as client:
            yield client, engine
    finally:
        main_module.app.dependency_overrides.clear()
        engine.dispose()


def test_all_api_routes_cover_empty_reads_and_validation(api_client) -> None:
    client, _engine = api_client
    assert client.get("/health").status_code == 200
    assert client.get("/api/v1/logs").json() == []
    assert client.get("/api/v1/services").json() == []
    assert client.get("/api/v1/anomalies").json() == []
    assert client.get("/api/v1/incidents").json() == []
    assert client.get("/api/v1/health/scores").json() == []
    assert client.get("/api/v1/alerts").json() == []

    empty_detection = client.post("/api/v1/detection/run")
    assert empty_detection.status_code == 200
    assert empty_detection.json()["buckets_evaluated"] == 0
    evaluated = client.post("/api/v1/alerts/evaluate")
    assert evaluated.status_code == 200
    assert evaluated.json() == {"created": 0, "suppressed": 0, "alerts": []}
    assert client.post("/api/v1/webhooks/local", json={"incident_id": 999, "payload": {}}).status_code == 404
    assert client.get("/api/v1/incidents", params={"status": "unknown"}).status_code == 422


def test_ingest_detect_and_read_back_anomalies_incidents_health_and_alerts(api_client) -> None:
    client, _engine = api_client
    latest_minute = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    events = []
    for minute in range(31):
        timestamp = latest_minute - timedelta(minutes=30 - minute)
        is_spike = minute == 30
        for index in range(10):
            events.append(
                {
                    "timestamp": (timestamp + timedelta(seconds=index)).isoformat(),
                    "service": "payments",
                    "level": "error" if is_spike else "info",
                    "message": "error spike" if is_spike else "normal request",
                    "latency_ms": 1800 if is_spike else 100,
                }
            )

    ingested = client.post("/api/v1/logs/ingest", json={"events": events})
    assert ingested.status_code == 201, ingested.text
    assert ingested.json()["accepted"] == len(events)
    assert ingested.json()["service_counts"] == {"payments": len(events)}

    logs = client.get("/api/v1/logs", params={"service": "payments", "level": "err", "limit": 20})
    assert logs.status_code == 200
    assert len(logs.json()) == 10
    assert all(row["level"] == "error" for row in logs.json())
    services = client.get("/api/v1/services")
    assert services.status_code == 200
    assert services.json()[0]["event_count"] == len(events)

    detected = client.post("/api/v1/detection/run")
    assert detected.status_code == 200, detected.text
    assert detected.json()["anomalies_created"] > 0
    assert detected.json()["incidents_opened"] > 0
    assert detected.json()["health_scores_recorded"] == 1

    anomalies = client.get("/api/v1/anomalies", params={"service": "payments", "limit": 1000})
    assert anomalies.status_code == 200
    assert anomalies.json()
    assert all(
        {"detection_method", "observed_value", "expected_baseline", "score", "severity", "affected_service", "timestamp"} <= item.keys()
        for item in anomalies.json()
    )
    incidents = client.get("/api/v1/incidents", params={"service": "payments", "status": "open"})
    assert incidents.status_code == 200
    assert len(incidents.json()) == 1
    assert incidents.json()[0]["anomaly_ids"]
    assert incidents.json()[0]["explanation"]

    health = client.get("/api/v1/health/scores", params={"service": "payments", "limit": 10})
    assert health.status_code == 200
    assert health.json()
    expected_window_end = (latest_minute + timedelta(seconds=9)).isoformat().replace("+00:00", "Z")
    assert health.json()[0]["window_end"] == expected_window_end

    evaluation = client.post("/api/v1/alerts/evaluate")
    assert evaluation.status_code == 200, evaluation.text
    assert evaluation.json()["created"] == 1
    alerts = client.get("/api/v1/alerts", params={"incident_id": incidents.json()[0]["id"], "channel": "local_webhook"})
    assert alerts.status_code == 200
    assert len(alerts.json()) == 1
    assert alerts.json()[0]["delivery_status"] == "simulated"


def test_incident_closure_uses_latest_event_time_not_wall_clock(api_client) -> None:
    client, engine = api_client
    incident_last_seen = datetime(2026, 1, 1, 16, 1, tzinfo=timezone.utc)
    latest_event_time = datetime(2026, 1, 1, 16, 1, 59, tzinfo=timezone.utc)
    with Session(engine) as session:
        session.add(
            Incident(
                dedupe_key="event-time-closure-regression",
                service_group="payments",
                severity="medium",
                title="medium anomaly in payments",
                status="open",
                first_seen=incident_last_seen,
                last_seen=incident_last_seen,
                anomaly_count=1,
                anomaly_ids_json="[1]",
                explanation="evidence-based incident",
            )
        )
        session.add(
            LogEvent(
                timestamp=latest_event_time,
                service="payments",
                level="info",
                message="latest event",
                latency_ms=100,
                attributes_json="{}",
            )
        )
        session.commit()

    response = client.get("/api/v1/incidents", params={"service": "payments"})
    assert response.status_code == 200, response.text
    assert response.json()[0]["status"] == "open"
