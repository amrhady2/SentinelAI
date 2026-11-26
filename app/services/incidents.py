from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from app.analytics_config import (
    CORRELATION_WINDOW_MINUTES,
    INCIDENT_EXPLANATION_TEMPLATE,
    INCIDENT_TITLE_TEMPLATE,
    SEVERITY_WEIGHTS,
)
from app.models import Anomaly, Incident, LogEvent
from app.services.ingestion import to_utc_iso


@dataclass(frozen=True)
class CorrelationSummary:
    incidents_opened: int
    anomalies_attached: int
    incidents_closed: int


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _incident_key(service: str, first_seen: datetime) -> str:
    raw = f"{service}|{to_utc_iso(_utc(first_seen))}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _evidence_field(anomaly: Anomaly, field: str) -> object:
    evidence = json.loads(anomaly.evidence_json or "{}")
    return evidence.get(field)


def _refresh_incident_summary(session: Session, incident: Incident, anomaly_ids: list[int]) -> None:
    rows = session.exec(select(Anomaly).where(Anomaly.id.in_(anomaly_ids))).all()
    rows_by_id = {int(row.id): row for row in rows if row.id is not None}
    ordered = [rows_by_id[anomaly_id] for anomaly_id in anomaly_ids if anomaly_id in rows_by_id]
    if not ordered:
        return

    newest = max(ordered, key=lambda row: _utc(row.timestamp))
    incident.anomaly_ids_json = json.dumps(anomaly_ids, separators=(",", ":"))
    incident.anomaly_count = len(anomaly_ids)
    incident.first_seen = min((_utc(row.timestamp) for row in ordered))
    incident.last_seen = max((_utc(row.timestamp) for row in ordered))
    incident.severity = max(
        (row.severity for row in ordered),
        key=lambda severity: SEVERITY_WEIGHTS.get(severity.lower(), 0),
    )
    incident.title = INCIDENT_TITLE_TEMPLATE.format(
        severity=incident.severity,
        service=incident.service_group,
    )
    incident.explanation = INCIDENT_EXPLANATION_TEMPLATE.format(
        service=incident.service_group,
        count=len(anomaly_ids),
        detection_method=_evidence_field(newest, "detection_method"),
        metric=_evidence_field(newest, "metric"),
        observed_value=_evidence_field(newest, "observed_value"),
        expected_baseline=_evidence_field(newest, "expected_baseline"),
        score=_evidence_field(newest, "score"),
        severity=_evidence_field(newest, "severity"),
        timestamp=_evidence_field(newest, "timestamp"),
    )
    session.add(incident)


def close_quiet_incidents(
    session: Session,
    *,
    latest_event_time: datetime | None = None,
    window_minutes: int = CORRELATION_WINDOW_MINUTES,
) -> int:
    """Close incidents using global event time, never elapsed wall-clock time."""
    if latest_event_time is None:
        latest_event_time = session.exec(
            select(LogEvent.timestamp)
            .order_by(LogEvent.timestamp.desc(), LogEvent.id.desc())
            .limit(1)
        ).first()
    if latest_event_time is None:
        return 0

    reference_time = _utc(latest_event_time)
    quiet_period = timedelta(minutes=window_minutes)
    open_incidents = session.exec(select(Incident).where(Incident.status == "open")).all()
    closed = 0
    for incident in open_incidents:
        if reference_time - _utc(incident.last_seen) >= quiet_period:
            incident.status = "closed"
            session.add(incident)
            closed += 1
    return closed


def correlate_anomalies(
    session: Session,
    *,
    latest_event_time: datetime | None = None,
    window_minutes: int = CORRELATION_WINDOW_MINUTES,
) -> CorrelationSummary:
    """Attach uncorrelated anomalies to per-service incidents in timestamp order."""
    window = timedelta(minutes=window_minutes)
    pending = session.exec(
        select(Anomaly)
        .where(Anomaly.incident_id.is_(None))
        .order_by(Anomaly.timestamp, Anomaly.id)
    ).all()
    opened = attached = 0

    for anomaly in pending:
        timestamp = _utc(anomaly.timestamp)
        active = session.exec(
            select(Incident)
            .where(Incident.service_group == anomaly.affected_service, Incident.status == "open")
            .order_by(Incident.last_seen.desc(), Incident.id.desc())
        ).first()

        should_join = False
        if active is not None:
            first_seen, last_seen = _utc(active.first_seen), _utc(active.last_seen)
            if first_seen <= timestamp <= last_seen:
                should_join = True
            elif timestamp >= last_seen and timestamp - last_seen <= window:
                should_join = True
            elif timestamp < first_seen and first_seen - timestamp <= window:
                should_join = True

        if active is None or not should_join:
            if active is not None:
                active.status = "closed"
                session.add(active)
            # Deterministic key lookup also makes late/backfilled records
            # idempotent when an incident with the same opening timestamp exists.
            existing = session.exec(
                select(Incident).where(
                    Incident.dedupe_key == _incident_key(anomaly.affected_service, timestamp)
                )
            ).first()
            if existing is not None:
                active = existing
                should_join = True

        if active is None or not should_join:
            active = Incident(
                dedupe_key=_incident_key(anomaly.affected_service, timestamp),
                service_group=anomaly.affected_service,
                severity=anomaly.severity,
                title="",
                status="open",
                first_seen=timestamp,
                last_seen=timestamp,
                anomaly_count=0,
                anomaly_ids_json="[]",
                explanation="",
            )
            session.add(active)
            session.flush()
            opened += 1

        ids = json.loads(active.anomaly_ids_json or "[]")
        anomaly_id = int(anomaly.id)
        if anomaly_id not in ids:
            ids.append(anomaly_id)
            _refresh_incident_summary(session, active, ids)
            anomaly.incident_id = active.id
            session.add(anomaly)
            attached += 1

    closed = close_quiet_incidents(session, latest_event_time=latest_event_time, window_minutes=window_minutes)
    return CorrelationSummary(opened, attached, closed)
