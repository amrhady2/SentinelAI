from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable

from app.analytics_config import (
    HEALTH_SCORE_BASELINE,
    HEALTH_SCORE_CEILING,
    HEALTH_SCORE_FLOOR,
    HEALTH_WINDOW_MINUTES,
    SEVERITY_WEIGHTS,
)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def calculate_service_health_scores(
    services: Iterable[str],
    anomalies: Iterable[object],
    *,
    now: datetime | None = None,
    window_minutes: int = HEALTH_WINDOW_MINUTES,
) -> dict[str, tuple[int, int]]:
    """Return service -> (health score, in-window anomaly count), inclusive at cutoff."""
    now_utc = _utc(now or datetime.now(timezone.utc))
    cutoff = now_utc - timedelta(minutes=window_minutes)
    service_names = list(dict.fromkeys(services))
    totals = {service: 0 for service in service_names}
    counts = {service: 0 for service in service_names}
    for anomaly in anomalies:
        service = str(anomaly.affected_service)
        timestamp = _utc(anomaly.timestamp)
        if service not in totals or timestamp < cutoff or timestamp > now_utc:
            continue
        weight = SEVERITY_WEIGHTS.get(str(anomaly.severity).lower(), 0)
        totals[service] += weight
        counts[service] += 1
    return {
        service: (
            max(HEALTH_SCORE_FLOOR, min(HEALTH_SCORE_CEILING, HEALTH_SCORE_BASELINE - totals[service])),
            counts[service],
        )
        for service in totals
    }
