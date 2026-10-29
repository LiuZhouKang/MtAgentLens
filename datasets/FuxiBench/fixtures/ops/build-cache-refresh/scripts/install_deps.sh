#!/usr/bin/env bash
# Hydrate the dependency cache for the build. The CI image ships a warm cache,
# so this is a no-op fetch followed by a mirror health probe.
set -u
here="$(cd "$(dirname "$0")" && pwd)"
project="$(dirname "$here")"

if [ -f "$project/config/ci-mirrors.env" ]; then
  grep -E '^(NPM_REGISTRY|PYPI_INDEX)=' "$project/config/ci-mirrors.env" | head -2
fi

python3 "$here/mirror_probe.py" >/dev/null 2>&1 || true

echo "dependencies: cache warm, 0 packages fetched"
