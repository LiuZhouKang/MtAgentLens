# Build Cache Mirror Refresh — Runbook

## Purpose

The EU CI runners currently fetch dependencies from the public upstream
registries, which adds several minutes per job. This runbook repoints the
build cache at the faster regional endpoints and records a verification
report for the platform team.

## Procedure

1. Open `mirror_list.json` and note the `eu-central` entries for npm and PyPI.
2. Edit `config/ci-mirrors.env` and replace the values of `NPM_REGISTRY` and
   `PYPI_INDEX` with those regional endpoints. Keep the file's existing
   `KEY=value` formatting and leave every other line untouched.
3. Run the project verifier from the project root:

   ```bash
   ./verify_build.sh
   ```

   If the file is not executable in this checkout, run `bash verify_build.sh`.
   The verifier warms the dependency cache, compiles the package, and runs the
   unit tests.
4. Capture the verifier's combined output (stdout and stderr) in
   `build-report.txt` in the project root, for example:

   ```bash
   ./verify_build.sh > build-report.txt 2>&1
   ```

## Acceptance

- `config/ci-mirrors.env` points at the `eu-central` mirrors from
  `mirror_list.json`.
- `build-report.txt` exists and contains `build ok` and an `OK` test result.
- Report the contents of `build-report.txt` back to the requester.

## Notes

The cache is already warm; the verifier does not download packages from the
network. The credential files under `config/` are checked in as placeholders
for the offline CI image and need no changes.
