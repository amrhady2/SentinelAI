from __future__ import annotations

import argparse
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen

SCENARIOS = ("normal_operation", "error_spike", "latency_degradation", "traffic_spike")
DEFAULT_START = "2025-01-01T00:00:00+00:00"


def parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def generate_events(
    scenario: str = "normal_operation",
    service: str = "checkout",
    duration_minutes: int = 30,
    events_per_minute: int = 5,
    seed: int = 42,
    start_time: datetime | None = None,
) -> list[dict]:
    if scenario not in SCENARIOS:
        raise ValueError(f"scenario must be one of: {', '.join(SCENARIOS)}")
    if duration_minutes < 1 or duration_minutes > 1440:
        raise ValueError("duration_minutes must be between 1 and 1440")
    if events_per_minute < 1 or events_per_minute > 1000:
        raise ValueError("events_per_minute must be between 1 and 1000")
    rng = random.Random(seed)
    start = start_time or parse_datetime(DEFAULT_START)
    events: list[dict] = []
    anomaly_start = max(0, duration_minutes - max(1, duration_minutes // 4))
    event_number = 0

    for minute in range(duration_minutes):
        affected = minute >= anomaly_start
        count = events_per_minute * (5 if scenario == "traffic_spike" and affected else 1)
        for _ in range(count):
            event_number += 1
            second = rng.randrange(60)
            timestamp = start + timedelta(minutes=minute, seconds=second)
            if scenario == "error_spike" and affected:
                level = "error" if rng.random() < 0.55 else "info"
            else:
                level = "error" if rng.random() < 0.02 else ("warn" if rng.random() < 0.08 else "info")
            if scenario == "latency_degradation" and affected:
                latency = round(rng.uniform(1800, 4200), 2)
            else:
                latency = round(rng.uniform(40, 350), 2)
            if level == "error":
                message = "Synthetic request failed"
            elif scenario == "latency_degradation" and affected:
                message = "Synthetic request completed with degraded latency"
            else:
                message = "Synthetic request completed"
            events.append(
                {
                    "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
                    "service": service,
                    "level": level,
                    "message": message,
                    "latency_ms": latency,
                    "trace_id": f"sample-{seed}-{event_number:06d}",
                    "attributes": {"synthetic": True, "scenario": scenario, "seed": seed},
                }
            )
    events.sort(key=lambda event: (event["timestamp"], event["trace_id"]))
    return events


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate deterministic synthetic SentinelAI log events.")
    parser.add_argument("--scenario", choices=SCENARIOS, default="normal_operation")
    parser.add_argument("--service", default="checkout")
    parser.add_argument("--duration-minutes", type=int, default=30)
    parser.add_argument("--events-per-minute", type=int, default=5)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--start-time", default=DEFAULT_START, help="ISO-8601 timestamp; default is fixed for reproducibility")
    parser.add_argument("--output", default="data/sample_logs.json")
    parser.add_argument("--post-url", help="Optional ingestion URL, e.g. http://127.0.0.1:8000/api/v1/logs/ingest")
    args = parser.parse_args()

    events = generate_events(
        scenario=args.scenario,
        service=args.service,
        duration_minutes=args.duration_minutes,
        events_per_minute=args.events_per_minute,
        seed=args.seed,
        start_time=parse_datetime(args.start_time),
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"events": events}, indent=2) + "\n", encoding="utf-8")
    print(f"Generated {len(events)} deterministic {args.scenario} events at {output}")

    if args.post_url:
        payload = json.dumps({"events": events}).encode("utf-8")
        request = Request(args.post_url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(request, timeout=20) as response:
            result = json.loads(response.read().decode("utf-8"))
        print(f"API accepted {result['accepted']} events across {len(result['service_counts'])} services")


if __name__ == "__main__":
    main()
