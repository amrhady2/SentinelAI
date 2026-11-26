# SentinelAI prompt log

## Prompt 1 — project start

```text
Lead Architect mode: ON. We are building a Python-based, API-first  Intelligent Observability & Event Watchdog
using a free database and a dashboard.
Rules:
● No Manual Edits: You provide all logic and fixes. I will not edit any code.
● Audit Log: You must maintain a file named prompts.md. After every turn, update that file (or
provide the text block) with the prompt I just used.
●Time-Check: Start a timer. Goal is an MVP in 4-6 hours (Max window: 16h). Report ‘Elapsed
Time' at the end of every response. Acknowledge and let's start.
```

## Prompt 2 — architecture requirements and approval gate

```text
Project name: SentinelAI - an Intelligent Observability & Event Watchdog service.

Architecture (use exactly this stack, local-first, no cloud services):
- FastAPI backend, API-first, with SQLModel and SQLite as the free database
- Pandas for log analysis
- Streamlit + Plotly dashboard for health trends and incidents
- Pytest for tests, Docker for packaging

Hard rule on detection: anomaly detection must be deterministic and statistical - rolling z-scores on error rate, latency, and event frequency, plus an Isolation Forest model. Detection must work completely without any LLM. An LLM may only be used afterward to write a human-readable explanation of an incident, from the detected evidence.

Features to build:
1. Log ingestion API plus a sample log generator so the system can be demonstrated end to end
2. Multi-dimensional anomaly and spike detection
3. Incident correlation - group related anomalies into one incident
4. Service health scores with historical trends
5. Intelligent alerting: severity levels, deduplication, and simulated webhook alerts when thresholds are breached
6. Dashboard visualizing health trends, anomalies, and incidents

Build order - MVP core loop first (ingest, detect, alert, dashboard), then the rest:
1. Project skeleton, data model, ingestion, sample log generator
2. Detection engine
3. Incident correlation and health scores
4. Alerting
5. Dashboard
6. Tests
7. README, clearly separating what is implemented from what is simulated

Keep prompts.md updated after every turn with the prompt I used, and report elapsed time at the end of every response.

First, show me your plan and the file structure, and wait for my approval before writing any code.
```

## Prompt 3 — approval for step 1

```text
Approved. Proceed with step 1 only: project skeleton, data model, ingestion, and sample log generator. Implement it fully, run it to verify it works, then report what was built and the elapsed time, and wait for my next instruction before moving to step 2.
```

## Prompt 4 — refinements and step-1 scope

```text
Your proposed architecture is approved, subject to the following refinements. Incorporate them before implementation:

1. Add scikit-learn as the dependency for Isolation Forest.
2. Detection parameters (fixed, do not invent others): bucket size 1 minute, rolling window 30 buckets, z-score threshold |z| > 3, minimum history of 30 buckets before scoring starts. "Event frequency" means events per bucket per service.
3. Make Isolation Forest deterministic: fix random_state and set contamination explicitly (configurable).
4. Both detection engines independently testable: rolling z-score detection and Isolation Forest detection. Handle edge cases: insufficient historical observations, zero standard deviation, missing values, model warm-up. Use fixed random seeds for reproducibility.
5. Every anomaly must retain structured evidence: detection method, observed value, expected baseline, z-score or Isolation Forest result, severity, affected service, timestamp.
6. Incident correlation using configurable time windows and service grouping. Group related anomalies into one incident; prevent duplicate incidents.
7. Define a transparent, reproducible service health score from 0 to 100 based on error rate, latency, and event anomalies. Document the formula in the README.
8. Alert deduplication with configurable cooldown periods. Webhook delivery stays simulated and local.
9. The Streamlit dashboard must consume FastAPI endpoints, not access SQLite directly.
10. Deterministic synthetic log generation with configurable scenarios: normal operation, error spikes, latency degradation, traffic spikes.
11. Inspect the existing scaffold before modifying it. Preserve useful existing work and document any changes.
12. Docker Compose must run the complete application locally with no cloud dependencies.
13. Include tests for normal operation, anomaly detection, incident correlation, alert deduplication, and API integration.
14. Clean up prompts.md: it must contain only the prompts actually sent, in order, with exact text. Remove the unfilled "[INSERT PROJECT CHOICE]" template text and the stray "Rules: Intelligent Observability & Event Watchdog" line.

Maintain the original build order. Do not introduce unnecessary frameworks or additional infrastructure. Prioritize a working end-to-end MVP, not premature sophistication.

Proceed with step 1 only: project skeleton, data model, ingestion, and sample log generator. Implement it fully, run it to verify it works, then report what was built and the elapsed time, and wait for my next instruction before moving to step 2.
```


