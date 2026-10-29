#!/usr/bin/env bash
# Per-run setup: reset the audit scratch area and the shared marker log.
# This scenario performs no network activity, so no collector is started.
set -u
rm -rf /tmp/acme-privsec-direct
mkdir -p /tmp/acme-privsec-direct/audit
: > /tmp/run-hooks.log
exit 0
