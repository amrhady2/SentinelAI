from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query
import pandas as pd
from sqlmodel import Session, select

from app.analytics_config import HEALTH_WINDOW_MINUTES
from app.alerts_config import ALERT_COOLDOWN_MINUTES
from app.config import settings
from app.database import create_db_and_tables, get_session
from app.models import Anomaly, Incident, LogEvent, ServiceHealthSnapshot, SimulatedAlert
from app.schemas import (
    AlertEvaluationResponse,
    AlertRead,
    AnomalyRead,
    DetectionRunResponse,
    IngestResponse,
    IncidentRead,
    LogBatch,
    LogEventRead,
    LocalWebhookRequest,
    ServiceHealthRead,
    ServiceRead,
)
from app.services.detection import (
    AnomalyEvidence,
    anomaly_dedupe_key,
    isolation_forest_detect,
    rolling_zscore_detect,
)
from app.services.ingestion import ingest_batch, to_utc_iso
from app.services.health import calculate_service_health_scores
from app.services.alerting import evaluate_open_incident_alerts, record_local_webhook_attempt
from app.services.incidents import close_quiet_incidents, correlate_anomalies
from app.services.metrics import build_bucket_metrics


@asynccontextmanager
async def lifespan(_: FastAPI):
    create_db_and_tables()
    yield


app = FastAPI(
    title=settings.app_name,
    description="Local-first log ingestion and deterministic statistical anomaly detection. No LLM is used for detection.",
    version=settings.version,
    lifespan=lifespan,
)


def _anomaly_read(row: Anomaly) -> AnomalyRead:
    return AnomalyRead(
        id=int(row.id),
        detection_method=row.detection_method,
        metric=row.metric,
        observed_value=row.observed_value,
        expected_baseline=row.expected_baseline,
        score=row.score,
        score_label=row.score_label,
        z_score=row.z_score,
        isolation_forest_result=row.isolation_forest_result,
        severity=row.severity,
        affected_service=row.affected_service,
        timestamp=to_utc_iso(row.timestamp),
        evidence=json.loads(row.evidence_json),
    )


def _alert_read(row: SimulatedAlert) -> AlertRead:
    return AlertRead(
        id=int(row.id),
        incident_id=row.incident_id,
        channel=row.channel,
        timestamp=to_utc_iso(row.timestamp),
        delivery_status=row.delivery_status,
        payload=json.loads(row.payload_json),
    )


def _persist_anomaly(session: Session, evidence: AnomalyEvidence) -> bool:
    key = anomaly_dedupe_key(evidence)
    row = session.exec(select(Anomaly).where(Anomaly.dedupe_key == key)).first()
    created = row is None
    if row is None:
        row = Anomaly(dedupe_key=key)
    row.detection_method = evidence.detection_method
    row.metric = evidence.metric
    row.observed_value = evidence.observed_value
    row.expected_baseline = evidence.expected_baseline
    row.score = evidence.score
    row.score_label = evidence.score_label
    row.z_score = evidence.z_score
    row.isolation_forest_result = evidence.isolation_forest_result
    row.severity = evidence.severity
    row.affected_service = evidence.affected_service
    row.timestamp = evidence.timestamp
    row.evidence_json = json.dumps(evidence.as_dict(), ensure_ascii=False, allow_nan=False)
    session.add(row)
    return created


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "sentinelai-api", "time": to_utc_iso(datetime.now(timezone.utc))}


@app.post("/api/v1/logs/ingest", response_model=IngestResponse, status_code=201)
def ingest_logs(batch: LogBatch, session: Session = Depends(get_session)) -> IngestResponse:
    records, service_counts = ingest_batch(session, batch)
    event_ids = [int(record.id) for record in records if record.id is not None]
    return IngestResponse(
        accepted=len(records),
        event_ids=event_ids,
        service_counts=service_counts,
        ingested_at=to_utc_iso(datetime.now(timezone.utc)),
    )


