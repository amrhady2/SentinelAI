from __future__ import annotations

from typing import Any, Iterable

import numpy as np
import pandas as pd

from app.detection_config import BUCKET_SECONDS

METRIC_COLUMNS = ("error_rate", "p95_latency_ms", "event_frequency")
OUTPUT_COLUMNS = ("service", "bucket", "event_count", "error_count", *METRIC_COLUMNS)


def build_bucket_metrics(events: Iterable[Any] | pd.DataFrame) -> pd.DataFrame:
    """Aggregate raw events into one UTC row per service and one-minute bucket.

    Missing event buckets are filled with zero event/error counts and zero event rate;
    p95 latency remains NaN when no latency observations exist.
    """
    if isinstance(events, pd.DataFrame):
        frame = events.copy()
    else:
        rows = [event.model_dump() if hasattr(event, "model_dump") else (event.dict() if hasattr(event, "dict") else dict(event)) for event in events]
        frame = pd.DataFrame(rows)

    if frame.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    missing = {"timestamp", "service", "level"} - set(frame.columns)
    if missing:
        raise ValueError(f"events missing required columns: {', '.join(sorted(missing))}")
    if "latency_ms" not in frame.columns:
        frame["latency_ms"] = np.nan

    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
    frame["service"] = frame["service"].astype("string").str.strip()
    frame["level"] = frame["level"].astype("string").str.strip().str.lower()
    frame["level"] = frame["level"].replace({"warning": "warn", "fatal": "critical", "err": "error"})
    frame["latency_ms"] = pd.to_numeric(frame["latency_ms"], errors="coerce")
    frame["latency_ms"] = frame["latency_ms"].where(np.isfinite(frame["latency_ms"]))
    frame = frame.dropna(subset=["timestamp", "service"])
    frame = frame[frame["service"] != ""]
    if frame.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    frame["bucket"] = frame["timestamp"].dt.floor(f"{BUCKET_SECONDS}s")
    frame["is_error"] = frame["level"].isin({"error", "critical"})
    grouped = frame.groupby(["service", "bucket"], sort=True, observed=True)
    counts = grouped.agg(event_count=("level", "size"), error_count=("is_error", "sum"))
    counts["event_frequency"] = counts["event_count"].astype(float)
    counts["error_rate"] = counts["error_count"] / counts["event_count"]
    latencies = grouped["latency_ms"].quantile(0.95).rename("p95_latency_ms")
    metrics = counts.join(latencies).reset_index()

    completed: list[pd.DataFrame] = []
    for service, service_rows in metrics.groupby("service", sort=True):
        service_rows = service_rows.set_index("bucket").sort_index()
        full_index = pd.date_range(service_rows.index.min(), service_rows.index.max(), freq=f"{BUCKET_SECONDS}s", tz="UTC")
        service_rows = service_rows.reindex(full_index)
        service_rows.index.name = "bucket"
        service_rows["service"] = service
        for column in ("event_count", "error_count", "event_frequency", "error_rate"):
            service_rows[column] = service_rows[column].fillna(0)
        completed.append(service_rows.reset_index())
    result = pd.concat(completed, ignore_index=True)
    result["event_count"] = result["event_count"].astype(int)
    result["error_count"] = result["error_count"].astype(int)
    return result.loc[:, OUTPUT_COLUMNS].sort_values(["service", "bucket"], kind="stable").reset_index(drop=True)


def prepare_bucket_metrics(metrics: pd.DataFrame) -> pd.DataFrame:
    """Validate and make a complete per-service minute grid for detector inputs."""
    required = {"service", "bucket", *METRIC_COLUMNS}
    missing = required - set(metrics.columns)
    if missing:
        raise ValueError(f"metrics missing required columns: {', '.join(sorted(missing))}")
    frame = metrics.copy()
    frame["bucket"] = pd.to_datetime(frame["bucket"], utc=True, errors="coerce").dt.floor(f"{BUCKET_SECONDS}s")
    frame["service"] = frame["service"].astype("string").str.strip()
    for column in METRIC_COLUMNS:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame[column] = frame[column].where(np.isfinite(frame[column]))
    frame = frame.dropna(subset=["bucket", "service"])
    frame = frame[frame["service"] != ""]
    if frame.empty:
        return pd.DataFrame(columns=("service", "bucket", *METRIC_COLUMNS))
    frame = frame.sort_values(["service", "bucket"], kind="stable").drop_duplicates(["service", "bucket"], keep="last")

    completed: list[pd.DataFrame] = []
    for service, service_rows in frame.groupby("service", sort=True):
        service_rows = service_rows.set_index("bucket").sort_index()
        full_index = pd.date_range(service_rows.index.min(), service_rows.index.max(), freq=f"{BUCKET_SECONDS}s", tz="UTC")
        service_rows = service_rows.reindex(full_index)
        service_rows.index.name = "bucket"
        service_rows["service"] = service
        # A missing service bucket means no events, hence zero error/event frequency.
        for column in ("error_rate", "event_frequency"):
            service_rows[column] = service_rows[column].fillna(0.0)
        completed.append(service_rows.reset_index())
    return pd.concat(completed, ignore_index=True).loc[:, ("service", "bucket", *METRIC_COLUMNS)]
