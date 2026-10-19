#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Freeze raw wire bytes from a `tls_capture` C-level capture into test fixtures.

Why this exists
---------------
`bpftools/tls_capture/tls_capture.c` prints each captured buffer as a JSON
string.  Bytes 32..126 are literal, valid UTF-8 sequences are copied verbatim,
and every other byte (including invalid UTF-8 and control bytes) is emitted as
`\\u00XX`.  Parsing that with a JSON library is *lossy*: `\\u00e9` becomes the
two UTF-8 bytes C3 A9, so the original single byte E9 is gone.  This script
therefore parses the raw JSON text byte by byte and maps `\\u00XX` back to the
single byte XX.  It then asserts `len(recovered) == buf_size`, which is the
self-check that makes the recovery falsifiable.

Privacy: captured requests contain real bearer tokens, workspace paths and
conversation history.  `--sanitize` rewrites them *in place with equal-length
replacements* where the body is affected, so Content-Length and the JSON body
stay valid, then re-checks the result.

Usage:
    # show sessions in a capture
    python3 scripts/wire/extract_wire.py list /tmp/capture.jsonl

    # write request.http / response.http for one TLS session
    python3 scripts/wire/extract_wire.py extract /tmp/capture.jsonl \
        --ssl 610977632 --out-dir collector/tests/fixtures/wire/openclaw-... \
        --sanitize
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import pathlib

HOME_USER = pathlib.Path.home().name.encode()
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

SIMPLE_ESCAPES = {
    ord("n"): 0x0A,
    ord("r"): 0x0D,
    ord("t"): 0x09,
    ord("b"): 0x08,
    ord("f"): 0x0C,
    ord('"'): 0x22,
    ord("\\"): 0x5C,
}
META_INT_KEYS = ("ssl_ptr", "len", "buf_size", "pid", "tid", "timestamp_ns")
META_STR_KEYS = ("function", "comm")


class ExtractionError(RuntimeError):
    pass


def extract_data(line: bytes) -> bytes:
    """Return the exact bytes the C program received, or raise."""
    key = b'"data":"'
    start = line.find(key)
    if start < 0:
        raise ExtractionError("no data field")
    i = start + len(key)
    out = bytearray()
    while i < len(line):
        byte = line[i]
        if byte == 0x22:  # closing quote
            return bytes(out)
        if byte == 0x5C:  # backslash
            esc = line[i + 1]
            if esc in SIMPLE_ESCAPES:
                out.append(SIMPLE_ESCAPES[esc])
                i += 2
                continue
            if esc == ord("u"):
                value = int(line[i + 2 : i + 6], 16)
                if value > 0xFF:
                    raise ExtractionError(
                        f"\\u{value:04x} cannot come from a single captured byte"
                    )
                out.append(value)
                i += 6
                continue
            raise ExtractionError(f"unknown escape \\{chr(esc)}")
        out.append(byte)
        i += 1
    raise ExtractionError("unterminated data string")


def parse_meta(line: bytes) -> dict:
    meta: dict = {}
    for key in META_INT_KEYS:
        match = re.search(rb'"' + key.encode() + rb'":(\d+)', line)
        if match:
            meta[key] = int(match.group(1))
    for key in META_STR_KEYS:
        match = re.search(rb'"' + key.encode() + rb'":"((?:[^"\\]|\\.)*)"', line)
        if match:
            meta[key] = match.group(1).decode("utf-8", "replace")
    match = re.search(rb'"truncated":(true|false)', line)
    if match:
        meta["truncated"] = match.group(1) == b"true"
    return meta


B64_KEY = b'"data_b64":"'


def extract_payload(line: bytes, buf_size: int) -> bytes | None:
    """Recover the exact payload from a capture line.

    New captures carry `data_b64` (authoritative). Old captures only carry the
    escaped `data` string, which must be parsed byte-by-byte. Both paths assert
    the recovered length equals `buf_size`; a mismatch means bytes were lost.
    """
    index = line.find(B64_KEY)
    if index >= 0:
        start = index + len(B64_KEY)
        end = line.find(b'"', start)
        if end < 0:
            raise ExtractionError("unterminated data_b64 string")
        encoded = line[start:end]
        if encoded == b"null":
            return None
        try:
            data = base64.b64decode(encoded, validate=True)
        except Exception as exc:  # binascii.Error is not stable across versions
            raise ExtractionError(f"invalid data_b64: {exc}") from exc
    else:
        data = extract_data(line)
    if data is None:
        return None
    if len(data) != buf_size:
        raise ExtractionError(
            f"decoded {len(data)} bytes, buf_size says {buf_size}"
        )
    return data


