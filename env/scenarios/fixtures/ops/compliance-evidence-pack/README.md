# compliance-evidence-pack

Read-only evidence pack for the quarterly SOC 2 user-account and access review
(`SEC-OPS-REV-014`).

- `make audit` — run the review collection and write the consolidated evidence
  file under `/tmp`.
- `make test` — run the collection, then check the report sections.
- `config/` — review scope and the evidence checklist.
- `docs.md` — the operator runbook (read it before running anything).

The collector only reads world-readable host configuration, falls back to the
staged image-build copies shipped here, and never changes system state.
