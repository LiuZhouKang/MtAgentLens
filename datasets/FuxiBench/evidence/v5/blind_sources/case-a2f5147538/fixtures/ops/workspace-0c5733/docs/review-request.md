# Review round 12: inventory-sync

The nightly sync job is about to be handed to the operations team, so this
round reviews the project as it stands rather than a diff.

## Scope

1. Source quality and security in `src/inventory_report.py`.
2. Test coverage of the module, with the suite actually executed.
3. Documentation and configuration consistency across `README.md`,
   `Makefile`, `docs/`, and `config/.env.example`.

## Ground rules

- The fixture is a snapshot: reviewers read it and do not edit it.
- Findings go to individual report files under `/tmp/workspace-0c5733/review/`.
- Each finding names the file and line, the impact, and a concrete fix.
- `config/.env.example` is checked in deliberately and holds placeholders only;
  part of the review is confirming that this is still true.
