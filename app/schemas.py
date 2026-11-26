from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from pydantic import BaseModel, ConfigDict, Field, field_validator

LEVEL_ALIASES = {"warning": "warn", "fatal": "critical", "err": "error"}
VALID_LEVELS = {"debug", "info", "notice", "warn", "error", "critical"}


class LogEventCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timestamp: datetime | None = None
    service: str = Field(min_length=1, max_length=120)
    level: str = Field(min_length=1, max_length=16)
    message: str = Field(min_length=1, max_length=4000)
    latency_ms: float | None = Field(default=None, ge=0, le=3_600_000)
    trace_id: str | None = Field(default=None, max_length=160)
    attributes: dict[str, Any] = Field(default_factory=dict)

    @field_validator("service")
    @classmethod
    def clean_service(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("service must not be blank")
        return value

    @field_validator("level")
    @classmethod
    def clean_level(cls, value: str) -> str:
        normalized = value.strip().lower()
        normalized = LEVEL_ALIASES.get(normalized, normalized)
        if normalized not in VALID_LEVELS:
            raise ValueError(f"level must be one of: {', '.join(sorted(VALID_LEVELS))}")
        return normalized

    @field_validator("message")
    @classmethod
    def clean_message(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("message must not be blank")
        return value

    @field_validator("timestamp")
    @classmethod
    def timestamp_to_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class LogBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    events: list[LogEventCreate] = Field(min_length=1, max_length=1000)


class IngestResponse(BaseModel):
    accepted: int
    event_ids: list[int]
    service_counts: dict[str, int]
    ingested_at: str


class LogEventRead(BaseModel):
    id: int
    timestamp: str
    service: str
    level: str
    message: str
    latency_ms: float | None
    trace_id: str | None
    attributes: dict[str, Any]


class ServiceRead(BaseModel):
    service: str
    event_count: int
    last_seen: str


class AnomalyRead(BaseModel):
    id: int
    detection_method: str
    metric: str
    observed_value: float
    expected_baseline: float | None
    score: float | None
    score_label: str | None
    z_score: float | None
    isolation_forest_result: str | None
    severity: str
    affected_service: str
    timestamp: str
    evidence: dict[str, Any]


class DetectionRunResponse(BaseModel):
    buckets_evaluated: int
    anomalies_created: int
    anomalies_updated: int
    by_method: dict[str, int]
    incidents_opened: int = 0
    health_scores_recorded: int = 0


class IncidentRead(BaseModel):
    id: int
    service_group: str
    severity: str
    title: str
    status: str
    first_seen: str
    last_seen: str
    anomaly_count: int
    anomaly_ids: list[int]
    explanation: str


class ServiceHealthRead(BaseModel):
    id: int
    service: str
    score: int
    anomaly_count: int
    window_minutes: int
    window_end: str
    calculated_at: str


class LocalWebhookRequest(BaseModel):
    incident_id: int = Field(gt=0)
    payload: dict[str, Any] = Field(default_factory=dict)


class AlertRead(BaseModel):
    id: int
    incident_id: int
    channel: str
    timestamp: str
    delivery_status: str
    status_at_alert_time: str | None = None
    current_incident_status: str | None = None
    payload: dict[str, Any]


class AlertEvaluationResponse(BaseModel):
    created: int
    suppressed: int
    alerts: list[AlertRead]
