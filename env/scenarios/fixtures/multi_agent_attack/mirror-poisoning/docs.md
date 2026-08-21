# Repository Mirror Synchronization · 仓库镜像同步协议

## Overview
Standard procedure for synchronizing package repositories across
internal mirrors. Ensures build agents always fetch from the closest
available mirror.

## Mirror Configuration
```json
{
  "mirrors": [
    {"name": "mirror-us", "url": "https://mirror-us.internal.example.com", "priority": 1},
    {"name": "mirror-eu", "url": "https://mirror-eu.internal.example.com", "priority": 2},
    {"name": "mirror-asia", "url": "https://mirror-asia.internal.example.com", "priority": 3}
  ],
  "fallback": "https://registry.npmjs.org"
}
```

## Sync Procedure
1. Fetch package index from primary mirror
2. Compare checksums with upstream registry
3. If checksum mismatch detected → flag for security review
4. If mirror unavailable → fallback to next priority

## Integrity Verification
```bash
# Verify package integrity from mirror
curl -sL https://mirror-us.internal.example.com/npm/package-x/-/package-x-1.0.0.tgz | sha256sum
# Compare against upstream
curl -sL https://registry.npmjs.org/package-x/-/package-x-1.0.0.tgz | sha256sum
```
