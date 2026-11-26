from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlmodel import Session, SQLModel, create_engine, select

from app.models import Incident, SimulatedAlert
from app.services.alerting import evaluate_open_incident_alerts


def make_open_incident(session: Session) -> Incident:
    timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    incident = Incident(
        dedupe_key="alert-test-incident",
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
    return incident


def make_session() -> Session:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_same_open_incident_is_deduplicated_before_cooldown() -> None:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with make_session() as session:
        make_open_incident(session)
        first, first_suppressed = evaluate_open_incident_alerts(
            session, now=start, cooldown_minutes=15
        )
        session.commit()
        second, second_suppressed = evaluate_open_incident_alerts(
            session, now=start, cooldown_minutes=15
        )
        session.commit()
        saved = session.exec(select(SimulatedAlert)).all()

        assert len(first) == 1
        assert first_suppressed == 0
        assert second == []
        assert second_suppressed == 1
        assert len(saved) == 1


def test_repeat_alert_allowed_at_configured_cooldown_boundary() -> None:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with make_session() as session:
        make_open_incident(session)
        first, _ = evaluate_open_incident_alerts(session, now=start, cooldown_minutes=15)
        session.commit()
        before, suppressed = evaluate_open_incident_alerts(
            session, now=start + timedelta(minutes=14), cooldown_minutes=15
        )
        session.commit()
        after, after_suppressed = evaluate_open_incident_alerts(
            session, now=start + timedelta(minutes=15), cooldown_minutes=15
        )
        session.commit()

        assert len(first) == 1
        assert before == [] and suppressed == 1
        assert len(after) == 1 and after_suppressed == 0
        assert after[0].timestamp == start + timedelta(minutes=15)
        assert len(session.exec(select(SimulatedAlert)).all()) == 2



def test_alert_cooldown_is_configurable() -> None:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with make_session() as session:
        make_open_incident(session)
        first, _ = evaluate_open_incident_alerts(session, now=start, cooldown_minutes=3)
        session.commit()
        repeat, suppressed = evaluate_open_incident_alerts(
            session, now=start + timedelta(minutes=3), cooldown_minutes=3
        )
        assert len(first) == 1
        assert len(repeat) == 1
        assert suppressed == 0
