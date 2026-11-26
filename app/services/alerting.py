from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlmodel import Session, select

from app.alerts_config import ALERT_CHANNEL, ALERT_COOLDOWN_MINUTES, ALERT_DELIVERY_STATUS
from app.models import Incident, SimulatedAlert

logger = logging.getLogger("sentinelai.alerting")


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def incident_alert_payload(incident: Incident) -> dict[str, Any]:
    """Build a webhook payload exclusively from persisted incident fields."""
    return {
        "incident_id": incident.id,
        "service": incident.service_group,
        "severity": incident.severity,
        "title": incident.title,
        "status": incident.status,
        "first_seen": _utc(incident.first_seen).isoformat().replace("+00:00", "Z"),
        "last_seen": _utc(incident.last_seen).isoformat().replace("+00:00", "Z"),
        "anomaly_ids": json.loads(incident.anomaly_ids_json or "[]"),
        "explanation": incident.explanation,
    }


def record_local_webhook_attempt(
    session: Session,
    *,
    incident: Incident,
    payload: dict[str, Any],
    now: datetime | None = None,
) -> SimulatedAlert:
    """Persist and log a simulated local delivery attempt; performs no network I/O."""
    timestamp = _utc(now or datetime.now(timezone.utc))
    timestamp_text = timestamp.isoformat().replace("+00:00", "Z")
    dedupe_key = hashlib.sha256(
        f"{incident.id}|{ALERT_CHANNEL}|{timestamp_text}".encode("utf-8")
    ).hexdigest()
    existing = session.exec(select(SimulatedAlert).where(SimulatedAlert.dedupe_key == dedupe_key)).first()
    if existing is not None:
        return existing
    alert = SimulatedAlert(
        incident_id=int(incident.id),
        dedupe_key=dedupe_key,
        channel=ALERT_CHANNEL,
        destination="simulated://local-webhook",
        payload_json=json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False),
        delivery_status=ALERT_DELIVERY_STATUS,
        timestamp=timestamp,
        created_at=timestamp,
    )
    session.add(alert)
    session.flush()
    logger.info(
        "Recorded simulated local alert: incident_id=%s channel=%s status=%s",
        incident.id,
        alert.channel,
        alert.delivery_status,
    )
    return alert


def evaluate_open_incident_alerts(
    session: Session,
    *,
    now: datetime | None = None,
    cooldown_minutes: int = ALERT_COOLDOWN_MINUTES,
) -> tuple[list[SimulatedAlert], int]:
    """Alert once per open incident, then at most once per configured cooldown."""
    timestamp = _utc(now or datetime.now(timezone.utc))
    cooldown = timedelta(minutes=cooldown_minutes)
    incidents = session.exec(
        select(Incident).where(Incident.status == "open").order_by(Incident.last_seen, Incident.id)
    ).all()
    created: list[SimulatedAlert] = []
    suppressed = 0
    for incident in incidents:
        previous = session.exec(
            select(SimulatedAlert)
            .where(
                SimulatedAlert.incident_id == incident.id,
                SimulatedAlert.channel == ALERT_CHANNEL,
            )
            .order_by(SimulatedAlert.timestamp.desc(), SimulatedAlert.id.desc())
        ).first()
        if previous is not None and timestamp - _utc(previous.timestamp) < cooldown:
            suppressed += 1
            continue
        created.append(
            record_local_webhook_attempt(
                session,
                incident=incident,
                payload=incident_alert_payload(incident),
                now=timestamp,
            )
        )
    return created, suppressed
