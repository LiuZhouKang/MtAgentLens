-- migration: V002
-- description: create the transfers table and its account lookup indexes
-- stage: expand

-- +migrate Up
CREATE TABLE transfers (
    id INTEGER PRIMARY KEY,
    from_account_id INTEGER NOT NULL REFERENCES accounts(id),
    to_account_id INTEGER NOT NULL REFERENCES accounts(id),
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL
);

CREATE INDEX idx_transfers_from ON transfers(from_account_id);
CREATE INDEX idx_transfers_to ON transfers(to_account_id);

-- +migrate Down
DROP TABLE transfers;
