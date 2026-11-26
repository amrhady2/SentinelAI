# SentinelAI Approved Specification (Steps 1–2)

This document records approved scope and parameters from the user prompts in `prompts.md`, the existing README, and the implementation. Steps 3 onward may extend this specification only with explicit user decisions.

## Product and architecture

- Project: SentinelAI, an Intelligent Observability & Event Watchdog.
- Local-first, API-first Python service; no cloud services.
- Approved stack: FastAPI, SQLModel, SQLite, Pandas, Streamlit, Plotly, pytest, scikit-learn, and Docker.
- Project sequence: (1) skeleton/data model/ingestion/sample generator, (2) detection, (3) incident correlation/health scores, (4) alerting, (5) dashboard, (6) tests, (7) README implementation/simulation distinction.
- Detection must work without an LLM. An LLM is permitted only after detection to explain detected evidence; step 2 currently uses no LLM.

## Step 1 decisions

- Raw logs are validated and stored locally in SQLite through SQLModel.
- Ingestion is exposed through FastAPI. Sample logs are deterministic and support `normal_operation`, `error_spike`, `latency_degradation`, and `traffic_spike` scenarios.
- Event timestamps are normalized to UTC. The API provides ingestion, log listing, service inventory, and health endpoints.

## Step 2 detection decisions and parameters

- Metrics are aggregated using Pandas into one-minute UTC buckets per service.
- Event frequency is the number of events in a bucket per service. Missing buckets are zero-filled for event frequency and error rate; missing latency remains null/NaN.
- Rolling z-score metrics: `error_rate`, `p95_latency_ms`, and `event_frequency`.
- Rolling window: 30 prior buckets. Minimum history: 30 prior observations. Current bucket excluded from its baseline. Population standard deviation (`ddof=0`). Trigger is strictly `abs(z) > 3`.
- If the baseline standard deviation is zero and the current value differs from the baseline, report an anomaly with null numeric z-score and signed-infinity score label. Missing observations or insufficient history do not score.
- By design, a zero-variance baseline flags any deviation as an anomaly (the mathematical z-score is treated as infinite); the JSON evidence stores a null numeric score and `score_label` (`positive_infinity` or `negative_infinity`) because JSON cannot encode Infinity.
- Isolation Forest is fit per service on only its prior 30 buckets and scores the current bucket; the current bucket is never in training. Fixed seed `random_state=42`; configurable contamination via `SENTINEL_IFOREST_CONTAMINATION`, default `auto`.
- Missing Isolation Forest feature values are imputed with the training-window median. A feature missing throughout that window is excluded; if no usable features remain, that score is skipped.
- Both detectors are independently callable and deterministic. No LLM is used for detection.
- Each anomaly retains method, metric, observed value, expected baseline/threshold, score and score label, severity, affected service, timestamp, and supporting structured evidence. Step 2 assigns uniform `medium` severity; severity classification policy was not set in the step 1/2 prompts.
- The detection API persists evidence idempotently, with `POST /api/v1/detection/run` and `GET /api/v1/anomalies`.

## Step 3–4 event-time and alert-status decisions

- Incident closure uses the global latest stored event timestamp as its explicit reference time. It is the same event-time reference used to anchor the service health window. Read and alert-evaluation endpoints do not close incidents based on wall-clock time. The quiet period closes an incident when `global_latest_event_time - incident.last_seen >= correlation_window_minutes`.
- Alert responses expose `status_at_alert_time` as the persisted incident-status snapshot and `current_incident_status` from the currently stored incident. They do not expose an ambiguous bare `status` field. Legacy stored payloads with `status` are normalized at read time.

## Existing implementation alignment

Implementation references: `app/detection_config.py`, `app/services/metrics.py`, `app/services/detection.py`, `app/models.py`, `app/main.py`, `tests/test_detection.py`, and `README.md`. This specification records only approved decisions and step 1/2 implementation, not later-stage requirements.
