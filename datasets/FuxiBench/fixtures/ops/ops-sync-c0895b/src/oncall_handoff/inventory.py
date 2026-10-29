"""Inventory helpers for the ops-sync collection verification."""

from typing import List

SERVICE_MARKER = "- name:"


def read_service_names(text: str) -> List[str]:
    """Return the service names declared in a service-inventory document."""
    names: List[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(SERVICE_MARKER):
            name = stripped[len(SERVICE_MARKER):].strip().strip("'\"")
            if name:
                names.append(name)
    return names


def count_services(text: str) -> int:
    """Count the services declared in a service-inventory document."""
    return len(read_service_names(text))
