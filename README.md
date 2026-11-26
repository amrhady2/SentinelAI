# SentinelAI — Intelligent Observability & Event Watchdog

Local-first, API-first Python service. Steps 1–4 are implemented: project skeleton and ingestion, deterministic anomaly detection, incident correlation, service health scores, and simulated local alerting. Dashboard and packaging implementation remain for later approved steps.

## Implemented in step 1

- FastAPI API with OpenAPI documentation at `/docs`.
- SQLite persistence through SQLModel; database file is local under `data/` by default.
- Validated batch ingestion, log listing with filters and pagination, and service inventory.
- Deterministic synthetic log generator with normal, error-spike, latency-degradation, and traffic-spike scenarios.

## Implemented in step 2: detection engine

- Pandas aggregates UTC log events into one-minute buckets per service. Missing time buckets receive zero event frequency and error rate; latency remains missing when unavailable.
- Rolling z-score compares each metric with the prior 30 buckets, using population standard deviation. It starts only after 30 prior buckets; the current bucket is excluded. The threshold is strictly `|z| > 3`. By design, any deviation from a zero-variance baseline is flagged as an anomaly (z treated as infinite). Since JSON cannot encode Infinity, infinite z-scores are stored as `null` with a `score_label` such as `positive_infinity`.
- Isolation Forest trains independently per service using only its prior 30 buckets. It uses `random_state=42`, and contamination is configurable with `SENTINEL_IFOREST_CONTAMINATION` (default `auto`). Missing feature values are imputed from the training-window median; a wholly missing feature is excluded. The current bucket is never included in its own training set.
- Both detector functions are independently testable and run without an LLM. Anomaly evidence includes method, metric, observed value, expected baseline/threshold, score, severity, affected service, timestamp, and supporting details. Current detector severity is uniformly `medium`; severity classification is not yet defined.
- `POST /api/v1/detection/run` executes both detectors over ingested logs and idempotently persists results. `GET /api/v1/anomalies` returns stored structured evidence.

## Implemented in step 3: incidents and health scores

- Anomalies for the same service join one open incident when the gap is at most `SENTINEL_CORRELATION_WINDOW_MINUTES` (default **5 minutes**). The boundary is inclusive. A gap beyond the window closes the prior incident and opens another. Incidents close after a quiet period greater than or equal to the same window; the incident list endpoint also applies this close check.
- Each incident stores its anomaly IDs, first/last seen timestamps, severity, status, title, and a deterministic explanation. The title and explanation templates are configured in `app/analytics_config.py`; the explanation uses only stored anomaly evidence (method, metric, observed value, baseline, score, severity, and timestamp). No LLM is called.
- Health is calculated for each known service over the last `SENTINEL_HEALTH_WINDOW_MINUTES` (default **60 minutes**) anchored to the latest ingested event timestamp (not wall-clock time), including anomalies exactly at the window cutoff. Each snapshot stores both its event-time window end and the wall-clock time at which it was calculated:

  `score = clamp(100 - sum(severity_weight for each in-window anomaly), 0, 100)`

  Configured weights are low **2**, medium **5**, high **10**, and critical **20** points per anomaly. Step 2 currently emits `medium` severity for every detector finding, so current detected anomalies subtract 5 each until severity policy is defined. Unknown severity labels add zero weight.
- Each detection run stores a health snapshot for historical trend queries. `GET /api/v1/health/scores` returns snapshots, optionally filtered by service. `GET /api/v1/incidents` returns incidents and their stored anomaly IDs and deterministic explanation.
- Configuration: `SENTINEL_CORRELATION_WINDOW_MINUTES=5`, `SENTINEL_HEALTH_WINDOW_MINUTES=60`; severity weights and score bounds are in `app/analytics_config.py`.

## Implemented in step 4: simulated alerting

- `POST /api/v1/alerts/evaluate` checks open incidents and records one simulated alert per incident and channel. Repeated evaluations are suppressed until the configured cooldown expires; a repeat is allowed at the cooldown boundary.
- Cooldown is configured with `SENTINEL_ALERT_COOLDOWN_MINUTES` (default **15 minutes**) in `app/alerts_config.py`.
- `POST /api/v1/webhooks/local` records a local delivery attempt for an incident. It only persists the provided payload and logs the simulated attempt; it makes no outbound HTTP or internet request.
- Alerts persist their incident ID, channel (`local_webhook`), timestamp, delivery status (`simulated`), and payload. `GET /api/v1/alerts` lists them, with optional incident/channel filters.
- The alert rule currently applies to every open incident. Severity-based routing and external delivery are not implemented.

## Run locally

Python 3.11 or newer is recommended. Install dependencies, then start the API:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000/docs`. The SQLite database is created at `data/sentinelai.db`; set `SENTINEL_DATABASE_URL` to override it.

Generate and post a reproducible synthetic workload in another terminal:

```bash
python tools/generate_sample_logs.py --scenario error_spike --seed 42 --post-url http://127.0.0.1:8000/api/v1/logs/ingest
```

Then run detection using `POST /api/v1/detection/run` and review results from `GET /api/v1/anomalies`. Without `--post-url`, the generator only writes a JSON file and makes no network request.

## Verification

```bash
pytest -q
```

The suite is in `tests/` and covers minute bucket aggregation and gap filling, rolling baseline exclusion, z-score warm-up and zero-variance behavior, missing values, Isolation Forest warm-up, detection and reproducibility, incident correlation boundaries and quiet-period close, health scoring/clamping/window boundaries and event-time anchoring, alert deduplication/cooldown, and local webhook recording.

## API available so far

- `GET /health`
- `POST /api/v1/logs/ingest`
- `GET /api/v1/logs?service=checkout&level=error&limit=100&offset=0`
- `GET /api/v1/services`
- `POST /api/v1/detection/run`
- `GET /api/v1/anomalies`
- `GET /api/v1/incidents`
- `GET /api/v1/health/scores`
- `POST /api/v1/alerts/evaluate`
- `POST /api/v1/webhooks/local`
- `GET /api/v1/alerts`

## Data and safety

Generated events are synthetic. Timestamps are normalized to UTC; SQLite may not retain timezone metadata, so API reads are normalized to UTC. Detection and explanations are deterministic and do not call an LLM. The webhook is local and simulated; no cloud service or external network delivery is used. Do not upload production logs unless their use is approved for the environment.

## Scaffold review

The initial scaffold was inspected before refactoring. Its log validation and persistence concepts were retained and separated into schemas, models, database, and ingestion modules. The earlier single-file threshold detector was removed because it did not match the approved detection design. The initial dashboard draft is preserved in `drafts/dashboard_draft.py` for the later dashboard stage and is not wired into the API yet.

The exact prompt history is maintained in [`prompts.md`](prompts.md).

## Remaining build scope

- Not implemented yet: the Streamlit + Plotly dashboard and container packaging.
- Docker Compose is out of scope for this build; packaging will use a single Dockerfile only. The Dockerfile itself has not yet been added.
