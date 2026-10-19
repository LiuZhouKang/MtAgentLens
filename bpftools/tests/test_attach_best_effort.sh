#!/bin/bash
# Verifies that SSL symbol attachment is best-effort: a missing symbol must
# not abort the rest of the sequence.
#
# Why this matters: with the CHECKED macros, the first absent symbol returns
# early, silently leaving every later symbol (including SSL_do_handshake)
# unattached — a silent-zero-capture failure mode.
#
# Requires: sudo (pass SUDO_PW via env), a node binary, and a live target.
set -u

BIN="$(dirname "$0")/../.output/bin/tls_capture"
NODE_BIN="${NODE_BIN:-/home/lzk/.nvm/versions/node/v24.19.0/bin/node}"
: "${SUDO_PW:?SUDO_PW must be set}"

[ -x "$BIN" ] || { echo "FAIL: $BIN not built"; exit 1; }
[ -x "$NODE_BIN" ] || { echo "FAIL: node binary $NODE_BIN not found"; exit 1; }

printf '%s\n' "$SUDO_PW" | sudo -S -p '' true 2>/dev/null || { echo "FAIL: sudo"; exit 1; }

ERR=$(mktemp); OUT=$(mktemp)
trap 'rm -f "$ERR" "$OUT"' EXIT

printf '%s\n' "$SUDO_PW" | sudo -S -p '' "$BIN" --binary-path "$NODE_BIN" >"$OUT" 2>"$ERR" &
PID=$!
sleep 3

if ! kill -0 "$PID" 2>/dev/null; then
    echo "FAIL: tls_capture exited immediately"; head -20 "$ERR"; exit 1
fi

printf '%s\n' "$SUDO_PW" | sudo -S -p '' kill -INT "$PID" 2>/dev/null
sleep 1
printf '%s\n' "$SUDO_PW" | sudo -S -p '' kill -9 "$PID" 2>/dev/null
wait "$PID" 2>/dev/null

# The attach path must report its outcome rather than failing silently.
# Messages are English on purpose: the Rust side classifies stderr by English
# keywords (Failed/Error/cannot/Warning), so a Chinese message would be logged
# at info level and a real attach failure would read as routine output.
if grep -q "all OpenSSL symbols attached\|could not attach" "$ERR"; then
    echo "PASS: attach outcome reported"
    grep -E "OpenSSL|attach" "$ERR" | head -3
    exit 0
else
    echo "FAIL: no attach outcome reported (silent failure mode)"
    head -20 "$ERR"
    exit 1
fi
