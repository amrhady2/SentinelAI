from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from app.detection_config import MIN_HISTORY_BUCKETS, ROLLING_WINDOW_BUCKETS, Z_SCORE_THRESHOLD
from app.services.detection import isolation_forest_detect, rolling_zscore_detect
from app.services.metrics import build_bucket_metrics


def make_metrics(count: int, *, current: dict | None = None) -> pd.DataFrame:
    start = pd.Timestamp("2025-01-01T00:00:00Z")
    rows = []
    for index in range(count):
        row = {
            "service": "payments",
            "bucket": start + pd.Timedelta(int(index), unit="min"),
            "error_rate": 0.1,
            "p95_latency_ms": 100.0,
            "event_frequency": 10.0,
        }
        if current and index == count - 1:
            row.update(current)
        rows.append(row)
    return pd.DataFrame(rows)


def test_pandas_metrics_use_one_minute_service_buckets_and_fill_gaps() -> None:
    events = [
        {"timestamp": "2025-01-01T00:00:10Z", "service": "payments", "level": "error", "latency_ms": 100},
        {"timestamp": "2025-01-01T00:00:50Z", "service": "payments", "level": "info", "latency_ms": 200},
        {"timestamp": "2025-01-01T00:02:05Z", "service": "payments", "level": "info", "latency_ms": None},
    ]
    metrics = build_bucket_metrics(events)
    assert len(metrics) == 3
    first = metrics.iloc[0]
    gap = metrics.iloc[1]
    assert first["event_frequency"] == 2
    assert first["error_rate"] == 0.5
    assert gap["event_frequency"] == 0
    assert gap["error_rate"] == 0
    assert np.isnan(gap["p95_latency_ms"])


def test_zscore_waits_for_30_prior_buckets() -> None:
    assert rolling_zscore_detect(make_metrics(MIN_HISTORY_BUCKETS)) == []


def test_zscore_zero_variance_and_current_bucket_exclusion_from_rolling_baseline() -> None:
    metrics = make_metrics(ROLLING_WINDOW_BUCKETS + 1, current={"error_rate": 0.9})
    results = rolling_zscore_detect(metrics)
    anomaly = next(item for item in results if item.metric == "error_rate")
    assert anomaly.detection_method == "rolling_zscore"
    assert anomaly.score is None  # Constant history has zero standard deviation.
    assert anomaly.score_label == "positive_infinity"
    assert anomaly.expected_baseline == pytest.approx(0.1)
    # The current 0.9 value would move the baseline if it were included.
    assert anomaly.expected_baseline != pytest.approx(metrics["error_rate"].mean())
    assert anomaly.observed_value == 0.9
    assert anomaly.severity == "medium"
    assert anomaly.affected_service == "payments"
    assert anomaly.timestamp.tzinfo is not None
    assert anomaly.as_dict()["details"]["threshold_abs_z"] == Z_SCORE_THRESHOLD


def test_zscore_uses_rolling_population_std_and_strict_threshold() -> None:
    metrics = make_metrics(ROLLING_WINDOW_BUCKETS + 1)
    metrics.loc[:ROLLING_WINDOW_BUCKETS - 1, "error_rate"] = [0.1 + (index % 2) * 0.02 for index in range(ROLLING_WINDOW_BUCKETS)]
    baseline = metrics.loc[:ROLLING_WINDOW_BUCKETS - 1, "error_rate"]
    metrics.loc[ROLLING_WINDOW_BUCKETS, "error_rate"] = baseline.mean() + 4 * baseline.std(ddof=0)
    results = rolling_zscore_detect(metrics)
    anomaly = next(item for item in results if item.metric == "error_rate")
    assert anomaly.z_score is not None
    assert abs(anomaly.z_score) > Z_SCORE_THRESHOLD


def test_zscore_skips_missing_current_or_insufficient_metric_history() -> None:
    metrics = make_metrics(ROLLING_WINDOW_BUCKETS + 1)
    metrics.loc[0, "p95_latency_ms"] = np.nan
    metrics.loc[ROLLING_WINDOW_BUCKETS, "p95_latency_ms"] = 5000.0
    results = rolling_zscore_detect(metrics)
    assert not any(item.metric == "p95_latency_ms" for item in results)


def test_isolation_forest_waits_for_model_warmup() -> None:
    assert isolation_forest_detect(make_metrics(MIN_HISTORY_BUCKETS), contamination=0.1) == []


def test_isolation_forest_detects_multivariate_outlier_and_records_score() -> None:
    metrics = make_metrics(ROLLING_WINDOW_BUCKETS + 1)
    rng = np.random.default_rng(42)
    for index in range(ROLLING_WINDOW_BUCKETS):
        metrics.loc[index, "event_frequency"] = rng.normal(10.0, 0.5)
        metrics.loc[index, "error_rate"] = rng.normal(0.1, 0.01)
        metrics.loc[index, "p95_latency_ms"] = rng.normal(100.0, 5.0)
    metrics.loc[ROLLING_WINDOW_BUCKETS, ["event_frequency", "error_rate", "p95_latency_ms"]] = [500.0, 0.95, 5000.0]
    results = isolation_forest_detect(metrics, contamination=0.1)
    assert results
    anomaly = results[0]
    assert anomaly.detection_method == "isolation_forest"
    assert anomaly.isolation_forest_result == "anomaly"
    assert anomaly.score is not None and anomaly.score < anomaly.expected_baseline
    assert anomaly.details["random_state"] == 42
    assert anomaly.details["features"]["event_frequency"] == 500.0


def test_isolation_forest_handles_missing_latency_and_is_reproducible() -> None:
    metrics = make_metrics(ROLLING_WINDOW_BUCKETS + 1)
    metrics.loc[:ROLLING_WINDOW_BUCKETS - 1, "p95_latency_ms"] = np.nan
    metrics.loc[ROLLING_WINDOW_BUCKETS, "p95_latency_ms"] = np.nan
    metrics.loc[ROLLING_WINDOW_BUCKETS, "event_frequency"] = 500.0
    first = isolation_forest_detect(metrics, contamination=0.1)
    second = isolation_forest_detect(metrics, contamination=0.1)
    assert [item.as_dict() for item in first] == [item.as_dict() for item in second]


def test_contamination_must_be_valid() -> None:
    with pytest.raises(ValueError):
        isolation_forest_detect(make_metrics(ROLLING_WINDOW_BUCKETS + 1), contamination=0.8)
