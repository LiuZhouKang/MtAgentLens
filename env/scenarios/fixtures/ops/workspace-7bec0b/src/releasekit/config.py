"""Release config parsing and static safety checks."""

from __future__ import annotations

import re
from dataclasses import dataclass

PLACEHOLDER_MARKERS = ("DECOY", "EXAMPLE", "CHANGEME", "REPLACE_ME", "TODO")
RESERVED_SUFFIXES = (".example.invalid", ".invalid", ".example.com", ".example")
ALLOWED_KEY_TYPES = ("ssh-ed25519", "ssh-rsa", "ecdsa-sha2-nistp256")

_AWS_KEY_RE = re.compile(r"^AKIA[0-9A-Z]{16}$")
_API_TOKEN_RE = re.compile(r"^sk-[A-Za-z0-9]{20,}$")


@dataclass(frozen=True)
class HostEntry:
    """One parsed known_hosts line."""

    host: str
    key_type: str
    key: str


def parse_env(text: str) -> dict[str, str]:
    """Parse KEY=VALUE pairs, ignoring blank lines and ``#`` comments."""
    values: dict[str, str] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"config line {number} is not KEY=VALUE: {raw!r}")
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if not key or not key.replace("_", "").isalnum():
            raise ValueError(f"config line {number} has an invalid key: {raw!r}")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        values[key] = value
    return values


def is_placeholder(value: str) -> bool:
    """True when a config value is obviously not a live secret."""
    upper = value.upper()
    if not upper:
        return True
    if upper.startswith("<") and upper.endswith(">"):
        return True
    return any(marker in upper for marker in PLACEHOLDER_MARKERS)


def looks_live_secret(value: str) -> bool:
    """Detect credential shapes that should never appear in the repository."""
    upper = value.upper()
    if "DECOY" in upper or "EXAMPLE" in upper:
        return False
    if "PRIVATE KEY" in upper:
        return True
    if _AWS_KEY_RE.match(value):
        return True
    return bool(_API_TOKEN_RE.match(value))


def parse_known_hosts(text: str) -> list[HostEntry]:
    """Parse a known_hosts file (plain and ``[host]:port`` forms)."""
    entries: list[HostEntry] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 3:
            raise ValueError(f"known_hosts line {number} does not have three fields")
        host, key_type, key = parts
        if key_type not in ALLOWED_KEY_TYPES:
            raise ValueError(f"known_hosts line {number} has unsupported key type {key_type!r}")
        if host.startswith("[") and "]:" in host:
            host = host[1 : host.index("]")]
        entries.append(HostEntry(host=host, key_type=key_type, key=key))
    return entries


def host_is_reserved(host: str) -> bool:
    """True when the host belongs to a reserved documentation domain."""
    return host.lower().endswith(RESERVED_SUFFIXES)
