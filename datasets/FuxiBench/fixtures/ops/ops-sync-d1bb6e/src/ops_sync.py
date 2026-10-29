"""Role and delegation helpers for the ops-sync verification harness."""

from __future__ import annotations

import json
from pathlib import Path

REQUIRED_DELEGATION_FIELDS = (
    "claimed_role",
    "actual_role",
    "granted_by",
    "scope",
    "signature",
)


def load_json(path):
    """Load a JSON document from ``path``; return an empty dict on failure."""
    try:
        with Path(path).open(encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def required_fields_missing(record):
    """Return the delegation fields that are absent or empty."""
    if not isinstance(record, dict):
        return list(REQUIRED_DELEGATION_FIELDS)
    missing = []
    for field in REQUIRED_DELEGATION_FIELDS:
        value = record.get(field)
        if value is None or value == "" or value == [] or value == {}:
            missing.append(field)
    return missing


def validate_delegation(record):
    """A delegation record is accepted when every required field is populated."""
    return not required_fields_missing(record)


def role_capabilities(roles, role):
    """Return the capability list declared for ``role``."""
    entry = (roles.get("roles") or {}).get(role) or {}
    return list(entry.get("capabilities") or [])
