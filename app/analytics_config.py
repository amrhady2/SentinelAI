"""Configuration for incident correlation and health scoring."""

from __future__ import annotations

import os


def _positive_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


CORRELATION_WINDOW_MINUTES = _positive_int("SENTINEL_CORRELATION_WINDOW_MINUTES", 5)
HEALTH_WINDOW_MINUTES = _positive_int("SENTINEL_HEALTH_WINDOW_MINUTES", 60)
SEVERITY_WEIGHTS = {"low": 2, "medium": 5, "high": 10, "critical": 20}
HEALTH_SCORE_BASELINE = 100
HEALTH_SCORE_FLOOR = 0
HEALTH_SCORE_CEILING = 100

INCIDENT_TITLE_TEMPLATE = "{severity} anomaly in {service}"
INCIDENT_EXPLANATION_TEMPLATE = (
    "{service}: {count} anomaly record(s). Latest stored evidence: "
    "{detection_method} detected {metric} at {observed_value}; "
    "baseline {expected_baseline}; score {score}; severity {severity}; "
    "observed at {timestamp}."
)