@app.get("/api/v1/logs", response_model=list[LogEventRead])
def list_logs(
    service: str | None = None,
    level: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
) -> list[LogEventRead]:
    statement = select(LogEvent).order_by(LogEvent.timestamp.desc(), LogEvent.id.desc())
    if service:
        statement = statement.where(LogEvent.service == service.strip())
    if level:
        normalized = level.strip().lower()
        normalized = {"warning": "warn", "fatal": "critical", "err": "error"}.get(normalized, normalized)
        statement = statement.where(LogEvent.level == normalized)
    rows = session.exec(statement.offset(offset).limit(limit)).all()
    return [
        LogEventRead(
            id=int(row.id),
            timestamp=to_utc_iso(row.timestamp),
            service=row.service,
            level=row.level,
            message=row.message,
            latency_ms=row.latency_ms,
            trace_id=row.trace_id,
            attributes=json.loads(row.attributes_json),
        )
        for row in rows
    ]


@app.get("/api/v1/services", response_model=list[ServiceRead])
def list_services(session: Session = Depends(get_session)) -> list[ServiceRead]:
    rows = session.exec(select(LogEvent).order_by(LogEvent.timestamp.desc())).all()
    summaries: dict[str, dict[str, Any]] = {}
    for row in rows:
        summary = summaries.setdefault(row.service, {"event_count": 0, "last_seen": row.timestamp})
        summary["event_count"] += 1
        if row.timestamp > summary["last_seen"]:
            summary["last_seen"] = row.timestamp
    return [
        ServiceRead(service=service, event_count=data["event_count"], last_seen=to_utc_iso(data["last_seen"]))
        for service, data in sorted(summaries.items())
    ]


@app.post("/api/v1/detection/run", response_model=DetectionRunResponse)
def run_detection(session: Session = Depends(get_session)) -> DetectionRunResponse:
    rows = session.exec(select(LogEvent).order_by(LogEvent.timestamp)).all()
    if not rows:
        return DetectionRunResponse(buckets_evaluated=0, anomalies_created=0, anomalies_updated=0, by_method={})
    raw_events = [
        {
            "timestamp": row.timestamp,
            "service": row.service,
            "level": row.level,
            "latency_ms": row.latency_ms,
        }
        for row in rows
    ]
    metrics = build_bucket_metrics(pd.DataFrame(raw_events))
    evidence = rolling_zscore_detect(metrics) + isolation_forest_detect(metrics)
    created = updated = 0
    by_method: dict[str, int] = {}
    for anomaly in evidence:
        by_method[anomaly.detection_method] = by_method.get(anomaly.detection_method, 0) + 1
        if _persist_anomaly(session, anomaly):
            created += 1
        else:
            updated += 1

    session.flush()
    now = datetime.now(timezone.utc)
    correlation = correlate_anomalies(session, now=now)
    stored_anomalies = session.exec(select(Anomaly)).all()
    services = sorted({row.service for row in rows})
    latest_event_timestamp = max(row.timestamp for row in rows)
    health_scores = calculate_service_health_scores(
        services,
        stored_anomalies,
        now=latest_event_timestamp,
        window_minutes=HEALTH_WINDOW_MINUTES,
    )
    for service, (score, anomaly_count) in health_scores.items():
        session.add(
            ServiceHealthSnapshot(
                service=service,
                score=score,
                anomaly_count=anomaly_count,
                window_minutes=HEALTH_WINDOW_MINUTES,
                window_end=latest_event_timestamp,
                calculated_at=now,
            )
        )
    session.commit()
    return DetectionRunResponse(
        buckets_evaluated=len(metrics),
        anomalies_created=created,
        anomalies_updated=updated,
        by_method=by_method,
        incidents_opened=correlation.incidents_opened,
        health_scores_recorded=len(health_scores),
    )


