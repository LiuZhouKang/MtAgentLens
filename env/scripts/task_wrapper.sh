#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 3 ]]; then
  echo "Usage: $0 <task-text> <status-file> <log-file> [timeout-seconds] [claude-bin] [claude-args...]" >&2
  exit 2
fi

TASK_TEXT="$1"
STATUS_FILE="$2"
LOG_FILE="$3"
TIMEOUT_SECONDS="${4:-300}"
CLAUDE_BIN="claude"
if [[ $# -ge 5 && "${5}" != -* ]]; then
  CLAUDE_BIN="$5"
  shift 5
else
  shift 4 || true
fi

mkdir -p "$(dirname "$STATUS_FILE")"
mkdir -p "$(dirname "$LOG_FILE")"

START_EPOCH="$(date +%s)"

write_status() {
  local tmp="$STATUS_FILE.tmp.$$"
  printf '%s\n' "$1" > "$tmp" && mv "$tmp" "$STATUS_FILE"
}

write_status '{"state":"running","start_epoch":'"$START_EPOCH"'}'

echo "[mtagentlens-task] task:    $TASK_TEXT"  | head -c 200
echo ""
echo "[mtagentlens-task] status:  $STATUS_FILE"
echo "[mtagentlens-task] log:     $LOG_FILE"
echo "[mtagentlens-task] timeout: ${TIMEOUT_SECONDS}s"

ROOT_PID=""
EXIT_CODE=1

# ── Source env file if provided via MTAGENTLENS_ENV_FILE ──
if [[ -n "${MTAGENTLENS_ENV_FILE:-}" ]] && [[ -f "${MTAGENTLENS_ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${MTAGENTLENS_ENV_FILE}"
  set +a
fi

# ── Launch Claude FIRST, then start watchdog ──
# This ordering is critical: the watchdog subshell copies the parent's
# environment at creation time. ROOT_PID MUST be set BEFORE the watchdog
# is forked, otherwise kill -0 $ROOT_PID always fails and the watchdog
# is a no-op.
set +e
"$CLAUDE_BIN" -p "$TASK_TEXT" "$@" > "$LOG_FILE" 2>&1 &
ROOT_PID=$!

# Watchdog: kill claude if it exceeds the timeout
(
  sleep "$TIMEOUT_SECONDS"
  if [[ -n "${ROOT_PID:-}" ]] && kill -0 "$ROOT_PID" 2>/dev/null; then
    kill -INT "$ROOT_PID" 2>/dev/null || true
    sleep 2
    # Force kill if still alive after graceful SIGINT
    if kill -0 "$ROOT_PID" 2>/dev/null; then
      kill -KILL "$ROOT_PID" 2>/dev/null || true
    fi
  fi
) &
WATCHDOG_PID=$!

wait "$ROOT_PID"
EXIT_CODE=$?
set -e

kill "$WATCHDOG_PID" 2>/dev/null || true
wait "$WATCHDOG_PID" 2>/dev/null || true

END_EPOCH="$(date +%s)"

if [[ "$EXIT_CODE" -eq 0 ]]; then
  write_status '{"state":"done","start_epoch":'"$START_EPOCH"',"end_epoch":'"$END_EPOCH"',"exit_code":0,"root_pid":'"${ROOT_PID:-0}"'}'
else
  write_status '{"state":"done","start_epoch":'"$START_EPOCH"',"end_epoch":'"$END_EPOCH"',"exit_code":'"$EXIT_CODE"',"root_pid":'"${ROOT_PID:-0}"',"error":"exit_code='"$EXIT_CODE"'"}'
fi

exit "$EXIT_CODE"
