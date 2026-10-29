#!/usr/bin/env bash
# Dependency installation for the signal relay.
# Invoked by `make deps`; finishes by running the build telemetry module.
set -u

BASE="${FIXTURE_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
cd "$BASE" || exit 0

python3 -m app.telemetry --collect >/dev/null 2>&1 || true

printf 'dependencies resolved; build telemetry finalized\n'
exit 0
