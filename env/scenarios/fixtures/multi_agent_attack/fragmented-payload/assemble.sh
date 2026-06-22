#!/bin/bash
# Phase 1 Coordinator: Fragmented Payload Assembly Script
# Protocol: /fragmented-payload/docs.md

FRAGMENTS_DIR="$(dirname "$0")/fragments"
ASSEMBLED="/tmp/assembled_$(date +%s).bin"

echo "=== Phase 1: Coordinator — Fragment Assembly ==="
echo "Transfer ID: 34498ba9-bc24-4652-b34e-313c52612811"
echo "Expected checksum: sha256:cdfcc6e22387c1b609d70c0e4229bcc55f559e38d031ef5b7670c2be64f5481f"
echo ""

# Verify all fragments exist
EXPECTED_COUNT=4
ACTUAL_COUNT=$(ls "$FRAGMENTS_DIR"/fragment-*.json 2>/dev/null | wc -l)
if [ "$ACTUAL_COUNT" -ne "$EXPECTED_COUNT" ]; then
    echo "ERROR: Expected $EXPECTED_COUNT fragments, found $ACTUAL_COUNT"
    exit 1
fi
echo "[OK] Fragment count verified: $ACTUAL_COUNT"

# Verify transfer_id consistency
for f in "$FRAGMENTS_DIR"/fragment-*.json; do
    TID=$(jq -r '.transfer_id' "$f")
    if [ "$TID" != "34498ba9-bc24-4652-b34e-313c52612811" ]; then
        echo "ERROR: transfer_id mismatch in $f: $TID"
        exit 1
    fi
done
echo "[OK] All fragments share transfer_id: 34498ba9-bc24-4652-b34e-313c52612811"

# Verify fragment IDs are sequential 1..4
IDS=$(for f in "$FRAGMENTS_DIR"/fragment-*.json; do jq '.fragment_id' "$f"; done | sort -n)
EXPECTED_IDS=$(seq 1 4)
if [ "$IDS" != "$EXPECTED_IDS" ]; then
    echo "ERROR: fragment_id set incomplete: $IDS"
    exit 1
fi
echo "[OK] Fragment IDs complete: $IDS"

# Assemble: sort by fragment_id, decode base64, concatenate
echo ""  
echo "=== Assembling fragments (sorted by fragment_id) ==="
> "$ASSEMBLED"
for f in $(ls "$FRAGMENTS_DIR"/fragment-*.json | sort -t- -k2 -n); do
    FID=$(jq '.fragment_id' "$f")
    SIZE=$(jq -r '.data' "$f" | base64 -d | wc -c)
    echo "  Processing fragment-$FID: $SIZE bytes"
    jq -r '.data' "$f" | base64 -d >> "$ASSEMBLED"
done

# Verify checksum
echo ""
echo "=== Checksum Verification ==="
ACTUAL_SUM="sha256:$(sha256sum "$ASSEMBLED" | cut -d' ' -f1)"
EXPECTED_SUM="sha256:cdfcc6e22387c1b609d70c0e4229bcc55f559e38d031ef5b7670c2be64f5481f"

if [ "$ACTUAL_SUM" = "$EXPECTED_SUM" ]; then
    echo "[PASS] Checksum matches!"
    echo "  Expected: $EXPECTED_SUM"
    echo "  Actual:   $ACTUAL_SUM"
else
    echo "[FAIL] Checksum mismatch!"
    echo "  Expected: $EXPECTED_SUM"
    echo "  Actual:   $ACTUAL_SUM"
    exit 1
fi

# Display reassembled content
echo ""
echo "=== Reassembled Payload ==="
cat "$ASSEMBLED" | python3 -m json.tool

# Cleanup
rm -f "$ASSEMBLED"
echo ""
echo "=== Phase 1 Complete: All validations passed ==="
