#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
out="${1:-../compare-handoff-2026-10-30.tar.gz}"
tar -czf "$out" \
  --exclude='.venv-*' \
  --exclude='models' \
  --exclude='Dataset' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='.pytest_cache' \
  .
echo "wrote $out"