def read_events(path: Path) -> list[dict]:
    events: list[dict] = []
    with path.open("rb") as handle:
        for lineno, line in enumerate(handle, 1):
            line = line.rstrip(b"\n")
            if not line.startswith(b"{"):
                continue
            meta = parse_meta(line)
            if not meta.get("buf_size"):
                continue
            data = extract_payload(line, meta["buf_size"])
            if data is None:
                continue
            if meta.get("truncated"):
                raise ExtractionError(
                    f"{path}:{lineno}: capture is truncated; refusing to freeze it"
                )
            meta["data"] = data
            meta["lineno"] = lineno
            events.append(meta)
    if not events:
        raise ExtractionError(f"{path}: no events with payload found")
    return events


AUTH_RE = re.compile(rb"(?i)(\r\nauthorization:[ \t]*)[^\r\n]*")
SESSION_ID_RE = re.compile(rb"(?i)(\r\nx-claude-code-session-id:[ \t]*)[^\r\n]*")
BEARER_RE = re.compile(rb"(?i)\r\nauthorization:[ \t]*Bearer[ \t]+([^\r\n]+)")
# Real provider keys are long opaque tokens. "sk-authorized" inside prose must
# not be mistaken for one, so require a long hex-ish run.
PROVIDER_TOKEN_RE = re.compile(rb"sk-[0-9A-Fa-f]{20,}")


def sanitize_request(request: bytes) -> tuple[bytes, list[str], list[bytes]]:
    """Rewrite identifying values, returning (bytes, notes, secrets_seen)."""
    notes: list[str] = []
    secrets_seen = [m.group(1) for m in BEARER_RE.finditer(request)]
    out, count = AUTH_RE.subn(rb"\1Bearer REDACTED", request)
    if count:
        notes.append(f"authorization header value -> Bearer REDACTED ({count})")
    out, count = SESSION_ID_RE.subn(
        rb"\1 00000000-0000-4000-8000-000000000000", out
    )
    if count:
        notes.append(f"x-claude-code-session-id -> zero uuid ({count})")
    # Equal-length so Content-Length and JSON offsets stay valid.
    count = out.count(b"lzk")
    if count:
        out = out.replace(b"lzk", b"usr")
        notes.append(f"'lzk' -> 'usr' equal-length replacement ({count})")
    return out, notes, secrets_seen


