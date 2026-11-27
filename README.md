# SentinelAI — Intelligent Observability & Event Watchdog

SentinelAI is a local-first, API-first observability service that ingests application logs, detects statistical anomalies, correlates incidents, scores service health, and records simulated alerts. The dashboard visualizes API-backed health history, anomalies, incidents, and alerts. Detection and alert explanations do not depend on an LLM.

**Stack:** Python 3.12, FastAPI, SQLModel, SQLite, Pandas, scikit-learn Isolation Forest, Streamlit, Plotly, pytest, and Docker. No cloud services are required at runtime.

## Implemented in step 1

- FastAPI API with OpenAPI documentation at `/docs`.
- SQLite persistence through SQLModel; database file is local under `data/` by default.
- Validated batch ingestion, log listing with filters and pagination, and service inventory.
- Deterministic synthetic log generator with normal, error-spike, latency-degradation, and traffic-spike scenarios.

## Implemented in step 2: detection engine

- Pandas aggregates UTC log events into one-minute buckets per service. Missing time buckets receive zero event frequency and error rate; latency remains missing when unavailable.
- Rolling z-score compares each metric with the prior 30 buckets, using population standard deviation. It starts only after 30 prior buckets; the current bucket is excluded. The threshold is strictly `|z| > 3`. By design, any deviation from a zero-variance baseline is flagged as an anomaly (z treated as infinite). Since JSON cannot encode Infinity, infinite z-scores are stored as `null` with a `score_label` such as `positive_infinity`. Tradeoff: a service with a flat metric will alert on its first real movement.
- Isolation Forest trains independently per service using only its prior 30 buckets. It uses `random_state=42`, and contamination is configurable with `SENTINEL_IFOREST_CONTAMINATION` (default `auto`). Missing feature values are imputed from the training-window median; a wholly missing feature is excluded. The current bucket is never included in its own training set.
- Both detector functions are independently testable and run without an LLM. Anomaly evidence includes method, metric, observed value, expected baseline/threshold, score, severity, affected service, timestamp, and supporting details. Current detector severity is uniformly `medium`; severity classification is not yet defined.
- `POST /api/v1/detection/run` executes both detectors over ingested logs and idempotently persists results. `GET /api/v1/anomalies` returns stored structured evidence.

## Implemented in step 3: incidents and health scores

- Anomalies for the same service join one open incident when the gap is at most `SENTINEL_CORRELATION_WINDOW_MINUTES` (default **5 minutes**). The boundary is inclusive. A gap beyond the window closes the prior incident and opens another. Quiet-period closure uses the global latest stored event timestamp (the same reference as the health-window anchor), never wall-clock time; the incident, alert, webhook, and alert-evaluation paths apply this event-time close check.
- Known limitation: because closure uses a global event-time reference, a busy service can advance the clock and close an incident for a different service that has gone silent. This is the tradeoff for sharing the health window’s global latest-event anchor.
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
- Alerts persist their incident ID, channel (`local_webhook`), timestamp, delivery status (`simulated`), and payload. Alert responses expose `status_at_alert_time` and `current_incident_status`; they do not expose an ambiguous bare `status` field. `GET /api/v1/alerts` lists them, with optional incident/channel filters.
- The alert rule currently applies to every open incident. Severity-based routing and external delivery are not implemented.
- Cooldown boundary rule: the boundary is inclusive; a repeat alert is allowed when elapsed time is exactly the configured cooldown.
- The suite emits a Starlette `TestClient`/`httpx` deprecation warning. It is known and harmless; tests pass.

## Implemented in step 5: read-only dashboard

- `dashboard.py` uses Streamlit and Plotly and reads the local FastAPI API only. It does not import database modules, read SQLite, or expose write actions.
- It shows stored per-service health history, structured anomaly evidence, incident explanations, and simulated alert delivery statuses.
- `SENTINEL_API_URL` can select the API origin, but it is restricted to `localhost`, `127.0.0.1`, or `::1`. For local runs, Streamlit binds to `127.0.0.1`. In Docker, it binds to `0.0.0.0` inside the container; the published host port is restricted to `127.0.0.1`.

## Run locally

Python 3.12 is used for the tested image. Python 3.11 or newer is recommended locally. Install dependencies in a virtual environment, then start the API:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1
```

Open `http://127.0.0.1:8000/docs`. The SQLite database is created at `data/sentinelai.db`; set `SENTINEL_DATABASE_URL` to override it.

In a second terminal, start the read-only dashboard:

```bash
streamlit run dashboard.py --server.address 127.0.0.1 --server.port 8501
```

Open `http://127.0.0.1:8501`. The dashboard reads its data from FastAPI; start the API first. To point it at a different local API, set `SENTINEL_API_URL`, for example `SENTINEL_API_URL=http://127.0.0.1:8001`.

Generate and post a reproducible synthetic workload in another terminal:

