from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from sqlmodel import Session

from app.models import LogEvent
from app.schemas import LogBatch


def as_utc(value: datetime) -> datetime:
    """Normalize every timestamp to timezone-aware UTC before SQLModel persistence."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def ingest_batch(session: Session, batch: LogBatch, now: datetime | None = None) -> tuple[list[LogEvent], dict[str, int]]:
    """Persist one validated batch atomically and return stored rows plus per-service counts."""
    received_at = as_utc(now or datetime.now(timezone.utc))
    records: list[LogEvent] = []
    service_counts: Counter[str] = Counter()
    for item in batch.events:
        timestamp = as_utc(item.timestamp or received_at)
        records.append(
            LogEvent(
                timestamp=timestamp,
                service=item.service,
                level=item.level,
                message=item.message,
                latency_ms=item.latency_ms,
                trace_id=item.trace_id,
                attributes_json=json.dumps(item.attributes, ensure_ascii=False, separators=(",", ":")),
            )
        )
        service_counts[item.service] += 1
    try:
        session.add_all(records)
        session.commit()
        for record in records:
            session.refresh(record)
    except Exception:
        session.rollback()
        raise
    return records, dict(service_counts)


def to_utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
