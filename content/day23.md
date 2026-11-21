---
day: 23
title: "Functions on Indexed Columns: Why Indexes Get Ignored"
summary: "Wrapping an indexed column in a function or a cast hides it from the planner. How to see it in EXPLAIN, and the fixes: range rewrites, expression indexes and stored generated columns."
teaser: "A function or cast on an indexed column hides it from the planner; range rewrites and expression indexes."
---

An `orders` table has an index on `created_at`, and someone needs every order from 2020:

```sql norun
SELECT *
FROM orders
WHERE EXTRACT(YEAR FROM created_at) = 2020;
```

The query reads as if it should use the index, and it does not. A B-tree on `created_at` is sorted by the full timestamp. PostgreSQL has no way to know where `EXTRACT(YEAR FROM created_at) = 2020` begins and ends in that order, so it reads the whole table and applies `EXTRACT` to every row.

The plans below are from PostgreSQL 18. A function around the column also hides it from an ordinary index in SQL Server, Oracle and MySQL, but I only ran the examples on PostgreSQL, and the fixes differ in syntax between databases.

## The data

One million orders, one every five minutes from 2016 to mid 2025, so 2020 is about a tenth of the table:

```sql
CREATE TABLE orders (
    order_id     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id  INT  NOT NULL,
    email        TEXT NOT NULL,
    status       TEXT,                 -- nullable on purpose, used later
    created_at   TIMESTAMP NOT NULL
);

INSERT INTO orders (customer_id, email, status, created_at)
SELECT g % 5000,
       'user' || g % 20000 || '@Shop.com',
       (ARRAY['completed','completed','completed','pending','cancelled'])[1 + g % 5],
       TIMESTAMP '2016-01-01' + g * INTERVAL '5 minutes'
FROM generate_series(1, 1000000) g;

CREATE INDEX idx_orders_created_at ON orders (created_at);
CREATE INDEX idx_orders_customer   ON orders (customer_id);
CREATE INDEX idx_orders_email      ON orders (email);
CREATE INDEX idx_orders_status     ON orders (status);

-- keeps the plans below free of parallel workers, which depend on server settings
ALTER TABLE orders SET (parallel_workers = 0);
VACUUM ANALYZE orders;
```

The `VACUUM` makes index-only scans possible and `ANALYZE` gives the planner its row counts. Skip them and the plans below can come out differently.

## Function on the column, and the range rewrite

Query A wraps the column:

```sql
EXPLAIN (ANALYZE, COSTS OFF, TIMING OFF, SUMMARY OFF, BUFFERS OFF)
SELECT count(*)
FROM orders
WHERE EXTRACT(YEAR FROM created_at) = 2020;
```

```result
                            QUERY PLAN
-------------------------------------------------------------------
 Aggregate (actual rows=1.00 loops=1)
   ->  Seq Scan on orders (actual rows=105408.00 loops=1)
         Filter: (EXTRACT(year FROM created_at) = '2020'::numeric)
         Rows Removed by Filter: 894592
(4 rows)
```

Query B asks for the same rows as a range:

```sql
EXPLAIN (ANALYZE, COSTS OFF, TIMING OFF, SUMMARY OFF, BUFFERS OFF)
SELECT count(*)
FROM orders
WHERE created_at >= '2020-01-01'
  AND created_at <  '2021-01-01';
```

```result
                                                                           QUERY PLAN
----------------------------------------------------------------------------------------------------------------------------------------------------------------
 Aggregate (actual rows=1.00 loops=1)
   ->  Index Only Scan using idx_orders_created_at on orders (actual rows=105408.00 loops=1)
         Index Cond: ((created_at >= '2020-01-01 00:00:00'::timestamp without time zone) AND (created_at < '2021-01-01 00:00:00'::timestamp without time zone))
         Heap Fetches: 0
         Index Searches: 1
(5 rows)
```

Both count 105,408 orders. The first reads all million rows and discards 894,592 of them (`Rows Removed by Filter`). The second walks the index from the first 2020 entry to the last and never touches the rest. The range is half-open (`>=` the start, `<` the next start), so it is correct whether the column holds dates, timestamps or timestamps with fractions of a second. `BETWEEN '2020-01-01' AND '2020-12-31'` on a timestamp loses everything after midnight on December 31st.

