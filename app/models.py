from __future__ import annotations

from datetime import datetime
from sqlmodel import Field, SQLModel
from sqlalchemy import Column, Text


class LogEvent(SQLModel, table=True):
    """Normalized source log event; timestamps are stored as naive UTC in SQLite."""

    id: int | None = Field(default=None, primary_key=True)
    timestamp: datetime = Field(index=True)
    service: str = Field(index=True, max_length=120)
    level: str = Field(index=True, max_length=16)
    message: str = Field(sa_column=Column(Text, nullable=False))
    latency_ms: float | None = Field(default=None, ge=0)
    trace_id: str | None = Field(default=None, max_length=160, index=True)
    attributes_json: str = Field(default="{}", sa_column=Column(Text, nullable=False))


class Incident(SQLModel, table=True):
    """Correlated anomaly group for one service and a bounded time window."""

    id: int | None = Field(default=None, primary_key=True)
    dedupe_key: str = Field(index=True, unique=True, max_length=200)
    service_group: str = Field(index=True, max_length=160)
    severity: str = Field(index=True, max_length=16)
    title: str = Field(max_length=240)
    status: str = Field(default="open", index=True, max_length=20)
    first_seen: datetime = Field(index=True)
    last_seen: datetime = Field(index=True)
    anomaly_count: int = Field(default=0, ge=0)
    anomaly_ids_json: str = Field(default="[]", sa_column=Column(Text, nullable=False))
    explanation: str = Field(default="", sa_column=Column(Text, nullable=False))


class Anomaly(SQLModel, table=True):
    """Structured evidence emitted by a deterministic detector."""

    id: int | None = Field(default=None, primary_key=True)
    incident_id: int | None = Field(default=None, foreign_key="incident.id", index=True)
    dedupe_key: str = Field(index=True, unique=True, max_length=64)
    detection_method: str = Field(index=True, max_length=40)
    metric: str = Field(index=True, max_length=40)
    observed_value: float
    expected_baseline: float | None = None
    z_score: float | None = None
    score: float | None = None
    score_label: str | None = Field(default=None, max_length=48)
    isolation_forest_result: str | None = Field(default=None, max_length=24)
    severity: str = Field(index=True, max_length=16)
    affected_service: str = Field(index=True, max_length=120)
    timestamp: datetime = Field(index=True)
    evidence_json: str = Field(default="{}", sa_column=Column(Text, nullable=False))


class SimulatedAlert(SQLModel, table=True):
    """Persisted record for a local simulated webhook delivery attempt."""

    id: int | None = Field(default=None, primary_key=True)
    incident_id: int = Field(foreign_key="incident.id", index=True)
    dedupe_key: str = Field(index=True, unique=True, max_length=200)
    channel: str = Field(default="local_webhook", index=True, max_length=80)
    destination: str = Field(default="simulated://local-webhook", max_length=240)
    payload_json: str = Field(sa_column=Column(Text, nullable=False))
    delivery_status: str = Field(default="simulated", max_length=24)
    timestamp: datetime = Field(index=True)
    created_at: datetime = Field(index=True)


class ServiceHealthSnapshot(SQLModel, table=True):
    """A point-in-time service health score for historical trend queries."""

    id: int | None = Field(default=None, primary_key=True)
    service: str = Field(index=True, max_length=120)
    score: int = Field(ge=0, le=100)
    anomaly_count: int = Field(ge=0)
    window_minutes: int = Field(gt=0)
    window_end: datetime = Field(index=True)
    calculated_at: datetime = Field(index=True)