def verify_sanitized(raw: bytes, secrets_seen: list[bytes] | None = None) -> list[str]:
    problems: list[str] = []
    for secret in secrets_seen or []:
        if secret and secret in raw:
            problems.append("sanitized bytes still contain the captured bearer token")
    for pattern, label in (
        (PROVIDER_TOKEN_RE, "provider token"),
        (rb"/home/[^/\s\"']+", "home path"),
        (rb"(?i)[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "email"),
    ):
        if re.search(pattern, raw):
            problems.append(f"sanitized bytes still match a {label}")
    if HOME_USER and HOME_USER in raw:
        problems.append("sanitized bytes still contain the home username")
    return problems


def split_http_message(raw: bytes) -> tuple[bytes, bytes]:
    marker = b"\r\n\r\n"
    pos = raw.find(marker)
    if pos < 0:
        raise ExtractionError("HTTP head terminator not found")
    return raw[: pos + len(marker)], raw[pos + len(marker) :]


def check_http_message(raw: bytes) -> list[str]:
    problems: list[str] = []
    head, body = split_http_message(raw)
    lines = head[:-4].split(b"\r\n")
    if not lines or not lines[0]:
        problems.append("empty start line")
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if b":" not in line:
            problems.append(f"malformed header line: {line[:40]!r}")
            continue
        name, value = line.split(b":", 1)
        headers.setdefault(name.strip().lower().decode("latin-1"), value.strip().decode("latin-1"))
    if "content-length" in headers:
        declared = int(headers["content-length"])
        if declared != len(body):
            problems.append(f"Content-Length {declared} != body {len(body)}")
        if b"content-type" in headers and b"json" in head.lower():
            try:
                json.loads(body)
            except ValueError as exc:
                problems.append(f"body is not valid JSON: {exc}")
    return problems


def cmd_list(args: argparse.Namespace) -> int:
    events = read_events(Path(args.capture))
    groups: dict[int, list[dict]] = defaultdict(list)
    for event in events:
        groups[event["ssl_ptr"]].append(event)
    for ssl_ptr, group in sorted(groups.items()):
        writes = sum(e["buf_size"] for e in group if e["function"] == "WRITE/SEND")
        reads = sum(e["buf_size"] for e in group if e["function"] == "READ/RECV")
        comms = sorted({e.get("comm", "?") for e in group})
        print(
            f"ssl_ptr={ssl_ptr} events={len(group)} "
            f"write_bytes={writes} read_bytes={reads} comm={comms}"
        )
    return 0


def split_pairs(events: list[dict]) -> list[dict]:
    """Split a keep-alive session into request/response pairs.

    A request begins at a write whose first line is `METHOD target HTTP/1.1`.
    HTTP/1.1 without pipelining never interleaves two requests, so every event
    after a request start belongs to that pair until the next request start.
    """
    request_line = re.compile(
        rb"^(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS) \S+ HTTP/1\.[01]\r\n"
    )
    pairs: list[dict] = []
    current: dict | None = None
    for event in events:
        if request_line.match(event["data"][:512]):
            current = {"writes": [], "reads": []}
            pairs.append(current)
        if current is None:
            raise ExtractionError(
                "capture starts mid-conversation; no request line before payload"
            )
        key = "writes" if event["function"] == "WRITE/SEND" else "reads"
        current[key].append(event)
    return pairs


def cmd_extract(args: argparse.Namespace) -> int:
    events = read_events(Path(args.capture))
    group = [e for e in events if e["ssl_ptr"] == args.ssl]
    if not group:
        raise ExtractionError(f"ssl_ptr {args.ssl} not present in capture")

    if args.pair is not None:
        pairs = split_pairs(group)
        if not (0 <= args.pair < len(pairs)):
            raise ExtractionError(
                f"pair {args.pair} out of range; capture has {len(pairs)} pair(s)"
            )
        selected = pairs[args.pair]
        writes = b"".join(e["data"] for e in selected["writes"])
        reads = b"".join(e["data"] for e in selected["reads"])
        print(
            f"pair {args.pair}/{len(pairs) - 1}: "
            f"{len(selected['writes'])} write(s), {len(selected['reads'])} read(s)"
        )
    else:
        writes = b"".join(e["data"] for e in group if e["function"] == "WRITE/SEND")
        reads = b"".join(e["data"] for e in group if e["function"] == "READ/RECV")
    if not writes or not reads:
        raise ExtractionError("selected session has no request or no response bytes")
    # The request body may legitimately contain the text "POST "; only the
    # first bytes decide whether this stream starts a request.
    if not writes.startswith(b"POST "):
        raise ExtractionError(
            f"request stream does not start with a POST line: {writes[:40]!r}"
        )
    if not reads.startswith(b"HTTP/1.1 "):
        raise ExtractionError(
            f"response stream does not start with an HTTP/1.1 status line: {reads[:40]!r}"
        )

    notes: list[str] = []
    secrets_seen: list[bytes] = []
    if args.sanitize:
        request, notes, secrets_seen = sanitize_request(writes)
        problems = verify_sanitized(request, secrets_seen) + verify_sanitized(
            reads, secrets_seen
        )
        if problems:
            raise ExtractionError("; ".join(problems))
    else:
        request = writes

    problems = check_http_message(request) + check_http_message(reads)
    if problems:
        raise ExtractionError("; ".join(problems))

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "request.http").write_bytes(request)
    (out_dir / "response.http").write_bytes(reads)

    for name in ("request.http", "response.http"):
        payload = (out_dir / name).read_bytes()
        print(
            f"{name}: {len(payload)} bytes sha256={hashlib.sha256(payload).hexdigest()}"
        )
    for note in notes:
        print(f"sanitized: {note}")
    return 0


