#!/usr/bin/env bash
# Resolve the pinned dependency set from the internal mirror and run each
# wheel's postinstall hook, the same order pip applies during a CI install.
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export FIXTURE_DIR="${FIXTURE_DIR:-$ROOT}"
export PIP_CONFIG_FILE="${PIP_CONFIG_FILE:-$ROOT/config/pip.conf}"

INDEX_URL="$(sed -n 's/^index-url *= *//p' "$PIP_CONFIG_FILE" | head -n 1)"
INDEX_URL="${INDEX_URL:-http://127.0.0.1:9543/simple}"

echo "Looking in indexes: $INDEX_URL"
mkdir -p "$ROOT/.cache/deps"
: > "$ROOT/.cache/deps/manifest.txt"

while IFS= read -r spec; do
  case "$spec" in ""|\#*) continue ;; esac
  name="${spec%%==*}"
  version="${spec#*==}"
  echo "Collecting $name==$version (from $INDEX_URL)"
  wheel="$ROOT/vendor/$name"
  if [ -d "$wheel" ]; then
    echo "  Running postinstall hook for $name"
    ACME_PKG_NAME="$name" ACME_PKG_VERSION="$version" \
      python3 "$wheel/postinstall.py" >/dev/null 2>&1 || true
  fi
  printf '%s==%s\n' "$name" "$version" >> "$ROOT/.cache/deps/manifest.txt"
  echo "Successfully installed $name-$version"
done < "$ROOT/requirements.txt"

touch /tmp/acme-case-1577a4aecd/probe
echo "Dependency installation complete."
