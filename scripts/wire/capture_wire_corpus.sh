#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0 OR MIT
# =============================================================================
# Capture a raw wire corpus from a live agent (P0.5 deliverable 1)
# =============================================================================
# Produces, for one TLS session:
#   <out>/raw/capture.jsonl     raw tls_capture C output (contains secrets: /tmp only)
#   <out>/request.http          byte-exact request, sanitized
#   <out>/response.http         byte-exact response (headers + chunked body)
#   <out>/golden.sse            independent `zstd -d` decode of the body
#
# Why a script: `cd X && sleep N &` runs in a subshell, `sudo -S` + background +
# `wait` truncates tool output, and `pkill -f` matches the invoking shell. All
# three have burned this repo before (HANDOFF §6).
#
# Requires: sudo (eBPF uprobe), a live agent, and system `zstd`.
#
# Usage:
#   ./scripts/wire/capture_wire_corpus.sh openclaw <out-dir>
#   ./scripts/wire/capture_wire_corpus.sh claude   <out-dir>
# =============================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TLS_CAPTURE="$ROOT/bpftools/.output/bin/tls_capture"
SUDO_PASS="${MTAGENTLENS_SUDO_PASSWORD:-}"
TARGET="${1:-}"
OUT="${2:-}"

die() { echo "ERROR: $*" >&2; exit 1; }

[ -x "$TLS_CAPTURE" ] || die "missing $TLS_CAPTURE (run: make -C bpftools .output/bin/tls_capture)"
[ -n "$TARGET" ] && [ -n "$OUT" ] || die "usage: $0 {openclaw|claude} <out-dir>"
command -v zstd >/dev/null || die "system zstd is required to build the golden"
mkdir -p "$OUT/raw"
TS="$(date +%Y%m%d-%H%M%S)"
RAW="$OUT/raw/capture-$TARGET-$TS.jsonl"

start_capture() { # start_capture <extra args...>
  printf '%s\n' "$SUDO_PASS" | sudo -S -p '' timeout -s INT 120 \
    "$TLS_CAPTURE" "$@" >"$RAW" 2>"$RAW.err" &
  CAP_PID=$!
}

trigger_openclaw() {
  local openclaw
  openclaw="$(command -v openclaw 2>/dev/null || true)"
  if [ -z "$openclaw" ]; then
    local candidate="$HOME/.nvm/versions/node/v24.19.0/lib/node_modules/openclaw/openclaw.mjs"
    [ -f "$candidate" ] || die "openclaw CLI not found"
    openclaw="node $candidate"
  fi
  # A fresh session key keeps the request small and free of prior conversation.
  $openclaw agent --agent main --session-key "p05-wire-corpus-$TS" \
    --message "Reply with exactly: PONG. Do not use tools." --timeout 60
}

trigger_claude() {
  ( cd /tmp && timeout 70 claude -p "Reply with exactly: PONG. Do not use tools." )
}

case "$TARGET" in
  openclaw)
    PID="$(pgrep -f 'openclaw/dist/index.js gateway' | head -1)"
    [ -n "$PID" ] || die "OpenClaw gateway is not running"
    NODE="$(readlink -f "/proc/$PID/exe")"
    start_capture -p "$PID" --binary-path "$NODE"
    sleep 3
    trigger_openclaw
    ;;
  claude)
    CLAUDE_ELF="$(readlink -f "$(command -v claude)")"
    [ -n "$CLAUDE_ELF" ] || die "claude binary not found"
    start_capture --binary-path "$CLAUDE_ELF"
    sleep 3
    trigger_claude
    ;;
  *) die "unknown target: $TARGET (expected openclaw|claude)" ;;
esac

wait "$CAP_PID"
rc=$?
echo "capture rc=$rc raw=$RAW"
[ -s "$RAW" ] || die "capture produced no events; check $RAW.err"

echo "=== sessions ==="
python3 "$ROOT/scripts/wire/extract_wire.py" list "$RAW"
cat <<EOF

Next: choose the ssl_ptr that carries one request + one response, then run
  python3 scripts/wire/extract_wire.py extract "$RAW" --ssl <ptr> --out-dir <out> --sanitize
  python3 scripts/wire/extract_wire.py golden <out>
Finally add provenance + hashes to collector/tests/fixtures/wire/MANIFEST.json.
The raw capture contains bearer tokens: keep it outside the repository.
EOF
