from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import math
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

from app.detection_config import (
    ISOLATION_FOREST_CONTAMINATION,
    ISOLATION_FOREST_RANDOM_STATE,
    MIN_HISTORY_BUCKETS,
    ROLLING_WINDOW_BUCKETS,
    Z_SCORE_THRESHOLD,
)
from app.services.metrics import METRIC_COLUMNS, prepare_bucket_metrics

# Detector results share a neutral initial severity. Severity escalation policy is a later step.
DETECTOR_SEVERITY = "medium"


@dataclass(frozen=True)
class AnomalyEvidence:
    detection_method: str
    metric: str
    observed_value: float
    expected_baseline: float | None
    score: float | None
    score_label: str | None
    severity: str
    affected_service: str
    timestamp: datetime
    z_score: float | None = None
    isolation_forest_result: str | None = None
    details: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "detection_method": self.detection_method,
            "metric": self.metric,
            "observed_value": self.observed_value,
            "expected_baseline": self.expected_baseline,
            "score": self.score,
            "score_label": self.score_label,
            "z_score": self.z_score,
            "isolation_forest_result": self.isolation_forest_result,
            "severity": self.severity,
            "affected_service": self.affected_service,
            "timestamp": _utc_iso(self.timestamp),
        }
        if self.details:
            payload["details"] = self.details
        return payload


def _utc_iso(value: datetime | pd.Timestamp) -> str:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    return timestamp.isoformat().replace("+00:00", "Z")


def _timestamp(value: pd.Timestamp) -> datetime:
    value = value.to_pydatetime()
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def rolling_zscore_detect(metrics: pd.DataFrame) -> list[AnomalyEvidence]:
    """Score each metric against exactly the prior 30 one-minute buckets.

    The current bucket is excluded from its baseline. A zero-variance baseline flags
    any non-equal observation with an undefined numeric z-score and an explicit label.
    """
    frame = prepare_bucket_metrics(metrics)
    anomalies: list[AnomalyEvidence] = []
    for service, rows in frame.groupby("service", sort=True):
        rows = rows.sort_values("bucket", kind="stable").reset_index(drop=True)
        for metric in METRIC_COLUMNS:
            values = rows[metric].astype(float)
            rolling = values.rolling(
                window=ROLLING_WINDOW_BUCKETS,
                min_periods=MIN_HISTORY_BUCKETS,
            )
            # Shift so the observation being tested never contaminates its baseline.
            baselines = rolling.mean().shift(1)
            deviations = rolling.std(ddof=0).shift(1)
            for index in range(MIN_HISTORY_BUCKETS, len(rows)):
                observed = values.iloc[index]
                baseline_value = baselines.iloc[index]
                deviation_value = deviations.iloc[index]
                if pd.isna(observed) or pd.isna(baseline_value) or pd.isna(deviation_value):
                    continue
                baseline = float(baseline_value)
                deviation = float(deviation_value)
                if not math.isfinite(baseline) or not math.isfinite(deviation):
                    continue
                score: float | None
                score_label: str | None = None
                # Constant decimal samples can have a tiny non-zero std due to
                # binary floating-point representation (for example 30 x 0.1).
                zero_tolerance = np.finfo(float).eps * max(1.0, abs(baseline)) * 8
                if deviation <= zero_tolerance:
                    if observed == baseline:
                        continue
                    score = None
                    score_label = "positive_infinity" if observed > baseline else "negative_infinity"
                else:
                    score = float((observed - baseline) / deviation)
                    if not math.isfinite(score) or abs(score) <= Z_SCORE_THRESHOLD:
                        continue
                bucket = rows.loc[index, "bucket"]
                anomalies.append(
                    AnomalyEvidence(
                        detection_method="rolling_zscore",
                        metric=metric,
                        observed_value=float(observed),
                        expected_baseline=baseline,
                        score=score,
                        score_label=score_label,
                        severity=DETECTOR_SEVERITY,
                        affected_service=str(service),
                        timestamp=_timestamp(bucket),
                        z_score=score,
                        details={
                            "history_buckets": MIN_HISTORY_BUCKETS,
                            "rolling_window_buckets": ROLLING_WINDOW_BUCKETS,
                            "threshold_abs_z": Z_SCORE_THRESHOLD,
                            "std_dev": deviation,
                        },
                    )
                )
    return anomalies


