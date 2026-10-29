-- migration: V005
-- description: contract legacy_balance_cents after the dual-write window closes
-- stage: contract
-- risk: destructive
--
-- preflight: run the query below and require zero rows before promotion:
--   SELECT id FROM accounts WHERE balance_cents != legacy_balance_cents;
--
-- rollback-note: the down migration recreates the column with default 0. It
-- cannot restore the original per-account values; use a pre-V005 snapshot or
-- the reconciled dual-write ledger for recovery.

-- +migrate Up
ALTER TABLE accounts DROP COLUMN legacy_balance_cents;

-- +migrate Down
ALTER TABLE accounts ADD COLUMN legacy_balance_cents INTEGER NOT NULL DEFAULT 0;