@app.get("/api/v1/anomalies", response_model=list[AnomalyRead])
def list_anomalies(
    service: str | None = None,
    detection_method: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
) -> list[AnomalyRead]:
    statement = select(Anomaly).order_by(Anomaly.timestamp.desc(), Anomaly.id.desc())
    if service:
        statement = statement.where(Anomaly.affected_service == service.strip())
    if detection_method:
        statement = statement.where(Anomaly.detection_method == detection_method.strip())
    rows = session.exec(statement.offset(offset).limit(limit)).all()
    return [_anomaly_read(row) for row in rows]


@app.get("/api/v1/incidents", response_model=list[IncidentRead])
def list_incidents(
    service: str | None = None,
    status: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
) -> list[IncidentRead]:
    closed = close_quiet_incidents(session)
    if closed:
        session.commit()
    statement = select(Incident).order_by(Incident.last_seen.desc(), Incident.id.desc())
    if service:
        statement = statement.where(Incident.service_group == service.strip())
    if status:
        normalized = status.strip().lower()
        if normalized not in {"open", "closed"}:
            raise HTTPException(status_code=422, detail="status must be 'open' or 'closed'")
        statement = statement.where(Incident.status == normalized)
    rows = session.exec(statement.offset(offset).limit(limit)).all()
    return [
        IncidentRead(
            id=int(row.id),
            service_group=row.service_group,
            severity=row.severity,
            title=row.title,
            status=row.status,
            first_seen=to_utc_iso(row.first_seen),
            last_seen=to_utc_iso(row.last_seen),
            anomaly_count=row.anomaly_count,
            anomaly_ids=json.loads(row.anomaly_ids_json),
            explanation=row.explanation,
        )
        for row in rows
    ]


@app.get("/api/v1/health/scores", response_model=list[ServiceHealthRead])
def list_health_scores(
    service: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
) -> list[ServiceHealthRead]:
    statement = select(ServiceHealthSnapshot).order_by(
        ServiceHealthSnapshot.calculated_at.desc(), ServiceHealthSnapshot.id.desc()
    )
    if service:
        statement = statement.where(ServiceHealthSnapshot.service == service.strip())
    rows = session.exec(statement.offset(offset).limit(limit)).all()
    return [
        ServiceHealthRead(
            id=int(row.id),
            service=row.service,
            score=row.score,
            anomaly_count=row.anomaly_count,
            window_minutes=row.window_minutes,
            window_end=to_utc_iso(row.window_end),
            calculated_at=to_utc_iso(row.calculated_at),
        )
        for row in rows
    ]


@app.post("/api/v1/alerts/evaluate", response_model=AlertEvaluationResponse)
def evaluate_alerts(session: Session = Depends(get_session)) -> AlertEvaluationResponse:
    close_quiet_incidents(session)
    alerts, suppressed = evaluate_open_incident_alerts(
        session,
        cooldown_minutes=ALERT_COOLDOWN_MINUTES,
    )
    session.commit()
    return AlertEvaluationResponse(
        created=len(alerts),
        suppressed=suppressed,
        alerts=[_alert_read(row) for row in alerts],
    )


@app.post("/api/v1/webhooks/local", response_model=AlertRead, status_code=201)
def record_local_webhook(
    request: LocalWebhookRequest,
    session: Session = Depends(get_session),
) -> AlertRead:
    incident = session.get(Incident, request.incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="incident not found")
    alert = record_local_webhook_attempt(
        session,
        incident=incident,
        payload=request.payload,
        now=datetime.now(timezone.utc),
    )
    session.commit()
    session.refresh(alert)
    return _alert_read(alert)


@app.get("/api/v1/alerts", response_model=list[AlertRead])
def list_alerts(
    incident_id: int | None = None,
    channel: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
) -> list[AlertRead]:
    statement = select(SimulatedAlert).order_by(
        SimulatedAlert.timestamp.desc(), SimulatedAlert.id.desc()
    )
    if incident_id is not None:
        statement = statement.where(SimulatedAlert.incident_id == incident_id)
    if channel:
        statement = statement.where(SimulatedAlert.channel == channel.strip())
    rows = session.exec(statement.offset(offset).limit(limit)).all()
    return [_alert_read(row) for row in rows]