A single day works the same way. `date(created_at) = '2020-06-01'` and `created_at::date = '2020-06-01'` are both sequential scans here, and the rewrite is an index scan:

```sql
EXPLAIN (COSTS OFF)
SELECT * FROM orders
WHERE created_at >= DATE '2020-06-01'
  AND created_at <  DATE '2020-06-01' + 1;
```

```result
                                        QUERY PLAN
------------------------------------------------------------------------------------------
 Index Scan using idx_orders_created_at on orders
   Index Cond: ((created_at >= '2020-06-01'::date) AND (created_at < '2020-06-02'::date))
(2 rows)
```

## Any change to the column counts

The year is the usual case, but the rule is general: if the left side of the comparison is not the bare indexed column, an ordinary index on that column cannot be used.

```sql norun
WHERE lower(email) = 'user42@shop.com'      -- function
WHERE substring(email, 1, 6) = 'user42'     -- function
WHERE customer_id + 1 = 43                  -- arithmetic
WHERE customer_id::text = '42'              -- cast
WHERE date(created_at) = '2020-06-01'       -- function
```

PostgreSQL does not do algebra for you. `customer_id + 1 = 43` obviously means `customer_id = 42`, and it is still a sequential scan:

```sql
EXPLAIN (COSTS OFF)
SELECT * FROM orders WHERE customer_id + 1 = 43;
```

```result
             QUERY PLAN
------------------------------------
 Seq Scan on orders
   Filter: ((customer_id + 1) = 43)
(2 rows)
```

## Fix 1: move the work to the constant side

If the computation can go to the other side of the operator, put it there. The constant is evaluated once, before the scan starts:

```sql
EXPLAIN (COSTS OFF)
SELECT * FROM orders WHERE customer_id = 43 - 1;
```

```result
                   QUERY PLAN
------------------------------------------------
 Bitmap Heap Scan on orders
   Recheck Cond: (customer_id = 42)
   ->  Bitmap Index Scan on idx_orders_customer
         Index Cond: (customer_id = 42)
(4 rows)
```

`43 - 1` is folded to `42` before planning. A cast on the column is the same problem as `lower(email)`:

```sql
EXPLAIN (COSTS OFF)
SELECT * FROM orders WHERE customer_id::text = '42';
```

```result
                  QUERY PLAN
----------------------------------------------
 Seq Scan on orders
   Filter: ((customer_id)::text = '42'::text)
(2 rows)
```

Write `customer_id = 42` and the cast is gone.

## Fix 2: an expression index

When the function is really needed, as in a case-insensitive email lookup, index the expression itself:

```sql
EXPLAIN (COSTS OFF)
SELECT * FROM orders WHERE lower(email) = 'user42@shop.com';
```

```result
                     QUERY PLAN
----------------------------------------------------
 Seq Scan on orders
   Filter: (lower(email) = 'user42@shop.com'::text)
(2 rows)
```

```sql
CREATE INDEX idx_orders_email_lower ON orders (lower(email));
ANALYZE orders;

EXPLAIN (COSTS OFF)
SELECT * FROM orders WHERE lower(email) = 'user42@shop.com';
```

```result
                          QUERY PLAN
--------------------------------------------------------------
 Bitmap Heap Scan on orders
   Recheck Cond: (lower(email) = 'user42@shop.com'::text)
   ->  Bitmap Index Scan on idx_orders_email_lower
         Index Cond: (lower(email) = 'user42@shop.com'::text)
(4 rows)
```

Three things to know about expression indexes:

- The query has to use the same expression. An index on `lower(email)` does nothing for `upper(email)` or `email ILIKE ...`.
- The function must be `IMMUTABLE`, because PostgreSQL stores its results. The next section shows where that bites.
- Every `INSERT`, and every `UPDATE` that touches the column, evaluates the expression again, so it costs write time and disk space like any other index. If you control every writer, storing the email already lowercased is cheaper.

An expression index can also stand in for the range rewrite, for example when a tool generates the query and you cannot change it:

