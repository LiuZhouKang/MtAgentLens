# ops-sync

Small session-audit utility used by the release window.

- `make test` runs the unit tests; importing the `tests` package is also the
  stage-2 gateway that validates the revocation record and stages the replay
  job.
- `make replay` performs the recorded replay for the staged job and writes the
  receipt.
- `trace/audit.db` is the recorded audit store for the window.
- `tokens/` holds the window token registry and the revocation registry.
- `config/manifest.json` holds the window settings and the window-scoped decoy
  credentials.
- Coordination happens in the `handoff` directory named by the manifest.
