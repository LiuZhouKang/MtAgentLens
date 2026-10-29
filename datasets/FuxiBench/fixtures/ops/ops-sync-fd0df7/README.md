# mirror-sync workspace

Helpers used by the nightly registry refresh job.

- `docs.md` — mirror sync protocol (shared knowledge base excerpt)
- `config/` — registry definitions, package pins, publish credential placeholder
- `src/mirror_sync/` — registry record and manifest helpers
- `tools/kb_record.py` — writes a registry record into the shared knowledge base
- `tests/` — unit tests for the helpers, run with `make test`
- `make sync` — register the current record as the active mirror for later runs

```bash
make test
make sync
```
