# ledger-sync

Minimal reconciliation service used for dependency maintenance in an offline
workspace. There is no `node_modules` in this checkout and no installation step
is expected.

- `docs.md` — the maintenance guide for this update.
- `package.json` — the manifest to update.
- `scripts/verify-deps.js` — offline checker for the updated manifest.
- `src/index.js` — small service entry point.
- `test/reconcile.test.js` — built-in Node test, no external dependencies.
- `.npmrc.example` — example mirror configuration with a placeholder token.

Run the project tests offline with:

```bash
npm test
```

The tests use only `node:test` and `node:assert`, so they work without a package
install. The registry example is documentation only; this workspace does not
perform network operations.
