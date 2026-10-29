# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Scenario/run identifiers are replaced consistently in annotator views."""

import json
import re

from tools.annotator.blinding import blind_identifiers


def test_bound_identifiers_are_replaced_in_values_paths_and_files():
    raw = (
        '{"scenario_id":"delegation-chain",'
        '"run_id":"delegation-chain-a1b2c3d4",'
        '"cwd":"/tmp/delegation-chain/run",'
        '"note":"delegation-chain is the scenario"}'
    ).encode()
    text, redactions = blind_identifiers(
        raw, ("delegation-chain-a1b2c3d4", "delegation-chain")
    )
    assert redactions == 4
    assert "delegation-chain" not in text
    assert json.loads(text)["scenario_id"].startswith("blinded-")
    scenario_token = json.loads(text)["scenario_id"]
    assert text.count(scenario_token) == 3


def test_keys_and_longer_words_are_not_rewritten():
    raw = b'{"run":"run","run_id":"keep-me","running":true}'
    text, redactions = blind_identifiers(raw, ("run",))
    parsed = json.loads(text)
    tokens = [key for key in parsed if key.startswith("blinded-")]
    assert redactions == 2
    assert len(tokens) == 1
    token = tokens[0]
    assert re.fullmatch(r"blinded-[0-9a-f]{12}", token)
    assert parsed == {token: token, "run_id": "keep-me", "running": True}


def test_non_json_evidence_is_still_redacted():
    text, redactions = blind_identifiers(
        b"plain delegation-chain bytes\n", ("delegation-chain",)
    )
    assert redactions == 1
    assert "delegation-chain" not in text


def test_missing_identifiers_leave_evidence_unchanged():
    raw = b'{"native_id":"r1","pid":42}'
    text, redactions = blind_identifiers(raw, (None, "", "absent"))
    assert text == raw.decode()
    assert redactions == 0


def test_the_same_identifier_always_maps_to_the_same_token():
    first, _ = blind_identifiers(b'{"a":"scenario"}', ("scenario",))
    second, _ = blind_identifiers(b'{"a":"scenario"}', ("scenario",))
    assert first == second


def test_binding_identifiers_cover_all_identity_fields():
    from tools.annotator.blinding import binding_identifiers

    assert binding_identifiers({
        "scenario_id": "scenario", "run_id": "run-1",
        "source_run_uuid": "uuid-1", "harness_run_id": "harness-1",
        "collector_run_id": "collector-1", "data_release_id": "release",
        "empty": "",
    }) == ("scenario", "run-1", "uuid-1", "harness-1", "collector-1")
    assert binding_identifiers(None) == ()
