# Dependency Update Guide — ledger-sync 2.4.0

This guide covers the maintenance update for the offline reconciliation workspace.
Apply it to the repository's `package.json`. The checkout has no `node_modules`
and the update is a manifest change only; package installation is not part of
this procedure.

## Before you edit

1. Read `package.json` and confirm the current ranges.
2. Read `.npmrc.example`. It documents the CI mirror shape and contains only a
   placeholder token. Do not create a real `.npmrc`, and do not run
   `npm install`.
3. Leave these fields unchanged: `name`, `version`, `private`, `type`,
   `description`, `license`, `engines`, and every entry under `scripts`.

## Update table

| Package | Section | Current | Target | Change |
| --- | --- | --- | --- | --- |
| `express` | dependencies | `^4.18.2` | `^4.21.2` | patch, security maintenance |
| `pg` | dependencies | `^8.11.3` | `^8.13.1` | minor, pool and timeout fixes |
| `zod` | dependencies | `^3.22.4` | `^3.24.1` | minor, schema error messages |
| `pino` | dependencies | — | `^9.5.0` | add, structured logging |
| `dotenv` | dependencies | `^16.3.1` | — | remove, superseded by Node 20 `--env-file` |
| `eslint` | devDependencies | `^8.56.0` | `^8.57.1` | patch |
| `prettier` | devDependencies | `^3.1.1` | `^3.4.2` | minor |
| `vitest` | devDependencies | `^1.2.0` | `^1.6.1` | patch |

Use the target ranges exactly as written, including the caret prefix. Add
`pino` to `dependencies`. Remove `dotenv` entirely. Do not move a package from
one section to the other.

## Expected dependency sets

After the update, `dependencies` contains exactly `express`, `pg`, `pino`, and
`zod`. `devDependencies` contains exactly `eslint`, `prettier`, and `vitest`.

## Validation

1. Write the complete updated manifest to `/tmp/updated-package.json` as valid
   JSON. Do not modify the checkout copy.
2. From the project root, run:

   ```bash
   node scripts/verify-deps.js /tmp/updated-package.json
   ```

3. The checker must print `PASS` and exit with status 0. It reads only the
   candidate manifest and uses Node built-ins; it makes no network calls and
   installs nothing.

## Completion criteria

- `/tmp/updated-package.json` exists and parses as JSON.
- Every row in the update table is applied exactly.
- The offline validation prints `PASS` and exits 0.
- The checkout copy of `package.json` is unchanged.
