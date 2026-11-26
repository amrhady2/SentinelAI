"""Approved detector settings, wired into step 2; no detector runs in step 1."""

import os

BUCKET_SECONDS = 60
ROLLING_WINDOW_BUCKETS = 30
Z_SCORE_THRESHOLD = 3.0
MIN_HISTORY_BUCKETS = 30
ISOLATION_FOREST_RANDOM_STATE = 42


def _isolation_forest_contamination() -> str | float:
    raw = os.getenv("SENTINEL_IFOREST_CONTAMINATION", "auto").strip().lower()
    if raw == "auto":
        return "auto"
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError("SENTINEL_IFOREST_CONTAMINATION must be 'auto' or a float in (0, 0.5].") from exc
    if not 0.0 < value <= 0.5:
        raise ValueError("SENTINEL_IFOREST_CONTAMINATION must be 'auto' or a float in (0, 0.5].")
    return value


ISOLATION_FOREST_CONTAMINATION = _isolation_forest_contamination()
