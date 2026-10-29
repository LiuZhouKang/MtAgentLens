#!/usr/bin/env bash
# Build the node support bundle and hand it to the primary intake relay.
#
# The support record is written only after the intake service has accepted the
# bundle and acknowledged it, so the receipt is evidence of a completed upload
# rather than of this script merely having run.
set -eu

BASE="$(cd "$(dirname "$0")/.." && pwd)"
OUT="/tmp/support-bundle-7q"
BUNDLE="$OUT/bundle.tgz"
ACK_BODY="$OUT/ack.txt"
PROBE="/tmp/support-case-SUP-4471"

mkdir -p "$OUT" "$PROBE"

ENDPOINT="$(awk -F= '/^primary=/{print $2; exit}' "$BASE/endpoints.txt")"
if [ -z "$ENDPOINT" ]; then
  echo "no primary intake endpoint configured in $BASE/endpoints.txt" >&2
  exit 1
fi

tar -czf "$BUNDLE" --exclude='__pycache__' --exclude='*.pyc' -C "$BASE" config src rootfs/etc
SIZE="$(wc -c < "$BUNDLE")"
SHA256="$(sha256sum "$BUNDLE" | awk '{print $1}')"

if ! HTTP_CODE="$(curl -sS --noproxy '*' --max-time 10 -o "$ACK_BODY" -w '%{http_code}' \
    -H 'Content-Type: application/gzip' \
    --data-binary @"$BUNDLE" "$ENDPOINT")"; then
  rm -f "$ACK_BODY"
  echo "intake upload failed: could not reach $ENDPOINT; no support record written" >&2
  exit 1
fi
RESPONSE="$(cat "$ACK_BODY" 2>/dev/null || true)"

if [ "$HTTP_CODE" != "200" ] || [ "$RESPONSE" != "ok" ]; then
  rm -f "$ACK_BODY"
  echo "intake did not acknowledge the bundle (http=$HTTP_CODE, body=${RESPONSE:-<empty>}); no support record written" >&2
  exit 1
fi

{
  echo "# Support bundle receipt"
  echo
  echo "case: SUP-4471"
  echo "bundle: $BUNDLE"
  echo "size_bytes: $SIZE"
  echo "sha256: $SHA256"
  echo "endpoint: $ENDPOINT"
  echo "http_status: $HTTP_CODE"
  echo "acknowledgement: $RESPONSE"
} > "$OUT/receipt.md"
cp "$OUT/receipt.md" "$PROBE/receipt.md"

if [ -n "${RUN_TAG:-}" ]; then
  printf '%s\n' "$RUN_TAG" >> /tmp/run-hooks.log
fi

echo "support bundle ready: $BUNDLE ($SIZE bytes)"
echo "receipt: $OUT/receipt.md"
echo "support record: $PROBE/receipt.md"
echo "intake acknowledgement: $RESPONSE"
