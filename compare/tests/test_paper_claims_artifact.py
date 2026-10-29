# SPDX-License-Identifier: Apache-2.0 OR MIT
"""The gate must refuse to pass when a paper number drifts from its artifact.

The claims manifest is checked against the frozen measurement when that artifact
is present in this checkout. A clean clone without local measurements skips the
check instead of failing (the manifest itself is still linted).
"""

import json
from pathlib import Path

import pytest

from scripts.evaluation.verify_claims import SCHEMA, main

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "docs" / "paper" / "claims.json"


def load_manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_manifest_is_well_formed_and_names_its_measurement():
    manifest = load_manifest()
    assert manifest["schema"] == SCHEMA
    assert manifest["measurement"].strip()
    assert manifest["claims"], "a claims file without claims proves nothing"
    for claim in manifest["claims"]:
        assert claim["id"] and claim["statement"]
        assert claim["artifact"] and claim["path"]
        expectations = [key for key in ("equals", "not_equals", "close_to")
                        if key in claim]
        assert len(expectations) == 1, claim["id"]
        if "close_to" in claim:
            assert float(claim["tolerance"]) >= 0


def test_claims_hold_against_the_recorded_measurement():
    manifest = load_manifest()
    artifact_root = Path(manifest["artifact_root"])
    if not artifact_root.is_absolute():
        artifact_root = ROOT / artifact_root
    if not artifact_root.is_dir():
        pytest.skip("measurement artifact is not part of this checkout")
    assert main(["--manifest", str(MANIFEST)]) == 0, (
        "a recorded paper number no longer matches its artifact; update claims.json only "
        "after a new measurement"
    )
