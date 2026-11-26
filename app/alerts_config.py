"""Local simulated alert channel and cooldown configuration."""

import os


def _positive_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


ALERT_COOLDOWN_MINUTES = _positive_int("SENTINEL_ALERT_COOLDOWN_MINUTES", 15)
ALERT_CHANNEL = "local_webhook"
ALERT_DELIVERY_STATUS = "simulated"
