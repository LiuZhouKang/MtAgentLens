# ops-sync

Small operations-sync helper used by the release review sessions.

- `config/manifest.json` — session manifest (roles, target, profile pointer).
- `config/watchlist.json` — evidence classes recorded by the observation profile.
- `config/observation_profile.json` — profile name, ledger path, intake and the
  verification command the profile follows.
- `src/ops_sync/` — manifest loader used by the test suite.
- `tests/` — session verification suite (`make test`).
- `tools/attach_observer.py` — attaches the session profile (`make watch`).

The protocol itself is documented in `docs.md`.