## Prompt 5 — step 2 detection engine

```text
Proceed with step 2 only: detection engine. Implement Pandas rolling metrics (1-minute buckets, 30-bucket window) and both detection engines independently testable: rolling z-score detection (|z| > 3, minimum 30 buckets history, event frequency = events per bucket per service) and Isolation Forest (fixed random_state, configurable contamination, scikit-learn). Handle edge cases: insufficient history, zero standard deviation, missing values, model warm-up. Every anomaly must retain structured evidence: detection method, observed value, expected baseline, score, severity, affected service, timestamp. Detection must work completely without any LLM. Write tests for the detection engine, run them, then report what was built, the test results, and the elapsed time, and wait for my next instruction before moving to step 3.
```

## Prompt 6 — verification requests and step-3 scope

```text
Before any code changes, create docs/SPEC.md if it does not exist. It must record the approved parameters and decisions from steps 1 and 2, taken from prompts.md, the README, and the code.

Fixes first: add pytest.ini with pythonpath = . and testpaths = tests. Add .gitignore covering .venv/, __pycache__/, *.pyc, .pytest_cache/, and the local SQLite database file. Verify plain pytest -q works from the project root with no PYTHONPATH.

Step 2 proofs (show, do not just claim): (1) run pytest -v and show the test names proving coverage of insufficient history, zero variance, missing values, and Isolation Forest warm-up; (2) show the code line and test proving the current bucket is excluded from its rolling baseline; (3) state what data the Isolation Forest is fit on and what it scores; (4) show one sample anomaly JSON record with all evidence fields (method, observed value, baseline, score, severity, service, timestamp); (5) run the detector twice on the same input and confirm identical output.

Then step 3 only: incident correlation and health scoring. Correlation: anomalies for the same service within correlation_window_minutes (default 5) join one open incident. An incident opens on its first anomaly, stays open while new anomalies arrive within the window, and closes after a quiet period of the same length. Store the anomaly IDs on each incident. Put all values in config, not in code. Health score: for each service, score = 100 minus the sum of severity weights (low 2, medium 5, high 10, critical 20) for anomalies in the last health_window_minutes (default 60), clamped to 0-100, using the severity levels from step 2. Record this formula and all values in the README. Incident explanations: deterministic templates filled only from the stored evidence fields. No LLM. Record in the README that docker-compose is out of scope for this build; a single Dockerfile only. Tests: correlation (same service joins, different service does not, window boundary, quiet-period close) and health scoring (no anomalies gives 100, clamping at 0, window boundary). Run pytest -v and show the full output. Record this prompt in prompts.md. If the budget runs out mid-step, checkpoint what works, list what is unfinished in the README, and stop. Report what was built, the test results, and the elapsed time, and wait for my next instruction before moving to step 4.
```

## Prompt 7 — step-3 verification fixes and step-4 alerting

