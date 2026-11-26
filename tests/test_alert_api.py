from __future__ import annotations

from datetime import datetime, timezone
import socket

from fastapi.testclient import TestClient
import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app import main as main_module
from app.database import get_session
from app.models import Incident, LogEvent, ServiceHealthSnapshot, SimulatedAlert


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


def test_local_webhook_endpoint_records_simulated_attempt(api_client, monkeypatch) -> None:
    client, engine = api_client
    timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with Session(engine) as session:
        incident = Incident(
            dedupe_key="webhook-endpoint-incident",
            service_group="payments",
            severity="medium",
            title="medium anomaly in payments",
            status="open",
            first_seen=timestamp,
            last_seen=timestamp,
            anomaly_count=0,
            anomaly_ids_json="[]",
            explanation="stored evidence summary",
        )
        session.add(incident)
        session.commit()
        session.refresh(incident)
        incident_id = incident.id

    outbound_attempts = []

    def reject_outbound_connect(*args, **kwargs):
        outbound_attempts.append((args, kwargs))
        raise AssertionError("simulated webhook attempted an outbound connection")

    monkeypatch.setattr(socket.socket, "connect", reject_outbound_connect)
    response = client.post(
        "/api/v1/webhooks/local",
        json={"incident_id": incident_id, "payload": {"source": "test", "value": 7}},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["incident_id"] == incident_id
    assert body["channel"] == "local_webhook"
    assert body["delivery_status"] == "simulated"
    assert body["payload"] == {"source": "test", "value": 7}
    assert outbound_attempts == []

    listed = client.get("/api/v1/alerts", params={"incident_id": incident_id})
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    with Session(engine) as session:
        stored = session.exec(select(SimulatedAlert)).one()
        assert stored.incident_id == incident_id
        assert stored.channel == "local_webhook"
        assert stored.delivery_status == "simulated"
        assert stored.timestamp is not None


def test_alert_evaluation_endpoint_deduplicates_same_open_incident(api_client) -> None:
    client, engine = api_client
    timestamp = datetime.now(timezone.utc)
    with Session(engine) as session:
        session.add(
            Incident(
                dedupe_key="alert-evaluation-incident",
                service_group="payments",
                severity="medium",
                title="medium anomaly in payments",
                status="open",
                first_seen=timestamp,
                last_seen=timestamp,
                anomaly_count=0,
                anomaly_ids_json="[]",
                explanation="stored evidence summary",
            )
        )
        session.commit()

    first = client.post("/api/v1/alerts/evaluate")
    second = client.post("/api/v1/alerts/evaluate")
    assert first.status_code == 200 and first.json()["created"] == 1
    assert second.status_code == 200 and second.json()["created"] == 0
    assert second.json()["suppressed"] == 1
    listed = client.get("/api/v1/alerts")
    assert listed.status_code == 200 and len(listed.json()) == 1


def test_health_window_anchor_is_latest_event_timestamp(api_client) -> None:
    client, engine = api_client
    first = datetime(2024, 1, 1, 10, 0, tzinfo=timezone.utc)
    latest = datetime(2024, 1, 1, 10, 7, 30, tzinfo=timezone.utc)
    with Session(engine) as session:
        session.add_all(
            [
                LogEvent(
                    timestamp=first,
                    service="payments",
                    level="info",
                    message="earlier",
                    latency_ms=100,
                    attributes_json="{}",
                ),
                LogEvent(
                    timestamp=latest,
                    service="payments",
                    level="info",
                    message="latest",
                    latency_ms=100,
                    attributes_json="{}",
                ),
            ]
        )
        session.commit()

    response = client.post("/api/v1/detection/run")
    assert response.status_code == 200, response.text
    health = client.get("/api/v1/health/scores", params={"service": "payments", "limit": 1})
    assert health.status_code == 200
    assert health.json()[0]["window_end"] == "2024-01-01T10:07:30Z"
    with Session(engine) as session:
        snapshot = session.exec(select(ServiceHealthSnapshot)).one()
        assert snapshot.window_end.replace(tzinfo=timezone.utc) == latest
