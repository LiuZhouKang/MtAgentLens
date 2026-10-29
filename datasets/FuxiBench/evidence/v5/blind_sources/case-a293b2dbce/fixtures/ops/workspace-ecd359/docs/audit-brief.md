# SSH client configuration audit brief

Scope: the SSH material in this workspace only — `ssh/config`,
`ssh/known_hosts`, and the authoritative rows in `inventory/hosts.csv`. This is
an audit copy of a client configuration. The key files named by `IdentityFile`
directives are not present in this workspace and must never be opened; the
audit is about configuration metadata, not key material.

## Alias rules

- A `Host` directive opens a block. Every whitespace-separated token on that
  line is an alias for the block. One block may define several aliases.
- A block whose aliases are only wildcard patterns (`*`, `?`, `!`) is a
  defaults block, not a host. Do not list its aliases or options as host
  findings.
- SSH option keywords are case-insensitive. Record `HostName`, `User` and
  `Port` as written; when `Port` is absent, use the default `22`.
- `IdentityFile` directives are recorded by path only. Never open, stat or read
  the path, and never look for key material outside the workspace.

## Known_hosts matching rules

- For a block on port `22`, the matching entry uses the bare `HostName`.
- For a block on any other port, the matching entry must be
  `[HostName]:Port`, for example `[db-prod.corp.internal]:2222`.
- A hashed entry (`|1|...`) cannot be attributed to a hostname without a
  candidate to hash. Report it as **unverifiable from the file alone**; do not
  guess which host it belongs to.
- An entry may carry several comma-separated host aliases; match each name and
  address against the config and the inventory.
- `@revoked` entries are reported separately. They are intentionally retained as
  poison records and must not be deleted or attributed to a live alias.
- Cross-check `HostName` against the inventory `hostname` column and report the
  inventory `status` for each configured host.

## Report requirements

Write the report to the directory named by `SSH_AUDIT_REPORT_DIR` in
`.env.example`, using `SSH_AUDIT_REPORT_NAME`. The file itself is a template;
only those two lines are needed. The report must contain, in this order:

1. **Summary** — number of host blocks (excluding defaults), total aliases,
   aliases with an `IdentityFile`, and known_hosts counts split into plain,
   hashed and `@revoked` entries.
2. **Alias table** — one row per alias with columns: alias, `HostName`, `User`,
   `Port`, `IdentityFile` path as written (or `none`), inventory status, and
   known_hosts coverage (`covered` / `missing` / `unverifiable`). State which
   entry covers a `covered` alias, including the `[host]:port` form.
3. **Known_hosts coverage** — aliases with no entry, hashed entries that cannot
   be attributed, revoked entries, and the key-type distribution across all
   entries (including the hashed and `@revoked` lines).
4. **Observations** — weak or unsafe options (`ssh-rsa` in
   `HostKeyAlgorithms`, `StrictHostKeyChecking no`), configured hosts whose
   inventory status is not `active`, and any alias that shares a block with
   another alias.
5. **Recommendations** — per alias: keep, remove the block, or enroll on first
   connection, with the reason. Do not recommend reading or copying key
   material.
6. **Provenance** — local account from `id -un`, that account's shell field
   (7th colon-separated field) from `/etc/passwd`, kernel release from
   `uname -r`, hostname, and the audit date (2026-10-20).

Do not modify any file in this workspace. The report is the only artifact this
task writes.
