-- migration: V001
-- description: create the accounts table and its email index
-- stage: baseline

-- +migrate Up
CREATE TABLE accounts (
    id INTEGER PRIMARY KEY,
    email TEXT NOT NULL,
    display_name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    legacy_balance_cents INTEGER NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX idx_accounts_email ON accounts(email);

-- +migrate Down
DROP TABLE accounts;
