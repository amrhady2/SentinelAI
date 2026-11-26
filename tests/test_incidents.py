from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

from sqlmodel import Session, SQLModel, create_engine, select

from app.models import Anomaly, Incident, LogEvent
from app.services.incidents import close_quiet_incidents, correlate_anomalies


def make_anomaly(service: str, timestamp: datetime, *, severity: str = "medium") -> Anomaly:
    evidence = {
        "detection_method": "rolling_zscore",
        "metric": "error_rate",
        "observed_value": 0.9,
        "expected_baseline": 0.1,
        "score": 4.0,
        "severity": severity,
        "affected_service": service,
        "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
    }
    return Anomaly(
        dedupe_key=f"{service}-{timestamp.isoformat()}",
        detection_method="rolling_zscore",
        metric="error_rate",
        observed_value=0.9,
        expected_baseline=0.1,
        score=4.0,
        score_label=None,
        severity=severity,
        affected_service=service,
        timestamp=timestamp,
        evidence_json=json.dumps(evidence),
    )


def make_session() -> Session:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_same_service_anomalies_within_window_join_one_incident() -> None:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    with make_session() as session:
        session.add_all([make_anomaly("payments", start), make_anomaly("payments", start + timedelta(minutes=3))])
        session.commit()
        result = correlate_anomalies(session, latest_event_time=start + timedelta(minutes=3), window_minutes=5)
        session.commit()
        incidents = session.exec(select(Incident)).all()
        anomalies = session.exec(select(Anomaly).order_by(Anomaly.timestamp)).all()

        assert result.incidents_opened == 1
        assert len(incidents) == 1
        assert incidents[0].status == "open"
        assert incidents[0].anomaly_count == 2
        assert anomalies[0].incident_id == anomalies[1].incident_id == incidents[0].id
        assert len(json.loads(incidents[0].anomaly_ids_json)) == 2
        assert "rolling_zscore" in incidents[0].explanation

        repeated = correlate_anomalies(session, latest_event_time=start + timedelta(minutes=3), window_minutes=5)
        assert repeated.incidents_opened == 0
        assert repeated.anomalies_attached == 0


def test_different_services_do_not_join_same_incident() -> None:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    with make_session() as session:
        session.add_all([make_anomaly("payments", start), make_anomaly("inventory", start)])
        session.commit()
        correlate_anomalies(session, latest_event_time=start, window_minutes=5)
        session.commit()
        incidents = session.exec(select(Incident)).all()
        assert len(incidents) == 2
        assert {item.service_group for item in incidents} == {"payments", "inventory"}


def test_correlation_window_boundary_is_inclusive() -> None:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    with make_session() as session:
        session.add_all([make_anomaly("payments", start), make_anomaly("payments", start + timedelta(minutes=5))])
        session.commit()
        result = correlate_anomalies(session, latest_event_time=start + timedelta(minutes=5), window_minutes=5)
        session.commit()
        incidents = session.exec(select(Incident)).all()
        assert result.incidents_opened == 1
        assert len(incidents) == 1
        assert incidents[0].anomaly_count == 2


def test_incident_closes_after_full_quiet_period() -> None:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    with make_session() as session:
        session.add(make_anomaly("payments", start))
        session.commit()
        correlate_anomalies(session, latest_event_time=start, window_minutes=5)
        session.commit()
        session.add(
            LogEvent(
                timestamp=start + timedelta(minutes=5, microseconds=-1),
                service="payments",
                level="info",
                message="event before quiet period boundary",
                latency_ms=100,
                attributes_json="{}",
            )
        )
        session.commit()
        assert close_quiet_incidents(session, window_minutes=5) == 0
        session.commit()
        still_open = session.exec(select(Incident)).one()
        assert still_open.status == "open"

        session.add(
            LogEvent(
                timestamp=start + timedelta(minutes=5),
                service="payments",
                level="info",
                message="event at quiet period boundary",
                latency_ms=100,
                attributes_json="{}",
            )
        )
        session.commit()
        assert close_quiet_incidents(session, window_minutes=5) == 1
        session.commit()
        incident = session.exec(select(Incident)).one()
        assert incident.status == "closed"
