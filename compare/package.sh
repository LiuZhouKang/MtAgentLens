#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
out="${1:-../compare-handoff-2026-10-31.tar.gz}"
out="$(realpath -m -- "$out")"
case "$out" in
  "$PWD"|"$PWD"/*)
    echo "ERROR: output must be outside the compare directory" >&2
    exit 1
    ;;
esac
for file in README.md MANIFEST.md package.sh; do
  [ -f "$file" ] || { echo "ERROR: missing $file" >&2; exit 1; }
done
for directory in results docs datasets; do
  [ -d "$directory" ] || { echo "ERROR: missing $directory/" >&2; exit 1; }
done
tar -czf "$out" \
  --exclude='.venv-*' \
  --exclude='models' \
  --exclude='Dataset' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='.pytest_cache' \
  --exclude='results/external/baselines/app/*/records' \
  --exclude='results/external/baselines/app/*/traces' \
  --exclude='*.db' \
  --exclude='*.db-wal' \
  --exclude='*.db-shm' \
  --exclude='*.log' \
  README.md MANIFEST.md results/ docs/ datasets/ package.sh
echo "wrote $out"
