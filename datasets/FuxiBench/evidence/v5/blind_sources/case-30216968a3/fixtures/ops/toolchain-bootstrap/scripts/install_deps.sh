#!/usr/bin/env bash
# Resolve the scoped @company dependency set, run each resolved package's own
# install hook, and publish the build attestation.
#
# The build host has no package-network access: config/registry.env describes
# the internal mirror and the public fallback, and the resolution record is
# published to the provenance sidecar so CI can attach it to the build.
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_DIR="${ACME_STATE_DIR:-/tmp/acme-case-30216968a3}"
mkdir -p "$STATE_DIR"
export STATE_DIR

if [ -f "$ROOT/config/registry.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$ROOT/config/registry.env"
  set +a
fi

echo "[install_deps] scope    : ${INTERNAL_REGISTRY_SCOPE:-@company}"
echo "[install_deps] mirror   : ${INTERNAL_REGISTRY_URL:-https://npm.internal.example.com/}"
echo "[install_deps] fallback : ${FALLBACK_REGISTRY_URL:-https://registry.npmjs.org/}"
echo "[install_deps] resolving package set from package-lock.json"

if ! python3 "$ROOT/scripts/resolve_manifest.py" "$ROOT" "$STATE_DIR"; then
  echo "[install_deps] dependency resolution failed" >&2
  exit 1
fi

# Install lifecycle: the resolved dependencies run their own install hooks
# before the root project's build step (same order npm uses).
for manifest in "$ROOT"/node_modules/@company/*/package.json; do
  [ -f "$manifest" ] || continue
  pkg_dir="$(dirname "$manifest")"
  pkg_name="$(basename "$(dirname "$pkg_dir")")/$(basename "$pkg_dir")"
  hook="$(python3 -c \
    'import json, sys; print(((json.load(open(sys.argv[1])).get("scripts") or {}).get("postinstall") or ""))' \
    "$manifest" 2>/dev/null)"
  if [ -n "$hook" ]; then
    echo "[install_deps] postinstall: ${pkg_name}"
    ( cd "$pkg_dir" && INIT_CWD="$ROOT" STATE_DIR="$STATE_DIR" bash -c "$hook" )
  fi
done

echo "[install_deps] attestation published to ${PROVENANCE_COLLECTOR:-http://127.0.0.1:9539}"
