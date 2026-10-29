-- migration: V004
-- description: add balance_cents, backfill it, and add transfer events
-- stage: expand-backfill

-- +migrate Up
ALTER TABLE accounts ADD COLUMN balance_cents INTEGER NOT NULL DEFAULT 0;

UPDATE accounts
SET balance_cents = legacy_balance_cents;

CREATE INDEX idx_transfers_status_created ON transfers(status, created_at);

CREATE TABLE transfer_events (
    id INTEGER PRIMARY KEY,
    transfer_id INTEGER NOT NULL REFERENCES transfers(id),
    event_type TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX idx_transfer_events_transfer ON transfer_events(transfer_id, id);

-- +migrate Down
DROP TABLE transfer_events;
DROP INDEX idx_transfers_status_created;

-- Remove balance_cents with a table rebuild so the down path does not depend
-- on ALTER TABLE DROP COLUMN support.
CREATE TABLE accounts_pre_balance (
    id INTEGER PRIMARY KEY,
    email TEXT NOT NULL,
    display_name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    legacy_balance_cents INTEGER NOT NULL DEFAULT 0,
    tier TEXT NOT NULL DEFAULT 'standard'
);

INSERT INTO accounts_pre_balance (id, email, display_name, created_at, legacy_balance_cents, tier)
SELECT id, email, display_name, created_at, legacy_balance_cents, tier
FROM accounts;

DROP TABLE accounts;

ALTER TABLE accounts_pre_balance RENAME TO accounts;

CREATE UNIQUE INDEX idx_accounts_email ON accounts(email);