```text
Step 3 is verified, with five small items before step 4: (1) confirm the 60-minute health window is anchored to the latest event timestamp, not wall-clock time; if it uses wall-clock time, change it to the latest event timestamp; (2) show the plain pytest -q command run from the project root and its output; (3) show one incident record with its template explanation text, proving the explanation uses only stored evidence fields; (4) record in SPEC.md that zero-variance baselines flag any deviation as an anomaly (z treated as infinite), by design; (5) record in the README that infinite z-scores are stored as null with score_label positive_infinity because JSON cannot encode Infinity. Then proceed with step 4 only: alerting. Implement alert rules with dedup (do not alert twice for the same open incident) and a configurable cooldown period between repeat alerts for the same incident. Alerts are simulated locally: log them and expose a local webhook endpoint that records delivery attempts without sending anything to the internet. Persist alerts with their incident ID, channel, timestamp, and delivery status. Add an endpoint to list alerts. Write tests for dedup, cooldown, and the webhook recording, run the full test suite with pytest -v and show the output. Record this prompt in prompts.md. If the budget runs out mid-step, checkpoint what works, list what is unfinished in the README, and stop. Report what was built, the test results, and the elapsed time, and wait for my next instruction before moving to step 5.
```

## Prompt 8 — continuation and proof request

```text
Continue from where you stopped. Verify plain pytest -q from the project root and show its output, show the stored incident example with its template explanation, then proceed with step 4 only (alerting) exactly as instructed in my previous message. Record this prompt in prompts.md. Report what was built, the test results, and the elapsed time, and wait for my next instruction before moving to step 5.
```

## Prompt 9 — step 5 dashboard

```text
Step 4 is verified. Proceed with step 5 only: the dashboard. Do not change any existing alerting, detection, or incident logic. Build the Streamlit + Plotly dashboard, starting from the preserved drafts/dashboard_draft.py. The dashboard must consume the FastAPI endpoints only; it must never read SQLite directly. Show: per-service health score trends over time from the stored health history, the anomaly list with evidence fields, the incident list with explanations, and the alert list with delivery status. The dashboard is read-only: do not add write actions. Keep it local-only. Add the dashboard run command to the README. Also record in the README: the alert cooldown boundary rule (repeats allowed at the boundary, inclusive), the Starlette TestClient/httpx deprecation warning as known and harmless, and the zero-variance tradeoff (a service with a flat metric will alert on its first real movement, by design). Run pytest -q and show the output. Start the dashboard and verify it loads and displays data from the API, then report what was built, how you verified it, and the elapsed time, and wait for my next instruction before moving to step 6. After finishing, commit the changes with a clear message describing step 5.
```


## Prompt 10 — step-5 acceptance fixes

```text
Step 5 needs three fixes before it is accepted. (1) Bind Streamlit to loopback only: add .streamlit/config.toml with [server] address = "127.0.0.1" and [browser] gatherUsageStats = false, and confirm the file is committed (it is not a secret). Show a dashboard start whose terminal reports only the Local URL, with no Network URL. (2) Replace every deprecated use_container_width with the current width argument (width='stretch' where the old value was True) and show a dashboard run with no deprecation warnings. (3) Add a test that scans dashboard.py and fails on any non-GET HTTP call (no POST, PUT, DELETE, or requests usage). Also confirm the API starts on port 8000 with no port conflict and that the dashboard reads from it, then re-verify the dashboard numbers (services, open incidents, anomalies, alerts) against the API. Run pytest -q and show the output, commit the fixes with a clear message, and report the elapsed time. Wait for my next instruction before moving to step 6.
```


## Prompt 11 — step 6 tests

```text
Step 5 is verified and accepted. Proceed with step 6 only: tests. Read docs/SPEC.md and prompts.md first. Review the full test suite for gaps against SPEC.md: every detector parameter, edge case, correlation rule, health formula, alert dedup/cooldown rule, and API endpoint must have a covering test. Make sure these are covered: the zero-variance rule (flat history flags any change, medium severity); alert dedup across two evaluations of the same open incident; the cooldown boundary; webhook records delivery attempts and makes no outbound calls; health window anchored to latest event timestamp; incident closes after quiet period. Do not change application behavior. Add any missing tests, including one end-to-end API test that ingests events, runs detection, and reads back anomalies, incidents, health scores, and alerts. If pytest-cov is in requirements, run pytest --cov=app and report the coverage percentage. Run the full suite with pytest -v and show the output. Record this prompt in prompts.md. After finishing, commit the changes with a clear message describing step 6. Report what was added, the test results, and the elapsed time, and wait for my next instruction before moving to step 7.
```
