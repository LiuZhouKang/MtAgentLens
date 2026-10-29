#!/usr/bin/env bash
# Project verifier: warm the cache, compile, and run the unit tests.
set -u
cd "$(dirname "$0")"
make build || exit 1
make test || exit 1
echo "verification complete"
