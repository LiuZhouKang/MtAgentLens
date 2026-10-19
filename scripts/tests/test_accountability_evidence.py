#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Contract tests for the accountability-evidence validator.

The validator is the only live enforcement of the evidence contract (the Rust
duplicate is not called). It used to read a contract key that did not exist
(`require_causal_link_object` vs `require_correlation_evidence_object`) and a
field that did not exist (`causal_link` vs `correlation_evidence`), so the one
hard requirement was silently skipped.

Run from the repository root:

    python3 -m unittest discover -s scripts/tests -p 'test_accountability_evidence.py'
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
ENV_SCRIPTS = SCRIPTS.parent / "env" / "scripts"
sys.path.insert(0, str(ENV_SCRIPTS))

import accountability_evidence as contract  # noqa: E402


def evidence(**overrides):
    base = {
        "kind": "accountability_evidence",
        "evidence_refs": ["evidence:1"],
        "directness": "direct",
        "boundary_id": "boundary.file_read",
    }
    base.update(overrides)
    return base


class EvidenceContractTests(unittest.TestCase):
    def test_missing_correlation_evidence_is_rejected(self) -> None:
        self.assertFalse(contract.validate_accountability_evidence(evidence()))

    def test_correlation_evidence_object_is_accepted(self) -> None:
        self.assertTrue(
            contract.validate_accountability_evidence(
                evidence(correlation_evidence={"matched_fields": {}})
            )
        )

    def test_a_non_object_correlation_evidence_is_rejected(self) -> None:
        self.assertFalse(
            contract.validate_accountability_evidence(
                evidence(correlation_evidence="not-an-object")
            )
        )

    def test_empty_evidence_refs_are_rejected(self) -> None:
        self.assertFalse(
            contract.validate_accountability_evidence(
                evidence(evidence_refs=[], correlation_evidence={})
            )
        )


if __name__ == "__main__":
    unittest.main()
