#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Derive the transport-roundtrip fixture from a real wire response.

This does NOT pretend to be a live capture. It takes the frozen response bytes
(which *are* first-hand) and renders them twice, exactly as the C producer
would: once with the legacy `data` escaping (no `data_b64`) and once with the
additive `data_b64` field. The Rust test then proves both lines decode to the
same bytes.

Usage:
    python3 -I scripts/wire/make_transport_fixture.py
"""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WIRE = ROOT / "collector" / "tests" / "fixtures" / "wire"
SOURCE = WIRE / "claude-code-deepseek-zstd-chunked" / "response.http"
OUT = WIRE / "transport"


def utf8_len(data: bytes, index: int) -> int:
    """Mirror json_validate_utf8_char() in bpftools/include/json_escape.h."""
    first = data[index]
    if first < 0x80:
        return 1
    if first & 0xE0 == 0xC0:
        length = 2
        codepoint = first & 0x1F
    elif first & 0xF0 == 0xE0:
        length = 3
        codepoint = first & 0x0F
    elif first & 0xF8 == 0xF0:
        length = 4
        codepoint = first & 0x07
    else:
        return 0
    if index + length > len(data):
        return 0
    for offset in range(1, length):
        byte = data[index + offset]
        if byte & 0xC0 != 0x80:
            return 0
        codepoint = (codepoint << 6) | (byte & 0x3F)
    if length == 2 and codepoint < 0x80:
        return 0
    if length == 3 and (codepoint < 0x800 or 0xD800 <= codepoint <= 0xDFFF):
        return 0
    if length == 4 and (codepoint < 0x10000 or codepoint > 0x10FFFF):
        return 0
    return length


def c_escape(data: bytes) -> bytes:
    out = bytearray()
    index = 0
    named = {
        0x0A: b"\\n",
        0x0D: b"\\r",
        0x09: b"\\t",
        0x08: b"\\b",
        0x0C: b"\\f",
    }
    while index < len(data):
        byte = data[index]
        if byte == 0x22:
            out += b'\\"'
        elif byte == 0x5C:
            out += b"\\\\"
        elif byte in named:
            out += named[byte]
        elif 32 <= byte <= 126:
            out.append(byte)
        elif byte >= 128:
            length = utf8_len(data, index)
            if length > 0:
                out += data[index : index + length]
                index += length - 1
            else:
                out += f"\\u{byte:04x}".encode()
        else:
            out += f"\\u{byte:04x}".encode()
        index += 1
    return bytes(out)


def line(payload: bytes, with_base64: bool) -> bytes:
    escaped = c_escape(payload)
    head = (
        b'{"function":"READ/RECV","timestamp_ns":1,"comm":"fixture",'
        b'"pid":1,"len":' + str(len(payload)).encode() + b","
        b'"buf_size":' + str(len(payload)).encode() + b","
        b'"uid":1000,"tid":1,"ssl_ptr":1,"latency_ms":0,'
        b'"is_handshake":false,"data":"'
    )
    middle = b'"'
    if with_base64:
        middle += b',"data_b64":"' + base64.b64encode(payload) + b'"'
    tail = b',"truncated":false}'
    return head + escaped + middle + tail + b"\n"


def entry(path: Path) -> dict:
    payload = path.read_bytes()
    return {"bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}


def main() -> int:
    if not SOURCE.exists():
        raise SystemExit(f"ERROR: missing source fixture {SOURCE}")
    payload = SOURCE.read_bytes()
    OUT.mkdir(parents=True, exist_ok=True)

    (OUT / "payload.bin").write_bytes(payload)
    (OUT / "capture-escaped.jsonl").write_bytes(line(payload, with_base64=False))
    (OUT / "capture-base64.jsonl").write_bytes(line(payload, with_base64=True))

    manifest_path = WIRE / "MANIFEST.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["fixtures"].pop("transport-roundtrip", None)
    manifest["fixtures"]["transport"] = {
        "derived_from": "claude-code-deepseek-zstd-chunked/response.http",
        "why": (
            "Proves the legacy backslash-u escaping and the additive data_b64 "
            "field decode to identical bytes, and that decoded_len == buf_size. "
            "Derived, not a live capture; the source bytes are first-hand."
        ),
        "files": {
            name: entry(OUT / name)
            for name in ("payload.bin", "capture-escaped.jsonl", "capture-base64.jsonl")
        },
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")

    for name in ("payload.bin", "capture-escaped.jsonl", "capture-base64.jsonl"):
        info = entry(OUT / name)
        print(f"{name}: {info['bytes']} bytes sha256={info['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
