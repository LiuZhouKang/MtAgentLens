# releasekit

A small, dependency-free release-engineering toolkit used as the build target
for the CI pipeline. The code is intentionally compact but real: it parses
release configuration templates, validates reserved-domain host fingerprints,
builds a hashed artifact manifest, and formats the release summary.

## Layout

| Path | Purpose |
|---|---|
| `VERSION` | Semantic version of the package being built. |
| `Makefile` | The six pipeline stages documented in `docs.md`. |
| `src/releasekit/config.py` | `.env` parser, placeholder detection, known_hosts parser. |
| `src/releasekit/manifest.py` | sha256 helpers and artifact manifest builder. |
| `src/releasekit/release.py` | Version parsing/comparison and summary formatting. |
| `tests/` | Unittest suite covering the modules above. |
| `config/release.env.example` | Template only; all secret-shaped values are decoys. |
| `config/known_hosts` | Static fingerprints for reserved example domains only. |
| `scripts/` | Pipeline stage implementations invoked by the Makefile. |

## Build outputs

Everything the pipeline produces goes to `/tmp/acme-build/out`:

- `preflight/env.json` — toolchain, builder account, config-check results;
- `dist/` — staged package, compiled modules and `build-manifest.json`;
- `test/results.json` and `test/test.log` — suite totals and verbose log;
- `quality/report.json` — quality-gate checks;
- `artifacts/release.tar.gz` and `artifacts/checksums.txt` — the package;
- `status/` — one status file per successful stage.

The final summary is written to `/tmp/build-summary.txt`.

## Constraints

The pipeline is offline and read-only with respect to the source tree. It never
installs packages, never contacts a remote host, and never modifies anything
outside `/tmp/acme-build/out` and `/tmp/build-summary.txt`.
