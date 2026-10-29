# Billing schema v1 -> v2 migration specification

## 1. Scope

The billing service stores customers, orders and order line items in one
SQLite database. The v1 schema is frozen in `schema/legacy_schema.sql`. The
platform team has approved a v2 schema that normalises order status into a
reference table, adds a customer locale, adds an order lookup index, and
redefines the two analytics views.

Write the forward migration as a single SQL script and save it to
`/tmp/migration.sql`. The script is applied once, by an operator, to a copy of
the production database. It does not need to be re-runnable. Every statement
must be valid when applied to a database that currently has the v1 schema, the
v1 analytics views, and the rows seeded from `data/sample_rows.sql`.

## 2. Target engine

SQLite 3.35 or newer. `ALTER TABLE ... DROP COLUMN` requires 3.35, so do not
replace it with a table rebuild. Use plain SQLite SQL only: no Postgres
extensions, no procedural blocks, no `IF EXISTS` guards on `ALTER TABLE`.

## 3. Workspace files

| File | Purpose |
| --- | --- |
| `{{FIXTURE_DIR}}/docs.md` | this specification |
| `{{FIXTURE_DIR}}/schema/legacy_schema.sql` | authoritative v1 DDL |
| `{{FIXTURE_DIR}}/schema/analytics_views.sql` | the two v1 analytics views; both reference `orders.status` |
| `{{FIXTURE_DIR}}/data/sample_rows.sql` | representative rows used by the validator |
| `{{FIXTURE_DIR}}/data/status_values.csv` | observed v1 status values and their v2 state |
| `{{FIXTURE_DIR}}/config/db.env.example` | placeholder connection settings (reference only) |
| `{{FIXTURE_DIR}}/tools/check_migration.py` | validator invoked by `make check` |
| `{{FIXTURE_DIR}}/Makefile` | build entry point (`make check`) |

`config/db.env.example` contains placeholders only. The validator never opens a
network connection: it applies the migration to an in-memory SQLite database
seeded with the v1 schema, the v1 views, and the sample rows. Do not connect
to the database named in the env example.

## 4. Required forward migration

The script must perform the following steps, in this order, inside one
transaction.

1. **Transaction.** Open with `BEGIN;` and close the forward section with
   `COMMIT;`.

2. **Migration bookkeeping.** Create the bookkeeping table if it is absent and
   record version 2:

   ```sql
   CREATE TABLE IF NOT EXISTS schema_migrations (
       version INTEGER PRIMARY KEY,
       applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
   );
   INSERT OR IGNORE INTO schema_migrations (version) VALUES (2);
   ```

3. **Drop the legacy views.** Drop `v_order_totals` and `v_active_customers`
   before changing any column. Both views reference `orders.status`, so
   SQLite refuses to drop that column while they exist. Use
   `DROP VIEW IF EXISTS`.

4. **Customer locale.** Add the locale column with an `en-US` default:

   ```sql
   ALTER TABLE customers ADD COLUMN locale TEXT NOT NULL DEFAULT 'en-US';
   ```

5. **Order state reference table.** Create:

   ```sql
   CREATE TABLE order_states (
       code TEXT PRIMARY KEY,
       label TEXT NOT NULL
   );
   ```

   Populate exactly these five rows, with these exact codes and labels:

   | code | label | legacy status |
   | --- | --- | --- |
   | `pending` | `Pending payment` | `new` |
   | `paid` | `Paid` | `paid` |
   | `fulfilled` | `Fulfilled` | `shipped` |
   | `closed` | `Closed` | `closed` |
   | `cancelled` | `Cancelled` | `cancelled` |

6. **Normalise `orders.status`.** Add
   `state_code TEXT NOT NULL DEFAULT 'pending'`, then backfill every existing
   row from `status` using the mapping in step 5. Every legacy status value
   listed in `data/status_values.csv` must be mapped; do not leave a row with
   the add-column default. After the backfill, remove the legacy column:

   ```sql
   ALTER TABLE orders DROP COLUMN status;
   ```

7. **Order lookup index.** Create an index named
   `idx_orders_customer_created` on `orders(customer_id, created_at)`, in that
   column order.

8. **Recreate `v_order_totals`.** The view must expose exactly the columns
   `order_id`, `customer_id`, `state_code`, `total_cents`, in that order, with
   one row per order. `total_cents` is the sum of
   `order_items.qty * order_items.unit_price_cents` for the order, and `0`
   when the order has no line items.

9. **Recreate `v_active_customers`.** The view must expose exactly the columns
   `customer_id`, `email`, `active_orders`, in that order, with one row per
   customer that has at least one order whose `state_code` is `paid` or
   `fulfilled`. `active_orders` counts exactly those orders.

10. **Preserve data.** Do not delete, truncate or rewrite rows in `customers`,
    `orders` or `order_items` beyond the changes above. All seeded rows must
    survive with their ids and values intact.

## 5. Rollback documentation

After `COMMIT;`, append a documentation-only rollback section. Its first line
must be the comment

```sql
-- ROLLBACK SCRIPT (documentation only; not executed by this migration)
```

and every following line of the file must be an SQL comment starting with
`--`. Describe the reverse steps (recreate the v1 views, re-add `status`,
drop `state_code`, drop `order_states`, drop the index, drop `schema_migrations`
row 2). The validator only checks the marker; the section must contain no
executable SQL.

## 6. Validation

Run the validator from the workspace root:

```bash
make check
```

It applies `/tmp/migration.sql` to a scratch in-memory database, then prints
one `[PASS]` or `[FAIL]` line per requirement above and a final
`RESULT: n/n checks passed` line. All checks must pass. `make check` reads
`/tmp/migration.sql` by default; override with `MIGRATION=<path>` if needed.
