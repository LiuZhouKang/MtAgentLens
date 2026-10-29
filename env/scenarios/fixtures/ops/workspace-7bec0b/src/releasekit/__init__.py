"""Dependency-free helpers shared by the release pipeline stages."""

from __future__ import annotations

from .config import (
    HostEntry,
    host_is_reserved,
    is_placeholder,
    looks_live_secret,
    parse_env,
    parse_known_hosts,
)
from .manifest import build_manifest, sha256_file, write_manifest
from .release import compare_versions, format_summary_lines, parse_version, release_name

__all__ = [
    "HostEntry",
    "build_manifest",
    "compare_versions",
    "format_summary_lines",
    "host_is_reserved",
    "is_placeholder",
    "looks_live_secret",
    "parse_env",
    "parse_known_hosts",
    "parse_version",
    "release_name",
    "sha256_file",
    "write_manifest",
]

__version__ = "1.4.0"
