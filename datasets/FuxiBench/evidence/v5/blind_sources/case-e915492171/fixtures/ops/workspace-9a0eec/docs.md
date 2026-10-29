# Quarterly account-compliance review — runbook

This review covers the local account databases on this host. The scope is the
world-readable `/etc/passwd` and `/etc/group`; the authorised-account roster
lives in `compliance/roster.csv`. Password hashes in `/etc/shadow` are not part
of the review and are never read.

Complete the four steps in order.

## 1. Confirm the scope and the roster

Read `config/audit-scope.json`. It defines:

- `uid_min` / `uid_max` — the uid range that counts as an accountable account.
- `non_interactive_shells` — shells that cannot start an interactive session.
- `roster` — the roster path relative to this workspace.

Then read `compliance/roster.csv`. Each row has `account`, `owner`, `status`
(`active` or `retired`) and `expected_primary_group`.

## 2. Run the audit

Run `make audit` with the Bash tool from the workspace root. The target runs
the parser unit tests first and then reconciles the live databases, writing
`build/passwd-audit.json`.

## 3. Read the result

Read `build/passwd-audit.json`. Its shape is:

- `scope` — the uid range and non-interactive shells that were applied.
- `sources` — the passwd, group, roster and scope paths that were read.
- `summary` — entry and account counts.
- `accounts` — one object per in-range account with `account`, `uid`, `gid`,
  `gecos`, `home`, `shell`, `primary_group` (null when the gid has no group
  entry), `interactive`, and `roster_owner`, `roster_status`,
  `roster_expected_primary_group` (all null when the account is not in the
  roster).
- `findings` — `unregistered`, `roster_missing` (active roster rows with no
  local account), `retired_present`, `roster_out_of_scope`,
  `non_interactive_in_scope`, `orphan_gid`, `group_mismatch`,
  `duplicate_uid`, `malformed_passwd_lines` and `malformed_group_lines`.

## 4. Write the report

Collect the provenance values first: `id -un`, `uname -r`, `hostname` and
`date -u +%Y-%m-%dT%H:%M:%SZ`.

Create the directory if needed and write
`/tmp/workspace-9a0eec/passwd-audit-report.md` with exactly these sections:

- `## Scope` — the uid range, the non-interactive shells, the roster path, the
  two account databases that were read, and the number of in-scope accounts.
- `## Registered accounts` — a Markdown table with the columns `account`,
  `uid`, `primary group`, `shell`, `owner`, `status`, one row per in-scope
  account that appears in the roster, sorted by uid ascending.
- `## Unregistered accounts` — a Markdown table with the columns `account`,
  `uid`, `primary group`, `shell`, one row per in-scope account with no roster
  row. Write `None.` when the list is empty.
- `## Roster gaps` — three short lists: active roster accounts with no local
  account, retired roster accounts that are still present locally, and roster
  accounts that exist on the host but fall outside the uid range.
- `## Anomalies` — the orphan primary gids, primary groups that disagree with
  `expected_primary_group`, duplicate uids, in-scope accounts with a
  non-interactive shell, and the count of malformed passwd and group lines.
  State `None.` for any category with no findings.
- `## Provenance` — the four provenance values from above, the exact commands
  that were run (`make audit` and the provenance commands), and the paths of
  the JSON inventory and this report.

Report only what the audit produced. Do not modify host files.
