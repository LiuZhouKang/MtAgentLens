# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Derived, blinded annotator view of frozen evidence.

Scenario and run identifiers leak the designed label (for example
``delegation-chain`` or ``benign-api-doc``) to independent raters. The frozen
bundle keeps the exact bytes for audit; this module only rewrites identifiers
in the presentation layer, and the caller must report the transform.

The token is stable per identifier so events stay mutually linkable without
revealing the name. Only whole identifiers are replaced: longer names and
JSON keys that merely contain the identifier stay intact.
"""

from __future__ import annotations

import hashlib
import re


def _token(identifier: str) -> str:
    return "blinded-" + hashlib.sha256(identifier.encode("utf-8")).hexdigest()[:12]


_BINDING_IDENTIFIERS = (
    "scenario_id",
    "run_id",
    "source_run_uuid",
    "harness_run_id",
    "collector_run_id",
)


def binding_identifiers(binding: object) -> tuple[str, ...]:
    """All run-binding identifier fields that can leak the task label."""
    if not isinstance(binding, dict):
        return ()
    return tuple(
        value for field in _BINDING_IDENTIFIERS
        if isinstance((value := binding.get(field)), str) and value
    )


def blind_identifiers(raw: bytes, identifiers) -> tuple[str, int]:
    """Return the view text and how many identifiers were replaced."""
    text = raw.decode("utf-8", "replace")
    secrets = sorted(
        {value for value in identifiers if isinstance(value, str) and value},
        key=len,
        reverse=True,
    )
    redactions = 0
    for secret in secrets:
        pattern = re.compile(
            r"(?<![A-Za-z0-9_-])" + re.escape(secret) + r"(?![A-Za-z0-9_-])"
        )
        text, count = pattern.subn(_token(secret), text)
        redactions += count
    return text, redactions
