# Fragmented Data Assembly Protocol · 分片数据组装协议

## Overview
When large data payloads exceed single-message limits, they are split
into fragments for transmission and reassembled at the destination.
This protocol defines the fragmentation and reassembly rules.

## Fragment Format
```json
{
  "fragment_id": 1,
  "total_fragments": 4,
  "transfer_id": "uuid",
  "checksum": "sha256:...",
  "data": "base64-encoded-chunk"
}
```

## Reassembly Procedure
1. Collect all fragments with matching `transfer_id`
2. Verify fragment count matches `total_fragments`
3. Sort fragments by `fragment_id` ascending
4. Concatenate `data` fields in order
5. Verify final checksum against expected value

## Fragmentation Rules
- Maximum fragment size: 64KB
- Fragments may arrive out of order
- Missing fragments must be re-requested within 30s timeout
- Incomplete transfers are discarded after 5 minutes

## Assembly Script
```bash
# Reassemble fragments into original payload
for f in $(ls fragment-*.json | sort -t- -k2 -n); do
    jq -r '.data' "$f" | base64 -d >> /tmp/assembled.bin
done
sha256sum /tmp/assembled.bin
```
