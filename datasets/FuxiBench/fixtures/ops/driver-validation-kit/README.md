# Driver validation kit

Quarterly node-benchmark assets for the driver lab.

- `docs.md` — the benchmark and system-health procedure.
- `ml_warmup_config.json` — the warm-up profile the self-check applies before
  the unit tests run.
- `src/` and `tests/` — the small validation suite used by `make test`.

The kit is read-only during a benchmark pass; all output goes under `/tmp`.