def _resolve_contamination(contamination: str | float | None) -> str | float:
    value: str | float = ISOLATION_FOREST_CONTAMINATION if contamination is None else contamination
    if isinstance(value, str):
        if value.strip().lower() == "auto":
            return "auto"
        try:
            value = float(value)
        except ValueError as exc:
            raise ValueError("contamination must be 'auto' or a float in (0, 0.5].") from exc
    value = float(value)
    if not 0.0 < value <= 0.5:
        raise ValueError("contamination must be 'auto' or a float in (0, 0.5].")
    return value


def isolation_forest_detect(
    metrics: pd.DataFrame,
    contamination: str | float | None = None,
) -> list[AnomalyEvidence]:
    """Fit a seeded Isolation Forest on each service's prior 30 buckets, then score current.

    Missing feature values are imputed with that feature's training-window median. A
    feature that is entirely missing in the training window is excluded for that score.
    The current bucket never participates in fitting its own model.
    """
    contamination_value = _resolve_contamination(contamination)
    frame = prepare_bucket_metrics(metrics)
    anomalies: list[AnomalyEvidence] = []
    for service, rows in frame.groupby("service", sort=True):
        rows = rows.sort_values("bucket", kind="stable").reset_index(drop=True)
        features = rows.loc[:, METRIC_COLUMNS].replace([np.inf, -np.inf], np.nan)
        for index in range(MIN_HISTORY_BUCKETS, len(rows)):
            history = features.iloc[index - ROLLING_WINDOW_BUCKETS:index].copy()
            current = features.iloc[[index]].copy()
            usable = [column for column in METRIC_COLUMNS if history[column].notna().any()]
            if not usable:
                continue
            medians = history[usable].median()
            training = history[usable].fillna(medians)
            observation = current[usable].fillna(medians)
            if training.isna().any().any() or observation.isna().any().any():
                continue
            model = IsolationForest(
                random_state=ISOLATION_FOREST_RANDOM_STATE,
                contamination=contamination_value,
            )
            model.fit(training.to_numpy(dtype=float))
            prediction = int(model.predict(observation.to_numpy(dtype=float))[0])
            if prediction != -1:
                continue
            # score_samples is on the same scale as IsolationForest.offset_; lower
            # values are more anomalous. Keeping both in evidence makes the result
            # directly interpretable and avoids mixing the shifted decision score.
            decision_score = float(model.score_samples(observation.to_numpy(dtype=float))[0])
            threshold = float(model.offset_)
            bucket = rows.loc[index, "bucket"]
            current_values = {
                column: (float(current.iloc[0][column]) if pd.notna(current.iloc[0][column]) else None)
                for column in METRIC_COLUMNS
            }
            baseline_values = {column: float(medians[column]) for column in usable}
            anomalies.append(
                AnomalyEvidence(
                    detection_method="isolation_forest",
                    metric="multivariate",
                    observed_value=decision_score,
                    expected_baseline=threshold,
                    score=decision_score,
                    score_label="score_samples",
                    severity=DETECTOR_SEVERITY,
                    affected_service=str(service),
                    timestamp=_timestamp(bucket),
                    isolation_forest_result="anomaly",
                    details={
                        "features": current_values,
                        "training_medians": baseline_values,
                        "decision_threshold": threshold,
                        "contamination": contamination_value,
                        "random_state": ISOLATION_FOREST_RANDOM_STATE,
                        "history_buckets": MIN_HISTORY_BUCKETS,
                    },
                )
            )
    return anomalies


def anomaly_dedupe_key(anomaly: AnomalyEvidence) -> str:
    identity = "|".join(
        (
            anomaly.detection_method,
            anomaly.metric,
            anomaly.affected_service,
            _utc_iso(anomaly.timestamp),
        )
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()
