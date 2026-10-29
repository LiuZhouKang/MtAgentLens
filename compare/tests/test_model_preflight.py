# SPDX-License-Identifier: Apache-2.0 OR MIT
"""A campaign must not start when the vendor will reject every run."""

from compare.scripts.run.model_preflight import blocks_campaign, classify


def test_a_successful_probe_allows_the_campaign():
    assert classify(200, "{}") == "ok"
    assert not blocks_campaign({"decision": "ok"})


def test_insufficient_balance_blocks_the_campaign():
    assert classify(402, '{"error":{"message":"Insufficient Balance"}}') == "insufficient_balance"
    assert blocks_campaign({"decision": "insufficient_balance"})


def test_quota_and_billing_shapes_also_block():
    assert classify(429, '{"error":{"code":"insufficient_quota"}}') == "insufficient_balance"
    assert classify(400, '{"error":{"message":"credit balance is too low"}}') == "insufficient_balance"


def test_unauthorized_blocks_and_generic_errors_do_not():
    assert classify(401, "{}") == "unauthorized"
    assert blocks_campaign({"decision": "unauthorized"})
    assert classify(500, "upstream down") == "error"
    assert not blocks_campaign({"decision": "error"})
    assert not blocks_campaign({"decision": "unavailable"})
