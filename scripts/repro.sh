#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0 OR MIT
# =============================================================================
# One-command artifact build: submodules → eBPF → frontend → Rust → gates.
# =============================================================================
# This is the reproducible half of the artifact. The frozen corpus lives
# outside git (see docs/evaluation/); `--with-pilot` additionally runs the
# Observer-on pilot against the local corpus, which needs sudo + an LLM.
#
# Usage: scripts/repro.sh [--with-pilot]
# Output: dist/repro-manifest.json
# =============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
WITH_PILOT=0
[ "${1:-}" = "--with-pilot" ] && WITH_PILOT=1
OUT="$ROOT/dist"
mkdir -p "$OUT"

echo "[1/6] submodules"
git submodule update --init --recursive

echo "[2/6] eBPF programs"
make -C bpftools

echo "[3/6] frontend"
( cd frontend && npm ci && npm run build )

echo "[4/6] Rust release"
( cd collector && cargo build --release )

echo "[5/6] gates"
./scripts/verify.sh || { echo "verify failed" >&2; exit 1; }

echo "[6/6] manifest"
python3 - "$ROOT" "$OUT/repro-manifest.json" <<'PY'
import hashlib, json, os, platform, subprocess, sys
from pathlib import Path

root, out = Path(sys.argv[1]), Path(sys.argv[2])
binary = root / "collector" / "build" / "release" / "mtagentlens"

def sha(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None

def version(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True).stdout.splitlines()[0]
    except (OSError, IndexError):
        return "unavailable"

payload = {
    "schema": "mtagentlens.repro-manifest/v1",
    "git_head": version(["git", "rev-parse", "HEAD"]),
    "git_dirty": bool(subprocess.run(
        ["git", "status", "--porcelain"], cwd=root, check=True,
        capture_output=True, text=True,
    ).stdout.strip()),
    "collector_binary": str(binary.relative_to(root)),
    "collector_sha256": sha(binary),
    "frontend_out": "frontend/out",
    "bpf_outputs": sorted(str(p.relative_to(root)) for p in (root / "bpftools" / ".output" / "bin").glob("*")),
    "toolchain": {
        "rustc": version(["rustc", "--version"]),
        "cargo": version(["cargo", "--version"]),
        "node": version(["node", "--version"]),
        "npm": version(["npm", "--version"]),
        "clang": version(["clang", "--version"]),
    },
    "environment": {
        "kernel": platform.release(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
    },
    "corpus": "not bundled; see docs/evaluation/ for checksums and collection scripts",
}
out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
print(json.dumps(payload, indent=2, sort_keys=True))
PY

if [ "$WITH_PILOT" = "1" ]; then
  echo "[pilot] Observer-on direction pilot"
  export PATH="$HOME/.local/bin:$PATH"
  export MTAGENTLENS_SUDO_PASSWORD="${MTAGENTLENS_SUDO_PASSWORD:-}"
  PYTHONPATH=env:env/src env/.venv/bin/python compare/scripts/run/pilot_observer.py \
    --only c2-beacon-direct --only c2-reverse-shell --only benign-heavy-build \
    --observer on --out "$OUT/pilot"
fi

echo "repro: ok -> $OUT/repro-manifest.json"
