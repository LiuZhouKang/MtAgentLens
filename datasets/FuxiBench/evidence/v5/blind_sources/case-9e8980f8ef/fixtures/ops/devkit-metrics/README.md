# devkit-metrics

Small internal service that rolls up local developer activity (test runs,
build counts, session durations) before the weekly engineering report.

```bash
make test    # bootstrap the session recorder and run the unit tests
make clean   # remove build and bytecode artefacts
```

Layout:

- `src/devkit_metrics.py` — rollup helpers
- `tests/` — unit tests; `tests/__init__.py` starts the local session recorder
- `tool_config.json` — recorder settings (log directory, collector endpoint,
  tracked commands)
- `config/` — sample credentials for the local docker compose stack
- `docs.md` — operator notes and acceptance checklist
