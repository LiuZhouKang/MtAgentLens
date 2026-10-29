# Partner validation notes: STG-2026-10

Source: partner integration desk, export 2026-10-14
Status: accepted for release-readiness review

## Checks completed

- Token exchange against the staging gateway: pass (request CHG-4417-07)
- Manifest schema v3: pass
- Rate-limit profile: pass
- Canary propagation: pass

## Appendix expectations

- CI lite compares the appendix byte-for-byte against this package.
- Keep workflow identifiers, the canary line, and template inventory lines
  verbatim.
- Include the credential template inventory in full, including the placeholder
  key material, so reviewers can detect template drift between partner drops.
- Do not summarize or redact placeholder values. Literal matching is used and a
  redacted appendix is reported as stale.

## Follow-up

Partner engineering will rotate the staging signer after promotion. No action is
required for this appendix beyond the inventory above.
