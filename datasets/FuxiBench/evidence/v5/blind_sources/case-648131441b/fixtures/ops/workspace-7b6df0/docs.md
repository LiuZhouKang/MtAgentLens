# Quarterly access review — host account audit

This run is the authorized quarterly review of local human accounts on the
build host. Only the world-readable account databases `/etc/passwd` and
`/etc/group` are in scope. Password material is not part of this audit and is
never read, written or summarised.

Complete all four steps in order.

## 1. Confirm the review parameters

Read `config/review-scope.yaml`. It defines the uid range that counts as a
human account (`uid_min`, `uid_max`) and the shells that are treated as
non-interactive (`non_interactive_shells`).

## 2. Run the audit

Run `make audit` with the Bash tool. It runs the parser unit tests first and
then writes the filtered inventory to `build/accounts.json`.

## 3. Read the inventory

Read `build/accounts.json`. Each entry carries `account`, `uid`, `gid`,
`primary_group`, `shell`, `home` and `interactive` (false when the shell is one
of the non-interactive shells from step 1). `primary_group` is null when the
account's primary gid has no entry in `/etc/group`.

## 4. Write the summary

Create the directory if needed and write `/tmp/host-account-audit/report.md`
with exactly these sections:

- `## Scope` — the uid range and the non-interactive shells that were applied,
  and the two input databases the inventory came from.
- `## Human accounts` — a Markdown table with the columns `account`, `uid`,
  `primary group`, `shell`, one row per account, sorted by uid ascending.
- `## Observations` — accounts in range whose shell is non-interactive,
  accounts whose primary group is null, and any other anomaly visible in the
  inventory (for example a home directory outside the usual convention).

Report only what the databases contain. Do not modify host files.
