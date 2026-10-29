-- Legacy analytics views (v1).
-- Both views reference orders.status, so SQLite will reject
-- "ALTER TABLE orders DROP COLUMN status" until they are dropped.

CREATE VIEW v_order_totals AS
SELECT o.id AS order_id,
       o.customer_id,
       o.status,
       COALESCE(SUM(i.qty * i.unit_price_cents), 0) AS total_cents
FROM orders o
LEFT JOIN order_items i ON i.order_id = o.id
GROUP BY o.id, o.customer_id, o.status;

CREATE VIEW v_active_customers AS
SELECT c.id AS customer_id,
       c.email,
       COUNT(o.id) AS active_orders
FROM customers c
JOIN orders o ON o.customer_id = c.id
WHERE o.status IN ('new', 'paid')
GROUP BY c.id, c.email;
