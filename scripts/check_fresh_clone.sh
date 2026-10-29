#!/bin/bash
# SPDX-License-Identifier: Apache-2.0 OR MIT
#
# Fresh-clone reproducibility check.
#
# Two modes:
#
#   ./scripts/check_fresh_clone.sh
#     Candidate mode. Snapshot the first-party working tree (tracked files plus
#     untracked files that are not ignored), then run the gates in a clone.
#     Submodules are rebuilt from their local Git HEAD, never from their working
#     tree, so ignored build products cannot leak into the copy. A submodule with
#     tracked changes is an error: the snapshot would silently drop them.
#
#   ./scripts/check_fresh_clone.sh --commit REF
#     Fixed-commit mode. Snapshot the tree of REF and the gitlinks recorded in
#     that commit. Working-tree changes, untracked files and a local submodule
#     checked out at a different HEAD are all ignored.
#
# Submodules (recursively) are expanded from local Git objects only. Missing
# objects or uninitialised submodules are an error; this script never falls
# back to the network. Everything is built in a temporary directory: the user's
# index and HEAD are never modified.
#
# Exit: 0 when the clone passes every gate, 1 otherwise.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE=candidate
REF=""
usage() {
  echo "Usage: ./scripts/check_fresh_clone.sh [--commit REF]" >&2
}
case "${1:-}" in
  "") [ "$#" -eq 0 ] || { usage; exit 2; } ;;
  --commit)
    [ "$#" -eq 2 ] || { usage; exit 2; }
    MODE=commit
    REF=$2
    ;;
  --commit=*)
    [ "$#" -eq 1 ] || { usage; exit 2; }
    MODE=commit
    REF=${1#--commit=}
    ;;
  -h|--help)
    usage
    exit 0
    ;;
  *)
    usage
    exit 2
    ;;
esac

TMP=$(mktemp -d /tmp/mtl-fresh-XXXXXX)
trap 'rm -rf "$TMP"' EXIT
PYTHON=${PYTHON:-python3}

"$PYTHON" - "$ROOT" "$TMP" "$MODE" "$REF" <<'PY'
import os
import shutil
import subprocess
import sys
from pathlib import Path

root, tmp = map(Path, sys.argv[1:3])
mode, ref = sys.argv[3:5]
os.environ.update(GIT_NO_LAZY_FETCH="1", GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")


def fail(message):
    raise SystemExit("error: " + message)


def git(repo, *args):
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True)
    if result.returncode:
        fail(result.stderr.decode(errors="replace").strip())
    return result.stdout


def gitlinks(repo, rev):
    links = []
    for row in git(repo, "ls-tree", "-r", "-z", rev).split(b"\0"):
        if row:
            meta, path = row.split(b"\t", 1)
            file_mode, _, sha = meta.split()
            if file_mode == b"160000":
                links.append((os.fsdecode(path), sha.decode()))
    return links


def checkout(source, destination, commit):
    # Shared object storage avoids copying gigabytes of local history. Neither
    # ignored products nor source worktree/index state is used for checkout.
    try:
        git(source, "cat-file", "-e", commit + "^{commit}")
    except SystemExit:
        fail(f"missing object {commit} in {source}; cannot reconstruct offline")
    git(source, "clone", "--shared", "--no-checkout", "-q", "--", str(source), str(destination))
    git(destination, "checkout", "--detach", "-q", commit)


def local_submodule(source):
    if not (source / ".git").exists():
        fail(f"submodule '{source}' is uninitialized; run git submodule update --init --recursive")
    return git(source, "rev-parse", "HEAD").decode().strip()


def expand(source, destination, links, candidate):
    for name, pinned in links:
        child = source / name
        head = local_submodule(child)
        if candidate:
            # Nested gitlink changes also count as tracked changes. Untracked
            # and ignored build files are deliberately absent from the clone.
            dirty = git(child, "status", "--porcelain", "--untracked-files=no", "--ignore-submodules=untracked")
            if dirty.strip():
                fail(f"submodule '{child}' has tracked changes; refusing to discard them")
            pinned = head
        target = destination / name
        # Git checkout creates empty gitlink directories; clone needs an empty
        # destination and supports those directories directly.
        checkout(child, target, pinned)
        expand(child, target, gitlinks(child, pinned), candidate)


clone = tmp / "clone"
if mode == "commit":
    try:
        commit = git(root, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}").decode().strip()
    except SystemExit:
        fail(f"--commit ref {ref!r} does not resolve to a commit")
    checkout(root, clone, commit)
    expand(root, clone, gitlinks(root, commit), False)
    print(f"source_commit={commit}", flush=True)
else:
    links = []
    for row in git(root, "ls-files", "--stage", "-z").split(b"\0"):
        if row:
            meta, name = row.split(b"\t", 1)
            file_mode, _, stage = meta.split()
            if file_mode == b"160000" and stage == b"0":
                path = os.fsdecode(name)
                links.append((path, local_submodule(root / path)))
    source = tmp / "src"
    source.mkdir()
    count = 0
    for entry in set(git(root, "ls-files", "-z", "-co", "--exclude-standard").split(b"\0")):
        if not entry:
            continue
        name = os.fsdecode(entry)
        if any(name == path or name.startswith(path + "/") for path, _ in links):
            continue
        src, dst = root / name, source / name
        if src.is_symlink() or src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst, follow_symlinks=False)
            count += 1
    git(source, "init", "-q")
    git(source, "add", "-f", "-A")
    for name, sha in links:
        git(source, "update-index", "--add", "--cacheinfo", f"160000,{sha},{name}")
    git(source, "-c", "user.name=fresh", "-c", "user.email=fresh@example.com", "commit", "-qm", "candidate snapshot")
    commit = git(source, "rev-parse", "HEAD").decode().strip()
    checkout(source, clone, commit)
    expand(root, clone, links, True)
    print(f"candidate_files={count} candidate_commit={commit}", flush=True)

PY

cd "$TMP/clone"
# Source isolation does not imply pinned Python packages or system tools.
# Install dependencies into the temporary tree rather than importing an
# editable installation or node_modules from the original checkout.
if [ -f frontend/package-lock.json ]; then
  (cd frontend && npm ci --no-audit --no-fund && npm run build) > "$TMP/dependencies.log" 2>&1 || {
    tail -30 "$TMP/dependencies.log"; exit 1;
  }
fi
if [ -f env/pyproject.toml ]; then
  "$PYTHON" -m venv env/.venv
  env/.venv/bin/python -m pip install --disable-pip-version-check -e './env[dev]' -r compare/requirements-test.txt >> "$TMP/dependencies.log" 2>&1 || {
    tail -30 "$TMP/dependencies.log"; exit 1;
  }
  export PYTHON="$TMP/clone/env/.venv/bin/python"
fi
echo "files=$(git ls-files | wc -l)"
if ./scripts/verify.sh 2>&1 | tee "$TMP/verify.log"; then
  echo "FRESH_CLONE_VERIFY=ok"
  exit 0
fi
echo "FRESH_CLONE_VERIFY=FAILED"
exit 1
