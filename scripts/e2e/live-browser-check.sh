#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0 OR MIT
# =============================================================================
# Browser check against the live server: screenshot the real pages while a
# `trace --server` process is serving its own live run.
# =============================================================================
# Requires sudo (eBPF) and Playwright. Usage: scripts/e2e/live-browser-check.sh
# =============================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BIN="$ROOT/collector/build/debug/mtagentlens"
[ -x "$BIN" ] || BIN="$ROOT/collector/build/release/mtagentlens"
SUDO_PASS="${MTAGENTLENS_SUDO_PASSWORD:-}"
PORT="${BROWSER_CHECK_PORT:-47893}"
WORK="$(mktemp -d /tmp/mtl-browser.XXXXXX)"
LOG="$WORK/live.log"
OUT="${MTL_SHOT_DIR:-$WORK/shots}"

die() { echo "ERROR: $*" >&2; exit 1; }
[ -x "$BIN" ] || die "collector binary not found"
command -v npx >/dev/null || die "npx is required"
mkdir -p "$OUT"
# The screenshot command runs from frontend/; keep relative MTL_SHOT_DIR stable.
OUT="$(cd "$OUT" && pwd)"

cleanup() {
  if [ -n "${COLLECTOR_PID:-}" ]; then
    printf '%s\n' "$SUDO_PASS" | sudo -S -p '' kill -INT "$COLLECTOR_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

printf '%s\n' "$SUDO_PASS" | sudo -S -p '' "$BIN" trace \
  --ssl false --process true -c claude -o "$LOG" -q \
  --server --server-port "$PORT" >"$WORK/collector.out" 2>&1 &
SUDO_PID=$!

for _ in $(seq 1 100); do
  curl -fsS --max-time 1 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && break
  sleep 0.2
done
curl -fsS --max-time 2 "http://127.0.0.1:$PORT/health" >/dev/null \
  || die "server did not start; see $WORK/collector.out"

for candidate in $(pgrep -P "$SUDO_PID" 2>/dev/null) "$SUDO_PID"; do
  argv0="$(tr '\0' '\n' < "/proc/$candidate/cmdline" 2>/dev/null | head -1)"
  if [ "$argv0" = "$BIN" ]; then COLLECTOR_PID="$candidate"; break; fi
done

# A tiny non-LLM workload makes the live screenshots meaningful when systemd is
# available; without it the pages only show their empty states.
if command -v systemd-run >/dev/null; then
  cp "$(command -v python3)" "$WORK/claude"
  chmod +x "$WORK/claude"
  printf '%s\n' "$SUDO_PASS" | sudo -S -p '' systemd-run --system     --unit="mtl-browser-$$" --collect --wait "$WORK/claude"     -c 'import time; time.sleep(1)' >"$WORK/workload.out" 2>&1 \
    || echo "warning: live workload failed; screenshots may show empty state" >&2
  for _ in $(seq 1 30); do
    if curl -fsS --max-time 2 "http://127.0.0.1:$PORT/api/runs/live/evidence" \
        | python3 -c 'import json,sys; sys.exit(not bool(json.load(sys.stdin).get("actions")))' 2>/dev/null; then
      break
    fi
    sleep 0.2
  done
fi

cd "$ROOT/frontend"
for page in "" console overview investigations traces; do
  name="${page:-home}"
  npx playwright screenshot \
    --viewport-size="1440,900" \
    --wait-for-timeout=3500 \
    --full-page \
    "http://127.0.0.1:$PORT/$page" "$OUT/$name.png" \
    || die "screenshot failed: $page"
  echo "shot: $OUT/$name.png"
done

echo "BROWSER_CHECK=ok dir=$OUT"
