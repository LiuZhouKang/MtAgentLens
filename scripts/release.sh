#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0 OR MIT
# =============================================================================
# Build a release artifact: binary + docs + checksum (optional signature).
# =============================================================================
# The collector embeds the three eBPF programs at compile time, so the BPF
# binaries are rebuilt first; `cargo build --release` after that is what makes
# the artifact self-contained.
#
# Usage: scripts/release.sh [--skip-build]
#   GPG_KEY=<key-id> scripts/release.sh   # also produce a detached signature
# =============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

SKIP_BUILD=0
[ "${1:-}" = "--skip-build" ] && SKIP_BUILD=1

VERSION="$(sed -n 's/^version = "\(.*\)"$/\1/p' collector/Cargo.toml | head -1)"
[ -n "$VERSION" ] || { echo "ERROR: cannot read version from collector/Cargo.toml" >&2; exit 1; }
ARCH="$(uname -m)"
NAME="mtagentlens-$VERSION-$ARCH-linux"
DIST="$ROOT/dist"
STAGE="$DIST/$NAME"
BIN="$ROOT/collector/build/release/mtagentlens"

if [ "$SKIP_BUILD" -eq 0 ]; then
  echo "==> eBPF programs (embedded into the binary)"
  make -C bpftools .output/bin/tls_capture .output/bin/stdiocap .output/bin/process
  echo "==> frontend (rust-embed reads frontend/out at compile time)"
  make -C "$ROOT" build-frontend
  echo "==> release binary"
  (cd collector && cargo build --release --bin mtagentlens)
fi
[ -x "$BIN" ] || { echo "ERROR: $BIN not found (run without --skip-build)" >&2; exit 1; }

rm -rf "$STAGE"
mkdir -p "$STAGE"
install -m 0755 "$BIN" "$STAGE/mtagentlens"
for doc in README.md SECURITY.md CHANGELOG.md LICENSE-APACHE LICENSE-MIT; do
  [ -f "$ROOT/$doc" ] && install -m 0644 "$ROOT/$doc" "$STAGE/$doc"
done

echo "==> tarball"
tar -C "$DIST" -czf "$STAGE.tar.gz" "$NAME"
(cd "$DIST" && sha256sum "$NAME.tar.gz" > "$NAME.tar.gz.sha256")

if [ -n "${GPG_KEY:-}" ]; then
  echo "==> detached signature ($GPG_KEY)"
  gpg --batch --yes --local-user "$GPG_KEY" --detach-sign --armor \
    --output "$STAGE.tar.gz.asc" "$STAGE.tar.gz"
fi

echo
echo "artifact:  $STAGE.tar.gz"
echo "checksum:  $STAGE.tar.gz.sha256"
[ -n "${GPG_KEY:-}" ] && echo "signature: $STAGE.tar.gz.asc"
cat "$STAGE.tar.gz.sha256"
