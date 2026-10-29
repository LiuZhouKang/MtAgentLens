"""Runtime configuration for ledger-api (standard library only).

The environment contract lives in ``config/env.schema.json``; this module is
the code side of it. Keep :data:`REQUIRED_VARS` and :data:`OPTIONAL_VARS` in
sync with the schema, and make sure newly added variables are read here so the
review of ``.env.example`` documents what the service actually consumes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

REQUIRED_VARS = (
    "API_KEY",
    "DATABASE_URL",
    "JWT_SIGNING_SECRET",
    "OBJECT_STORE_SECRET_KEY",
    "WEBHOOK_SIGNING_SECRET",
)

OPTIONAL_VARS = (
    "DATABASE_POOL_SIZE",
    "FEATURE_FLAGS",
    "LEDGER_API_HOST",
    "LEDGER_API_LOG_LEVEL",
    "LEDGER_API_PORT",
    "OBJECT_STORE_ACCESS_KEY",
    "OBJECT_STORE_BUCKET",
    "SENTRY_DSN",
    "SESSION_TTL_SECONDS",
    "SMTP_PASSWORD",
)


class MissingConfigError(ValueError):
    """Raised when a required environment variable has no value."""


def parse_feature_flags(raw: str) -> dict[str, bool]:
    """Turn ``a=true,b=false`` into ``{"a": True, "b": False}``."""
    flags: dict[str, bool] = {}
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        name, _, value = item.partition("=")
        flags[name.strip()] = value.strip().lower() in ("1", "true", "yes", "on")
    return flags


@dataclass(frozen=True)
class Settings:
    """Resolved service settings."""

    host: str = "127.0.0.1"
    port: int = 8080
    log_level: str = "info"
    api_key: str = ""
    database_url: str = ""
    pool_size: int = 10
    feature_flags: dict[str, bool] | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        values = os.environ if env is None else env
        missing = [name for name in REQUIRED_VARS if not values.get(name)]
        if missing:
            raise MissingConfigError(
                "missing required environment variables: " + ", ".join(missing)
            )
        return cls(
            host=values.get("LEDGER_API_HOST", cls.host),
            port=int(values.get("LEDGER_API_PORT", cls.port)),
            log_level=values.get("LEDGER_API_LOG_LEVEL", cls.log_level),
            api_key=values["API_KEY"],
            database_url=values["DATABASE_URL"],
            pool_size=int(values.get("DATABASE_POOL_SIZE", cls.pool_size)),
            feature_flags=parse_feature_flags(
                values.get("FEATURE_FLAGS", "ledger-v2=false")
            ),
        )
