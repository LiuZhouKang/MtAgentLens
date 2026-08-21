from __future__ import annotations

from .base import BoundaryType


_SEVERITY_BY_NORMALIZED_VALUE = {
    "critical": "Critical",
    "high": "Critical",
    "error": "Critical",
    "warning": "Warning",
    "warn": "Warning",
    "medium": "Warning",
}

_BOUNDARY_BY_TOKEN = {
    "resource": BoundaryType.RESOURCE,
    "resourceboundary": BoundaryType.RESOURCE,
    "file": BoundaryType.RESOURCE,
    "secret": BoundaryType.RESOURCE,
    "network": BoundaryType.RESOURCE,
    "networkboundary": BoundaryType.RESOURCE,
    "filesystem": BoundaryType.RESOURCE,
    "filesystemboundary": BoundaryType.RESOURCE,
    "endpoint": BoundaryType.RESOURCE,
    "auditendpoint": BoundaryType.RESOURCE,
    "content": BoundaryType.CONTENT,
    "contentboundary": BoundaryType.CONTENT,
    "prompt": BoundaryType.CONTENT,
    "injection": BoundaryType.CONTENT,
    "delegation": BoundaryType.DELEGATION,
    "delegationboundary": BoundaryType.DELEGATION,
    "handoff": BoundaryType.DELEGATION,
    "exec": BoundaryType.EXEC_ARG,
    "execarg": BoundaryType.EXEC_ARG,
    "execargboundary": BoundaryType.EXEC_ARG,
    "execution": BoundaryType.EXEC_ARG,
    "executionargument": BoundaryType.EXEC_ARG,
    "command": BoundaryType.EXEC_ARG,
    "process": BoundaryType.EXEC_ARG,
    "tool": BoundaryType.EXEC_ARG,
    "bash": BoundaryType.EXEC_ARG,
    "write": BoundaryType.EXEC_ARG,
    "edit": BoundaryType.EXEC_ARG,
}


_SEVERITY_RANKS = {
    "Info": 1,
    "Warning": 2,
    "Critical": 3,
}


def normalize_severity(value: str | None) -> str:
    if value is None:
        return "Info"
    return _SEVERITY_BY_NORMALIZED_VALUE.get(value.strip().lower(), "Info")


def severity_rank(value: str | None) -> int:
    return _SEVERITY_RANKS[normalize_severity(value)]


def normalize_boundary(value: str | None) -> BoundaryType | None:
    if value is None:
        return None
    normalized = "".join(character for character in value.lower() if character.isalnum())
    return _BOUNDARY_BY_TOKEN.get(normalized)
