# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Decode the collector's compressed analysis-checkpoint payloads.

The Rust writer stores large checkpoint JSON as zstd + base64 with a zstd1:
prefix (persistence/codec.rs). Python readers must decode before json.loads;
plain legacy rows pass through unchanged.
"""
from __future__ import annotations

import base64
import subprocess

PREFIX = "zstd1:"


def decode_checkpoint_payload(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("checkpoint payload must be text")
    if not value.startswith(PREFIX):
        return value
    compressed = base64.b64decode(value[len(PREFIX):], validate=True)
    result = subprocess.run(
        ["zstd", "-d", "-q", "-c"], input=compressed,
        capture_output=True, check=False,
    )
    if result.returncode:
        raise ValueError(
            "zstd checkpoint decode failed: "
            + result.stderr.decode("utf-8", "replace")[:200]
        )
    return result.stdout.decode("utf-8")