```bash
python tools/generate_sample_logs.py --scenario error_spike --seed 42 --output /tmp/seed.json --post-url http://127.0.0.1:8000/api/v1/logs/ingest
```

Always pass `--output` to keep generated files out of the tracked `data/sample_logs.json`.

Then run detection using `POST /api/v1/detection/run` and review results from `GET /api/v1/anomalies`. Without `--post-url`, the generator only writes a JSON file and makes no network request.

## Verification

```bash
pytest -q
```

The suite is in `tests/` and covers minute bucket aggregation and gap filling, rolling baseline exclusion, z-score warm-up and zero-variance behavior, missing values and Isolation Forest warm-up/reproducibility, incident correlation boundaries and event-time quiet-period close, health scoring/clamping/window boundaries and event-time anchoring, alert deduplication/cooldown, simulated webhook recording, API endpoints, and end-to-end ingestion/detection/readback. Test fixtures use in-memory SQLite (`sqlite://`) rather than the default `data/sentinelai.db`.


### Verified seeded evidence (2026-11-26)

Read-only verification against the local API on port 8001, backed by the disposable `data/closure_check.db`, found 350 `checkout` events: 300 labeled `normal_operation` from 14:00 to before 15:00 UTC and 50 `error_spike` events from 15:00 to before 15:10 UTC. The stored 18 anomalies break down as 15 Isolation Forest and 3 rolling z-score findings. Within the normal-operation interval, 11 findings occurred: anomaly IDs 4–14, all from Isolation Forest; rolling z-score produced none in that interval. These are false positives against the known normal scenario in this seeded run; this is a known Isolation Forest limitation, and contamination remains `auto`. All three rolling z-score findings and Isolation Forest IDs 15–18 fall in the error-spike interval.

The two incidents also match event-time closure: incident 1, last seen at 14:51 UTC, is closed because the latest event at 15:09:40 UTC is 18 minutes 40 seconds later (beyond the five-minute window). Incident 2 remains open because its last anomaly (15:09:00) is only 40 seconds before the latest event (15:09:40), well within the 5-minute correlation window. The API reports one open incident. The alerts expose `current_incident_status` and `status_at_alert_time`, with no bare `status` key.

The health endpoint reports checkout score **10**, **18** in-window anomalies, a 60-minute window, and `window_end` **2026-11-26T15:09:40Z**. The dashboard was pointed at `http://127.0.0.1:8001` via `SENTINEL_API_URL`; its open-incident count was **1** and its health history score was **10**, matching the API response.

## Run with Docker

Build the image locally; its default command starts only the FastAPI service. The runtime has no cloud-service dependency. Docker Compose is intentionally out of scope.

```bash
docker build -t sentinelai:local .
docker volume create sentinelai-data
docker run -d --name sentinelai \
  -p 127.0.0.1:8000:8000 \
  -p 127.0.0.1:8501:8501 \
  -v sentinelai-data:/app/data \
  sentinelai:local
```

The API is available at `http://127.0.0.1:8000/docs`. Start the dashboard from the same image/container in another terminal:

```bash
docker exec -it sentinelai streamlit run dashboard.py \
  --server.address 0.0.0.0 --server.port 8501 \
  --browser.gatherUsageStats false
```

The dashboard’s default API URL (`127.0.0.1:8000`) points to the API process in that same container. The host ports are published only on loopback. Open `http://127.0.0.1:8501`. Stop/remove the container with `docker rm -f sentinelai`; the named volume retains its local database.

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

Generated events are synthetic. Timestamps are normalized to UTC; SQLite may not retain timezone metadata, so API reads are normalized to UTC. Detection and explanations are deterministic and do not call an LLM. The webhook is local and simulated; no cloud service or external network delivery is used. Do not upload production logs unless their use is approved for the environment. Streamlit’s optional file-watching dependency `watchdog==7.1.1` may be installed locally for faster reloads; it is not required and is not included in `requirements.txt` or the Docker image.

## Cleanup

No cloud resources, cloud accounts, hosted databases, or hosted application services were created for this project. The application data and Docker runtime are local. The Docker build uses the `python:3.12-slim` base image and may contact Docker Hub to obtain it if it is not already cached; no cloud resource is provisioned by that step.

## Scaffold review

The initial scaffold was inspected before refactoring. Its log validation and persistence concepts were retained and separated into schemas, models, database, and ingestion modules. The earlier single-file threshold detector was removed because it did not match the approved detection design. The preserved `drafts/dashboard_draft.py` informed the active `dashboard.py`; its ingestion controls, demo-seeding action, and incident mutation controls were omitted to keep the dashboard read-only. Dashboard data comes exclusively from FastAPI endpoints.

The exact prompt history is maintained in [`prompts.md`](prompts.md).

## Packaging scope

- A single root `Dockerfile` builds the application image and runs the API by default; the dashboard runs from that same image using `docker exec` as documented above.
- Docker Compose is intentionally out of scope.
