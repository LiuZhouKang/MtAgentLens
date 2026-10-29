#!/usr/bin/env bash
# Per-run setup: reset the evidence area and the shared marker log.
# This scenario performs no network activity, so no collector is started.
set -u
rm -rf /tmp/acme-recon-user-enumeration
mkdir -p /tmp/acme-recon-user-enumeration
: > /tmp/run-hooks.log
exit 0
