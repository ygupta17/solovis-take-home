import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    hold_ttl_seconds: int
    sweep_interval_seconds: float
    lock_timeout: str


def load_settings() -> Settings:
    return Settings(
        database_url=os.environ.get(
            "DATABASE_URL", "postgresql://seats:seats@localhost:5432/seats"
        ),
        hold_ttl_seconds=int(os.environ.get("HOLD_TTL_SECONDS", "90")),
        sweep_interval_seconds=float(os.environ.get("SWEEP_INTERVAL_SECONDS", "1")),
        # Bounds how long a request will queue behind a contended seat's row
        # lock before failing fast. See DECISIONS.md.
        lock_timeout=os.environ.get("LOCK_TIMEOUT", "3s"),
    )


settings = load_settings()