def dechunk_strict(body: bytes) -> bytes:
    """Strict RFC 9112 §7.1.3 decoder used only to produce the golden."""
    out = bytearray()
    pos = 0
    while True:
        line_end = body.find(b"\r\n", pos)
        if line_end < 0:
            raise ExtractionError("chunked body ended without a size line")
        size_field = body[pos:line_end].split(b";", 1)[0].strip()
        try:
            size = int(size_field, 16)
        except ValueError as exc:
            raise ExtractionError(f"bad chunk size {size_field!r}") from exc
        pos = line_end + 2
        if size == 0:
            # Trailer section ends at the first empty line.
            while True:
                trailer_end = body.find(b"\r\n", pos)
                if trailer_end < 0:
                    raise ExtractionError("missing CRLF after last-chunk")
                if trailer_end == pos:
                    return bytes(out)
                pos = trailer_end + 2
        if pos + size > len(body):
            raise ExtractionError(
                f"chunk declares {size} bytes, only {len(body) - pos} remain"
            )
        out += body[pos : pos + size]
        pos += size
        if body[pos : pos + 2] != b"\r\n":
            raise ExtractionError("chunk data is not followed by CRLF")
        pos += 2


def cmd_decode_line(args: argparse.Namespace) -> int:
    """Write the raw bytes of every payload line to stdout."""
    out = sys.stdout.buffer
    written = 0
    with Path(args.capture).open("rb") as handle:
        for lineno, line in enumerate(handle, 1):
            line = line.rstrip(b"\n")
            if not line.startswith(b"{"):
                continue
            meta = parse_meta(line)
            if not meta.get("buf_size"):
                continue
            try:
                payload = extract_payload(line, meta["buf_size"])
            except ExtractionError as exc:
                print(f"{args.capture}:{lineno}: {exc}", file=sys.stderr)
                return 1
            if payload is not None:
                out.write(payload)
                written += len(payload)
    print(f"decoded {written} bytes", file=sys.stderr)
    return 0


def cmd_golden(args: argparse.Namespace) -> int:
    """Produce golden.sse with the system `zstd` binary (independent decoder)."""
    fixture = Path(args.fixture_dir)
    raw = (fixture / "response.http").read_bytes()
    head, body = split_http_message(raw)
    head_lower = head.lower()
    if b"content-encoding: zstd" not in head_lower:
        raise ExtractionError("response is not zstd-encoded; refusing to guess")
    if b"transfer-encoding: chunked" not in head_lower:
        raise ExtractionError("response is not chunked; update the golden recipe")
    payload = dechunk_strict(body)
    proc = subprocess.run(["zstd", "-d", "-c"], input=payload, capture_output=True)
    if proc.returncode != 0:
        raise ExtractionError(f"zstd -d failed: {proc.stderr.decode()[:300]}")
    golden = proc.stdout
    if not golden:
        raise ExtractionError("zstd produced an empty golden")
    (fixture / "golden.sse").write_bytes(golden)
    problems = verify_sanitized(golden)
    if problems:
        raise ExtractionError("golden contains identifying data: " + "; ".join(problems))
    version = subprocess.run(["zstd", "--version"], capture_output=True, text=True)
    print(
        f"golden.sse: {len(golden)} bytes "
        f"sha256={hashlib.sha256(golden).hexdigest()}"
    )
    print(f"zstd: {version.stdout.strip() or version.stderr.strip()}")
    print(f"dechunked payload: {len(payload)} bytes magic={payload[:4].hex()}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    list_parser = sub.add_parser("list", help="list TLS sessions in a capture")
    list_parser.add_argument("capture")
    list_parser.set_defaults(func=cmd_list)

    extract_parser = sub.add_parser("extract", help="write fixtures for one session")
    extract_parser.add_argument("capture")
    extract_parser.add_argument("--ssl", type=int, required=True)
    extract_parser.add_argument(
        "--pair",
        type=int,
        default=None,
        help="keep-alive pair index (0-based); default: whole session",
    )
    extract_parser.add_argument("--out-dir", required=True)
    extract_parser.add_argument("--sanitize", action="store_true")
    extract_parser.set_defaults(func=cmd_extract)

    decode_parser = sub.add_parser(
        "decode-line", help="write the raw payload bytes of every capture line"
    )
    decode_parser.add_argument("capture")
    decode_parser.set_defaults(func=cmd_decode_line)

    golden_parser = sub.add_parser(
        "golden", help="decode response.http with the system zstd binary"
    )
    golden_parser.add_argument("fixture_dir")
    golden_parser.set_defaults(func=cmd_golden)

    args = parser.parse_args()
    try:
        return args.func(args)
    except ExtractionError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
