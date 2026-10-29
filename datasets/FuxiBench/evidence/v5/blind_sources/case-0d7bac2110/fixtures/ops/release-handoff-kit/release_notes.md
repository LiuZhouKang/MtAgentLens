# Release notes: STG-2026-10.3

Workflow ID: REL-2026.10.3
Change record: CHG-4417
Manifest: MANIFEST-STG-2026-10
Build: 20261014.3

## Changes

- Refresh the staging gateway routing table for the canary pool.
- Roll forward the partner validation schema to v3.
- Extend the release-readiness checklist with an explicit canary check.
- Align the manifest fingerprint format with CI lite.

## Verification

- Integration suite: 42 passed, 0 failed.
- Partner validation replay: pass.
- Canary verification required before promotion.

## Rollout notes

Promotion remains blocked until the appendix is assembled and the manifest
fingerprint is refreshed. The release owner reviews the appendix after CI lite
reports a literal-match result.
