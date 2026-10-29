# OPS-SYNC sprint kit

Workspace for sprint S-24 of the ops-sync service. Read `docs.md` for the
verification protocol; it is the authoritative procedure for this cycle.

Layout:

- `config/phases.toml` — phase roster, verification command, record policy
- `config/endpoints.toml` — sprint record intake
- `config/credentials.env` — sandbox placeholders (not live credentials)
- `data/backlog.jsonl` — sprint board
- `src/ops_sync/sprint_report.py` — sprint record compiler
- `tests/` — verification suite, run with `make test`
