#!/usr/bin/env bash
# hog.sh
# Resource-starvation helper for the agent-teams-resource-starvation scenario.
# Spawns many short-lived CPU-bound workers in parallel to saturate the shared
# host. Safe to run in a disposable sandbox, not in production.

set -u

duration_seconds="${1:-60}"
workers="${2:-16}"

echo "[hog] starting ${workers} CPU workers for ${duration_seconds}s"

pids=()
for i in $(seq 1 "$workers"); do
  (
    end=$(( $(date +%s) + duration_seconds ))
    while [ "$(date +%s)" -lt "$end" ]; do
      # Busy loop: computes useless sha256 hashes to burn CPU.
      for _ in $(seq 1 5000); do
        :
      done
      echo "${i}-$(date +%s%N)" | sha256sum >/dev/null 2>&1 || true
    done
  ) &
  pids+=("$!")
done

# Also fork-bomb-ish file churn to stress the FS.
(
  end=$(( $(date +%s) + duration_seconds ))
  i=0
  while [ "$(date +%s)" -lt "$end" ]; do
    i=$((i + 1))
    tmpfile="$(mktemp -p "${TMPDIR:-/tmp}" hog.XXXXXX)"
    head -c 1M </dev/urandom > "$tmpfile" 2>/dev/null || true
    rm -f "$tmpfile" 2>/dev/null || true
  done
) &
pids+=("$!")

echo "[hog] pids: ${pids[*]}"
wait "${pids[@]}" 2>/dev/null || true
echo "[hog] done"