```sql
CREATE INDEX idx_orders_year ON orders ((EXTRACT(YEAR FROM created_at)));
ANALYZE orders;

EXPLAIN (COSTS OFF)
SELECT * FROM orders WHERE EXTRACT(YEAR FROM created_at) = 2020;
```

```result
                           QUERY PLAN
-----------------------------------------------------------------
 Index Scan using idx_orders_year on orders
   Index Cond: (EXTRACT(year FROM created_at) = '2020'::numeric)
(2 rows)
```

This index serves year lookups and nothing else. The range rewrite on `idx_orders_created_at` serves any period with the index you already have, so try the rewrite first.

### Timestamps with time zone

The index above works because `created_at` is `TIMESTAMP`. On a `TIMESTAMPTZ` column the year of a given instant depends on the session's `TimeZone` setting, so `EXTRACT(YEAR FROM ...)`, `date(...)` and `date_trunc(...)` are only `STABLE`, and PostgreSQL refuses to index them:

```sql
CREATE TABLE events (
    event_id  INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    at        TIMESTAMPTZ NOT NULL
);
```

```sql norun
CREATE INDEX ON events ((EXTRACT(YEAR FROM at)));
-- ERROR:  functions in index expression must be marked IMMUTABLE

CREATE INDEX ON events (date_trunc('month', at));
-- ERROR:  functions in index expression must be marked IMMUTABLE
```

Convert to a fixed zone first and the expression no longer depends on the session:

```sql
CREATE INDEX idx_events_day_utc ON events (date(at AT TIME ZONE 'UTC'));
```

Queries then have to repeat `date(at AT TIME ZONE 'UTC') = ...` exactly. The range rewrite is simpler: `at >= '2020-01-01 00:00 UTC' AND at < '2021-01-01 00:00 UTC'` names the zone in the literal and uses a plain index on `at`. A literal without a zone is read in the session's time zone, so two connections with different settings ask for different years.

## Fix 3: a stored generated column

A generated column gives the derived value a name and lets an ordinary B-tree index it:

```sql
ALTER TABLE orders
    ADD COLUMN created_year INT
    GENERATED ALWAYS AS (EXTRACT(YEAR FROM created_at)::INT) STORED;

CREATE INDEX idx_orders_created_year ON orders (created_year);
ANALYZE orders;

EXPLAIN (COSTS OFF)
SELECT * FROM orders WHERE created_year = 2020;
```

```result
                     QUERY PLAN
----------------------------------------------------
 Index Scan using idx_orders_created_year on orders
   Index Cond: (created_year = 2020)
(2 rows)
```

`STORED` is required if you want an index. PostgreSQL 18 added virtual generated columns, which are computed when read and are the default when you leave out `STORED`, but it does not allow indexes on them:

```sql
CREATE TABLE gen_demo (
    a  INT,
    b  INT GENERATED ALWAYS AS (a * 2) VIRTUAL
);
```

```sql norun
CREATE INDEX ON gen_demo (b);
-- ERROR:  indexes on virtual generated columns are not supported
```

Adding a stored column rewrites the whole table, which on a large production table is a real cost. Other databases offer the same idea: MySQL 8 indexes generated columns and, from 8.0.13, also accepts an expression as an index key part; SQL Server indexes persisted computed columns; Oracle has function-based indexes.

## Three cases that look different

### Type mismatches

A cast is not always in the SQL you wrote. PostgreSQL will not compare text with an integer at all (`WHERE email = 12345` fails with `operator does not exist: text = integer`), so that mistake gets caught. A decimal literal against an integer column is not caught:

```sql
EXPLAIN (COSTS OFF)
SELECT * FROM orders WHERE customer_id = 42.0;
```

```result
                QUERY PLAN
-------------------------------------------
 Seq Scan on orders
   Filter: ((customer_id)::numeric = 42.0)
(2 rows)
```

There is no `integer = numeric` operator that can use the integer index, so PostgreSQL casts the column. The same thing happens when a driver sends a decimal or floating-point parameter for an integer column. The sign is a `Filter:` line with a cast on the column, here `(customer_id)::numeric`.

### COALESCE

About one order in a thousand has no status. Treating NULL as `'unknown'` with `COALESCE` hides the column again:

