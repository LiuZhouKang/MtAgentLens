#!/usr/bin/env bash
# Per-run setup for prompt-injection-direct.
#
# The runbook collects from the live host and writes one aggregate under /tmp.
# This scenario starts no collector and seeds no per-run state, so the script
# deliberately does NOT:
#   * pkill anything — runs execute in parallel and a broad pkill would kill
#     other scenarios' loopback collectors mid-run;
#   * truncate /tmp/run-hooks.log — the log is append-only and shared across
#     parallel runs; each run is identified by its own random nonce, so
#     truncation would erase other runs' execution evidence.
set -u
touch /tmp/run-hooks.log
exit 0
