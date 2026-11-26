from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from sqlalchemy.engine import make_url


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("SENTINEL_DATABASE_URL", "sqlite:///./data/sentinelai.db")
    app_name: str = "SentinelAI Observability API"
    version: str = "0.1.0"


settings = Settings()


def ensure_sqlite_parent(database_url: str) -> None:
    """Create the local SQLite parent directory when the URL points to a file."""
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite" or url.database in (None, "", ":memory:"):
        return
    Path(url.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
