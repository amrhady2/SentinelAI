# SentinelAI
## Intelligent Observability & Event Watchdog

### Slide 1 — The problem

Operational logs can contain early signs of service failures, but noisy event streams make unusual changes hard to spot quickly. SentinelAI turns ingested logs into deterministic anomaly evidence, correlated incidents, health scores, and simulated alerts that engineers can review.

### Slide 2 — What SentinelAI does

- Accepts application events through a FastAPI ingestion API.
- Detects statistical changes in error rate, p95 latency, and per-service event frequency.
- Groups nearby anomalies by service into incidents and tracks service health over time.
- Presents the evidence in a read-only dashboard and records local simulated alert deliveries.
- Detection does not need an LLM; incident explanations use deterministic templates from stored evidence.

### Slide 3 — Architecture

```text
Log producer / deterministic sample generator
                  │
                  ▼
 FastAPI API ── SQLModel ── SQLite
      │              │
      ├── Pandas metrics and anomaly detectors
      ├── Incident correlation and health snapshots
      └── Simulated local webhook records
                  ▲
                  │ GET endpoints only
      Streamlit + Plotly dashboard

Pytest verifies the components and API; Docker packages the application.
```

The design is local-first: SQLite stores data locally, Docker runs locally, and no cloud service is required by the application.

### Slide 4 — Detection approach

- Pandas aggregates events into **1-minute buckets per service**.
- Rolling z-scores evaluate **error rate**, **p95 latency**, and **event frequency** (events per bucket per service).
- Z-score baseline: prior **30 buckets**, with at least **30 buckets of history**; the current bucket is excluded.
- A z-score anomaly requires **|z| > 3**. A deviation from a zero-variance baseline is flagged by design.
- Isolation Forest trains per service on prior buckets and scores the current bucket. It is deterministic with **`random_state=42`** and uses configurable contamination, default **`auto`**.
- Both detectors operate without an LLM. Anomalies retain structured evidence such as detector, metric, observed value, baseline, score, severity, service, and timestamp.

### Slide 5 — Event time, incidents, and health

- The health window is anchored to the latest ingested event timestamp, not the wall clock.
- Same-service anomalies within the configured **5-minute** correlation window join one open incident; the boundary is inclusive.
- Incident closure uses the **global latest event time** and closes after a quiet period of the same length.
- Cross-service tradeoff: activity from one service can advance the global event-time reference and close an incident for another service that has gone quiet.
- Health score is clamped to **0–100**:

  `score = clamp(100 − sum(severity weight for each anomaly in the health window), 0, 100)`

- Per-anomaly weights are **low 2, medium 5, high 10, critical 20**. Current detector findings are classified as medium.

### Slide 6 — Alerting

- Alert evaluation deduplicates alerts for an open incident and channel.
- Repeat alerts use a **15-minute cooldown**; a repeat is allowed at the inclusive cooldown boundary.
- Local webhooks are simulated: the API records an attempt and delivery status without sending an outbound request.
- Alert responses include **`status_at_alert_time`** and **`current_incident_status`**, avoiding a stale, ambiguous status field.

### Slide 7 — Dashboard

- Streamlit + Plotly displays health history, anomaly evidence, incident explanations, and alert delivery status.
- It reads from FastAPI endpoints only and uses **GET requests only**.
- The dashboard is read-only; it does not directly access SQLite or perform write actions.

### Slide 8 — Verified results

- **40 tests passed** in the latest full suite; one known Starlette/httpx deprecation warning was reported.
- Seeded API verification: **350 events**, **18 anomalies**, and **2 incidents**.
- Incident 1 was closed; incident 2 remained open. The dashboard showed **1 open incident**.
- The health endpoint and dashboard both showed health score **10** for checkout.
- Docker image build completed. In a disposable local container, the API health endpoint returned `status: ok`; the Streamlit health endpoint returned `ok`, and its page returned HTTP 200.

### Slide 9 — Known limitations

- In the seeded normal-operation window, Isolation Forest produced **11 findings**; rolling z-score produced **0**. This is a known false-positive limitation of the current Isolation Forest configuration (`contamination=auto`).
- Zero-variance z-score baselines treat any metric movement as an anomaly. This makes first movement from flat history visible but can be noisy.
- Global event-time closure has the cross-service tradeoff described above.
- Severity classification is not yet differentiated: current detector findings receive medium severity.

### Slide 10 — Run and verify locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1
```

In another terminal, run `streamlit run dashboard.py --server.address 127.0.0.1 --server.port 8501`. For Docker instructions and test commands, see the project README. Docker Compose is out of scope; one Dockerfile runs the API by default, and the dashboard can be started from the same image.