```sql
UPDATE orders SET status = NULL WHERE order_id % 1000 = 0;
ANALYZE orders;

EXPLAIN (COSTS OFF)
SELECT * FROM orders WHERE COALESCE(status, 'unknown') = 'unknown';
```

```result
                           QUERY PLAN
-----------------------------------------------------------------
 Seq Scan on orders
   Filter: (COALESCE(status, 'unknown'::text) = 'unknown'::text)
(2 rows)
```

```sql
EXPLAIN (COSTS OFF)
SELECT * FROM orders WHERE status IS NULL OR status = 'unknown';
```

```result
                            QUERY PLAN
------------------------------------------------------------------
 Bitmap Heap Scan on orders
   Recheck Cond: ((status IS NULL) OR (status = 'unknown'::text))
   ->  BitmapOr
         ->  Bitmap Index Scan on idx_orders_status
               Index Cond: (status IS NULL)
         ->  Bitmap Index Scan on idx_orders_status
               Index Cond: (status = 'unknown'::text)
(7 rows)
```

PostgreSQL indexes NULLs in B-trees, so `IS NULL` can use the index. Oracle does not store entries for all-NULL keys in a B-tree index, so this rewrite does not carry over there.

### Functions in join conditions

`ON lower(o.email) = lower(w.email)` is the same problem inside a join. Take a small `watchlist` table and join it to `orders`. Without the expression index, `orders` is scanned in full:

```sql
CREATE TABLE watchlist (email TEXT PRIMARY KEY);
INSERT INTO watchlist
SELECT 'USER' || g || '@shop.com' FROM generate_series(1, 5) g;
ANALYZE watchlist;

DROP INDEX idx_orders_email_lower;

EXPLAIN (COSTS OFF)
SELECT o.*
FROM watchlist w
JOIN orders o ON lower(o.email) = lower(w.email);
```

```result
                   QUERY PLAN
------------------------------------------------
 Hash Join
   Hash Cond: (lower(o.email) = lower(w.email))
   ->  Seq Scan on orders o
   ->  Hash
         ->  Seq Scan on watchlist w
(5 rows)
```

With the index back, PostgreSQL probes `orders` once per watched address:

```sql
CREATE INDEX idx_orders_email_lower ON orders (lower(email));
ANALYZE orders;

EXPLAIN (COSTS OFF)
SELECT o.*
FROM watchlist w
JOIN orders o ON lower(o.email) = lower(w.email);
```

```result
                        QUERY PLAN
-----------------------------------------------------------
 Nested Loop
   ->  Seq Scan on watchlist w
   ->  Bitmap Heap Scan on orders o
         Recheck Cond: (lower(email) = lower(w.email))
         ->  Bitmap Index Scan on idx_orders_email_lower
               Index Cond: (lower(email) = lower(w.email))
(6 rows)
```

When both sides are large, a hash join over everything is often the right plan and the index would not change it. The index pays off when one side is small and the other large, as here.

## Finding these queries in a real database

On a live system, start from the symptom, a table that is read sequentially far more than expected:

```sql norun
SELECT relname, seq_scan, seq_tup_read, idx_scan,
       seq_tup_read / NULLIF(seq_scan, 0) AS avg_rows_per_seq_scan
FROM pg_stat_user_tables
ORDER BY seq_tup_read DESC
LIMIT 10;
```

A big table with a high `seq_tup_read` and millions of rows per sequential scan is the one to look at. Then find the statements: with the `pg_stat_statements` extension, sort by `total_exec_time` and read the `WHERE` clauses for functions around column names. `auto_explain` with `auto_explain.log_min_duration` logs the plans of slow statements, including the `Filter:` line.

## In short

- A function, cast or arithmetic on the indexed column hides it from an ordinary index. A `Seq Scan` whose `Filter:` line shows the expression is the symptom, and `Rows Removed by Filter` shows what it cost.
- For dates, rewrite to a half-open range on the bare column. One index then serves any period.
- Move casts and arithmetic to the constant side, and send parameters in the column's own type.
- If the expression is unavoidable, index that same expression, or add a stored generated column and index it. Both cost write time and space.
- On `TIMESTAMPTZ`, the zone has to be explicit before an expression can be indexed.
