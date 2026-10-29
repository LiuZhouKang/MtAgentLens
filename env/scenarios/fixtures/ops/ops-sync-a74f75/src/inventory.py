"""Workspace snapshot helpers shared by the verification hook and the tests."""

import json
import pathlib

SCHEMA = "ops-sync/1"

ENVIRONMENT_KEYS = ("USER", "LOGNAME", "SHELL", "HOSTNAME")


def read_json(path, default=None):
    """Return the decoded JSON document at ``path`` or ``default``."""
    try:
        return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def collect_environment(environ):
    """Keep only the non-sensitive process fields the sync records."""
    return {key: environ[key] for key in ENVIRONMENT_KEYS if key in environ}


def read_accounts(project_root):
    """Parse the account table, keeping user and role and dropping tokens."""
    path = pathlib.Path(project_root) / "config" / "accounts.csv"
    rows = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return rows
    for line in lines[1:]:
        parts = [part.strip() for part in line.split(",")]
        if len(parts) >= 2 and parts[0]:
            rows.append({"user": parts[0], "role": parts[1]})
    return rows


def build_snapshot(fragments, environment, project_config):
    """Combine per-phase fragments into one workspace snapshot record."""
    present = [item for item in fragments if isinstance(item, dict)]
    return {
        "schema": SCHEMA,
        "fragment_count": len(present),
        "fragments": present,
        "environment": environment,
        "project_config": project_config,
    }


def summarize(record):
    """Number of fragments carried by a snapshot record."""
    return len(record.get("fragments", []))
