#!/bin/bash
# SPDX-License-Identifier: Apache-2.0 OR MIT
#
# Fresh-clone reproducibility check.
#
# Builds a temporary git repository from exactly the files a commit of this
# working tree would include, clones it into a clean directory, and runs the
# only gate there. This is how the four "works on my machine" prerequisites
# were found: submodules, the frontend embed placeholder, fixture byte
# fidelity, and the embedded eBPF binaries.
#
# Usage: ./scripts/check_fresh_clone.sh
# Exit: 0 when the clone passes every gate, 1 otherwise.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP=$(mktemp -d /tmp/mtl-fresh-XXXXXX)
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/src"

cd "$ROOT"
: > "$TMP/files.txt"
while IFS= read -r f; do
  [ -e "$f" ] && printf '%s\n' "$f" >> "$TMP/files.txt"
done < <(git ls-files -co --exclude-standard)
echo "candidate_files=$(wc -l < "$TMP/files.txt")"

rsync -a --files-from="$TMP/files.txt" "$ROOT/" "$TMP/src/"
cd "$TMP/src"
git init -q
git add -A
git -c user.email=fresh@example.com -c user.name=fresh commit -qm "fresh snapshot"
git clone -q "$TMP/src" "$TMP/clone"

# Submodules: populate from the local checkouts (deterministic, offline). A
# third party cloning the real repository runs `git submodule update`; this
# check validates the build, not the network.
for m in libbpf bpftool; do
  if [ -d "$ROOT/$m/src" ]; then
    mkdir -p "$TMP/clone/$m"
    rsync -a --exclude .git "$ROOT/$m/" "$TMP/clone/$m/"
  else
    git -C "$TMP/clone" submodule update --init --recursive >/dev/null 2>&1 || true
    break
  fi
done

cd "$TMP/clone"
echo "files=$(git ls-files | wc -l)"
if ./scripts/verify.sh > "$TMP/verify.log" 2>&1; then
  echo "FRESH_CLONE_VERIFY=ok"
  exit 0
fi
echo "FRESH_CLONE_VERIFY=FAILED"
tail -40 "$TMP/verify.log"
exit 1
