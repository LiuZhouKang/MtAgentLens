"""Runtime configuration for the report normalizer.

Values are read from the process environment. The runner environment is
populated from the tracked template at ``config/.env.example``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_TIMEOUT = "30s"
DEFAULT_MODE = "strict"


@dataclass(frozen=True)
class Settings:
    """Resolved runtime settings."""

    timeout_seconds: int
    mode: str


def parse_timeout(raw: str) -> int:
    """Parse a timeout value into whole seconds."""
    return int(raw)


def load_settings(environ=None) -> Settings:
    """Build settings from an environment mapping (defaults to ``os.environ``)."""
    env = os.environ if environ is None else environ
    return Settings(
        timeout_seconds=parse_timeout(env.get("REPORT_TIMEOUT", DEFAULT_TIMEOUT)),
        mode=env.get("REPORT_MODE", DEFAULT_MODE),
    )
