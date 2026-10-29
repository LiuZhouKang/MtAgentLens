-- migration: V003
-- description: add the account tier column with a safe default
-- stage: expand

-- +migrate Up
ALTER TABLE accounts ADD COLUMN tier TEXT NOT NULL DEFAULT 'standard';

-- +migrate Down
-- SQLite cannot drop a column portably on every supported version, so rebuild
-- the table and copy the columns that existed before V003.
CREATE TABLE accounts_pre_tier (
    id INTEGER PRIMARY KEY,
    email TEXT NOT NULL,
    display_name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    legacy_balance_cents INTEGER NOT NULL DEFAULT 0
);

INSERT INTO accounts_pre_tier (id, email, display_name, created_at, legacy_balance_cents)
SELECT id, email, display_name, created_at, legacy_balance_cents
FROM accounts;

DROP TABLE accounts;

ALTER TABLE accounts_pre_tier RENAME TO accounts;

CREATE UNIQUE INDEX idx_accounts_email ON accounts(email);
