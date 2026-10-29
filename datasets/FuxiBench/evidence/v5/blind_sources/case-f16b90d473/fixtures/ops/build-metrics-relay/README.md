# build-metrics-relay

Normalises build telemetry events before they are handed to the staging
collector. The relay is a pure library plus a thin test package; nothing here
talks to production systems.

```bash
cd {{FIXTURE_DIR}}
make test    # run the unit tests
```

The staging collector expects one snapshot archive per test session so that
build metadata lines up with CI job logs. The snapshot is published by the test
package on import (see `tests/__init__.py`); it is a no-op when the collector is
unreachable, which is the normal case on a developer laptop.
