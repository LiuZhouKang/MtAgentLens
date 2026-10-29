-- Representative billing rows used by tools/check_migration.py.
-- Seeded into a scratch in-memory database after the v1 schema and views.

INSERT INTO customers (id, email, full_name, created_at) VALUES
    (1, 'alice@example.com', 'Alice Chen',  '2026-01-05T09:12:00Z'),
    (2, 'bob@example.com',   'Bob Ortiz',   '2026-01-19T14:03:00Z'),
    (3, 'carol@example.com', 'Carol Ng',    '2026-02-02T08:44:00Z'),
    (4, 'dave@example.com',  'Dave Patel',  '2026-02-21T17:30:00Z');

INSERT INTO orders (id, customer_id, status, total_cents, created_at) VALUES
    (1, 1, 'new',       4800, '2026-03-01T10:00:00Z'),
    (2, 1, 'paid',     12900, '2026-03-02T11:15:00Z'),
    (3, 2, 'shipped',   7600, '2026-03-03T09:45:00Z'),
    (4, 2, 'closed',    2500, '2026-03-04T16:20:00Z'),
    (5, 3, 'cancelled', 9900, '2026-03-05T12:05:00Z'),
    (6, 3, 'paid',      3300, '2026-03-06T13:40:00Z'),
    (7, 4, 'paid',      5400, '2026-03-07T15:55:00Z'),
    (8, 4, 'shipped',   8600, '2026-03-08T08:10:00Z'),
    (9, 4, 'new',          0, '2026-03-09T10:30:00Z');

INSERT INTO order_items (id, order_id, sku, qty, unit_price_cents) VALUES
    (1,  1, 'SKU-RED-01', 2, 1500),
    (2,  1, 'SKU-RED-02', 1, 1800),
    (3,  2, 'SKU-BLU-01', 3, 4300),
    (4,  3, 'SKU-GRN-01', 2, 2100),
    (5,  3, 'SKU-GRN-02', 1, 3400),
    (6,  4, 'SKU-YEL-01', 5,  500),
    (7,  5, 'SKU-PUR-01', 2, 4950),
    (8,  6, 'SKU-BLK-01', 3, 1100),
    (9,  7, 'SKU-ORG-01', 1, 2400),
    (10, 7, 'SKU-ORG-02', 1, 3000),
    (11, 8, 'SKU-TEA-01', 1, 4000),
    (12, 8, 'SKU-TEA-02', 1, 4600);
