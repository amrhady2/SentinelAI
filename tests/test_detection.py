from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from app.detection_config import (
    BUCKET_SECONDS, ISOLATION_FOREST_RANDOM_STATE, MIN_HISTORY_BUCKETS,
    ROLLING_WINDOW_BUCKETS, Z_SCORE_THRESHOLD,
)
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


@pytest.mark.parametrize(("observed", "score_label"), [(0.9, "positive_infinity"), (0.0, "negative_infinity")])
def test_zscore_zero_variance_and_current_bucket_exclusion_from_rolling_baseline(observed, score_label) -> None:
    metrics = make_metrics(ROLLING_WINDOW_BUCKETS + 1, current={"error_rate": observed})
    results = rolling_zscore_detect(metrics)
    anomaly = next(item for item in results if item.metric == "error_rate")
    assert anomaly.detection_method == "rolling_zscore"
    assert anomaly.score is None  # Constant history has zero standard deviation.
    assert anomaly.score_label == score_label
    assert anomaly.expected_baseline == pytest.approx(0.1)
    # Including the tested value would move the baseline.
    assert anomaly.expected_baseline != pytest.approx(metrics["error_rate"].mean())
    assert anomaly.observed_value == observed
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



def test_normal_flat_service_history_has_no_anomalies() -> None:
    metrics = make_metrics(ROLLING_WINDOW_BUCKETS + 1)
    assert rolling_zscore_detect(metrics) == []
    assert isolation_forest_detect(metrics) == []


def test_detector_parameters_match_approved_values() -> None:
    assert BUCKET_SECONDS == 60
    assert ROLLING_WINDOW_BUCKETS == 30
    assert MIN_HISTORY_BUCKETS == 30
    assert Z_SCORE_THRESHOLD == 3.0
    assert ISOLATION_FOREST_RANDOM_STATE == 42


def test_event_frequency_is_per_bucket_and_per_service() -> None:
    events = [
        {"timestamp": "2025-01-01T00:00:01Z", "service": "payments", "level": "info"},
        {"timestamp": "2025-01-01T00:00:02Z", "service": "payments", "level": "info"},
        {"timestamp": "2025-01-01T00:00:03Z", "service": "inventory", "level": "info"},
        {"timestamp": "2025-01-01T00:00:04Z", "service": "inventory", "level": "info"},
        {"timestamp": "2025-01-01T00:00:05Z", "service": "inventory", "level": "info"},
        {"timestamp": "2025-01-01T00:01:01Z", "service": "payments", "level": "info"},
    ]
    metrics = build_bucket_metrics(events).set_index(["service", "bucket"])
    first_bucket = pd.Timestamp("2025-01-01T00:00:00Z")
    next_bucket = pd.Timestamp("2025-01-01T00:01:00Z")
    assert metrics.loc[("payments", first_bucket), "event_frequency"] == 2
    assert metrics.loc[("inventory", first_bucket), "event_frequency"] == 3
    assert metrics.loc[("payments", next_bucket), "event_frequency"] == 1


def test_zscore_does_not_trigger_at_exact_threshold() -> None:
    metrics = make_metrics(ROLLING_WINDOW_BUCKETS + 1)
    metrics.loc[:ROLLING_WINDOW_BUCKETS - 1, "event_frequency"] = [1.0 + (index % 2) * 2.0 for index in range(ROLLING_WINDOW_BUCKETS)]
    baseline = metrics.loc[:ROLLING_WINDOW_BUCKETS - 1, "event_frequency"]
    metrics.loc[ROLLING_WINDOW_BUCKETS, "event_frequency"] = baseline.mean() + Z_SCORE_THRESHOLD * baseline.std(ddof=0)
    results = rolling_zscore_detect(metrics)
    assert not any(item.metric == "event_frequency" for item in results)


def test_isolation_forest_fits_only_previous_buckets_and_scores_current(monkeypatch) -> None:
    metrics = make_metrics(ROLLING_WINDOW_BUCKETS + 1)
    metrics["event_frequency"] = np.arange(len(metrics), dtype=float)
    metrics.loc[ROLLING_WINDOW_BUCKETS, "event_frequency"] = 500.0
    observations = {}

    class RecordingForest:
        def __init__(self, *, random_state, contamination):
            observations["random_state"] = random_state
            observations["contamination"] = contamination
            self.offset_ = -0.5

        def fit(self, values):
            observations["training"] = values.copy()
            return self

        def predict(self, values):
            observations["scored"] = values.copy()
            return np.array([-1])

        def score_samples(self, values):
            return np.array([-0.6])

    monkeypatch.setattr("app.services.detection.IsolationForest", RecordingForest)
    results = isolation_forest_detect(metrics, contamination=0.1)

    assert len(results) == 1
    assert observations["training"].shape[0] == ROLLING_WINDOW_BUCKETS
    assert observations["training"][:, 2].max() == ROLLING_WINDOW_BUCKETS - 1
    assert observations["scored"].shape[0] == 1
    assert observations["scored"][0, 2] == 500.0
    assert observations["random_state"] == ISOLATION_FOREST_RANDOM_STATE
    assert observations["contamination"] == 0.1
    assert results[0].details["history_buckets"] == MIN_HISTORY_BUCKETS



def test_isolation_forest_imputes_partial_missing_training_values_with_median(monkeypatch) -> None:
    metrics = make_metrics(ROLLING_WINDOW_BUCKETS + 1)
    metrics.loc[4, "p95_latency_ms"] = np.nan
    metrics.loc[ROLLING_WINDOW_BUCKETS, "p95_latency_ms"] = 5000.0
    captured = {}

    class ImputationSpyForest:
        def __init__(self, *, random_state, contamination):
            self.offset_ = -0.5

        def fit(self, values):
            captured["training"] = values.copy()
            return self

        def predict(self, values):
            return np.array([-1])

        def score_samples(self, values):
            return np.array([-0.6])

    monkeypatch.setattr("app.services.detection.IsolationForest", ImputationSpyForest)
    results = isolation_forest_detect(metrics, contamination=0.1)

    assert len(results) == 1
    assert np.isfinite(captured["training"]).all()
    expected_median = metrics.loc[:ROLLING_WINDOW_BUCKETS - 1, "p95_latency_ms"].median()
    assert captured["training"][4, 1] == expected_median
    assert results[0].details["training_medians"]["p95_latency_ms"] == expected_median


@pytest.mark.parametrize(
    ("metric", "observed"),
    [("error_rate", 0.9), ("p95_latency_ms", 5000.0), ("event_frequency", 100.0)],
)
def test_zscore_detects_anomalies_in_each_approved_metric(metric: str, observed: float) -> None:
    metrics = make_metrics(ROLLING_WINDOW_BUCKETS + 1, current={metric: observed})
    results = rolling_zscore_detect(metrics)
    anomaly = next(item for item in results if item.metric == metric)
    assert anomaly.observed_value == observed
    assert anomaly.expected_baseline is not None
    assert anomaly.severity == "medium"
