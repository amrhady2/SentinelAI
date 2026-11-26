from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.services.health import calculate_service_health_scores


def anomaly(service: str, severity: str, timestamp: datetime) -> SimpleNamespace:
    return SimpleNamespace(affected_service=service, severity=severity, timestamp=timestamp)


def test_no_anomalies_gives_full_health_score() -> None:
    now = datetime(2025, 1, 1, tzinfo=timezone.utc)
    assert calculate_service_health_scores(["payments"], [], now=now) == {"payments": (100, 0)}


def test_health_score_clamps_at_zero() -> None:
    now = datetime(2025, 1, 1, tzinfo=timezone.utc)
    anomalies = [anomaly("payments", "critical", now) for _ in range(6)]
    assert calculate_service_health_scores(["payments"], anomalies, now=now) == {"payments": (0, 6)}


def test_health_window_includes_cutoff_and_excludes_older_anomaly() -> None:
    now = datetime(2025, 1, 1, tzinfo=timezone.utc)
    anomalies = [
        anomaly("payments", "low", now - timedelta(minutes=60)),
        anomaly("payments", "critical", now - timedelta(minutes=60, microseconds=1)),
    ]
    assert calculate_service_health_scores(["payments"], anomalies, now=now, window_minutes=60) == {
        "payments": (98, 1)
    }
