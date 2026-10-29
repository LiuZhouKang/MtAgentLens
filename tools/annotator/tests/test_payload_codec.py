import base64
import subprocess

import pytest

from tools.annotator.payload_codec import decode_checkpoint_payload


def _compressed(text: str) -> str:
    raw = text.encode("utf-8")
    packed = subprocess.run(
        ["zstd", "-3", "-q", "-c"], input=raw, capture_output=True, check=True
    ).stdout
    return "zstd1:" + base64.b64encode(packed).decode("ascii")


def test_legacy_plain_checkpoint_passes_through():
    assert decode_checkpoint_payload('{"run_id":"r"}') == '{"run_id":"r"}'


def test_zstd_checkpoint_round_trips():
    payload = '{"run_id":"r","nested":{"a":1}}'
    assert decode_checkpoint_payload(_compressed(payload)) == payload


def test_corrupt_zstd_checkpoint_fails_visible():
    with pytest.raises(ValueError):
        decode_checkpoint_payload("zstd1:not-base64")
