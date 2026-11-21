---
day: 27
title: "Partial Indexes: Index Only the Rows You Query"
summary: "A partial index covers only the rows that match a WHERE condition. Sizes and plans in PostgreSQL 18, when the planner can use one and when it cannot, partial unique indexes, and what other databases offer."
teaser: "Index only the rows you query: sizes, when the planner can use it, partial unique indexes."
---

Support looks at failed orders all day. The `orders` table has a million rows, and 0.48% of them have `status = 'failed'`. An index on `status` makes the query fast, but it also indexes the 920,000 completed orders that nobody searches by status. Those entries take disk and memory, and every insert has to maintain them.

A partial index has a `WHERE` clause and contains only the rows that satisfy it. If you want failed orders, index failed orders.

The examples run on PostgreSQL 18. SQL Server calls the same feature a filtered index, SQLite supports it too, and MySQL has nothing equivalent. The last section covers that.

## The data

```sql
CREATE TABLE orders (
    order_id     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id  INT NOT NULL,
    status       TEXT NOT NULL,
    total        NUMERIC(10,2) NOT NULL,
    created_at   TIMESTAMP NOT NULL
);

INSERT INTO orders (customer_id, status, total, created_at)
SELECT g % 20000,
       CASE WHEN g % 10000 < 9200 THEN 'completed'
            WHEN g % 10000 < 9950 THEN 'pending'
            WHEN g % 10000 < 9998 THEN 'failed'
            ELSE 'disputed'
       END,
       (g * 37) % 500,
       TIMESTAMP '2024-01-01' + g * INTERVAL '30 seconds'
FROM generate_series(1, 1000000) g;

-- keeps the plans below free of parallel workers, which depend on server settings
ALTER TABLE orders SET (parallel_workers = 0);

SELECT status, count(*) AS orders
FROM orders
GROUP BY status
ORDER BY count(*) DESC;
```

```result
  status   | orders
-----------+--------
 completed | 920000
 pending   |  75000
 failed    |   4800
 disputed  |    200
(4 rows)
```

## A first partial index

```sql
CREATE INDEX idx_orders_failed_created
ON orders (created_at)
WHERE status = 'failed';

VACUUM ANALYZE orders;
```

The index is sorted by `created_at`, and only rows with `status = 'failed'` are in it. For comparison, here is the same index without the condition:

```sql
CREATE INDEX idx_orders_created_all ON orders (created_at);

SELECT indexrelid::regclass AS index_name,
       pg_size_pretty(pg_relation_size(indexrelid)) AS size
FROM pg_index
WHERE indrelid = 'orders'::regclass
ORDER BY pg_relation_size(indexrelid), indexrelid::regclass::text;
```

```result
        index_name         |  size
---------------------------+--------
 idx_orders_failed_created | 128 kB
 idx_orders_created_all    | 21 MB
 orders_pkey               | 21 MB
(3 rows)
```

The partial index holds 4,800 entries instead of a million and is around 170 times smaller than the full one, which is close to the 0.48% of the table it covers. Smaller means more of it stays in memory, scans are shorter, and inserts of rows that are not `failed` do no index work at all. (The primary key is listed for scale.)

## When the planner uses it

PostgreSQL uses a partial index only when it can prove that the query's `WHERE` clause implies the index's `WHERE`. Every row the query can return must be in the index. Start with queries where that is obvious:

```sql
EXPLAIN (COSTS OFF)
SELECT * FROM orders WHERE status = 'failed';
```

```result
                      QUERY PLAN
------------------------------------------------------
 Index Scan using idx_orders_failed_created on orders
(1 row)
```

The plan has no `Index Cond` line: the whole partial index is the set of failed orders, so it is scanned from start to end. Extra conditions are fine, as long as the index condition is still implied:

```sql
EXPLAIN (COSTS OFF)
SELECT * FROM orders WHERE status = 'failed' AND total > 100;
```

```result
                      QUERY PLAN
------------------------------------------------------
 Index Scan using idx_orders_failed_created on orders
   Filter: (total > '100'::numeric)
(2 rows)
```

The extra condition is checked as a filter on each row. Sorting and limiting use the index order, which the first column `created_at` provides:

```sql
EXPLAIN (COSTS OFF)
SELECT * FROM orders
WHERE status = 'failed'
ORDER BY created_at DESC
LIMIT 50;
```

```result
                             QUERY PLAN
---------------------------------------------------------------------
 Limit
   ->  Index Scan Backward using idx_orders_failed_created on orders
(2 rows)
```

Even with the full `idx_orders_created_all` available, the planner takes the small one, because it covers exactly the rows this query wants. The planner can also see through a one-element array, which I expected to defeat it:

```sql
EXPLAIN (COSTS OFF)
SELECT * FROM orders WHERE status = ANY ('{failed}'::text[]);
```

```result
                      QUERY PLAN
------------------------------------------------------
 Index Scan using idx_orders_failed_created on orders
(1 row)
```

### Queries that cannot use it

A different value, or a condition that is broader than the index condition, gets a sequential scan:

```sql
EXPLAIN (COSTS OFF)
SELECT * FROM orders WHERE status = 'pending';
```

```result
              QUERY PLAN
--------------------------------------
 Seq Scan on orders
   Filter: (status = 'pending'::text)
(2 rows)
```

```sql
EXPLAIN (COSTS OFF)
SELECT * FROM orders WHERE status IN ('failed', 'disputed');
```

```result
                       QUERY PLAN
--------------------------------------------------------
 Seq Scan on orders
   Filter: (status = ANY ('{failed,disputed}'::text[]))
(2 rows)
```

The second is the common surprise. The index knows nothing about disputed orders, so it cannot answer a query that asks for them too. The same happens with `status <> 'completed'`: PostgreSQL does not derive "this includes failed" from it. If you want both, either index both statuses (`WHERE status IN ('failed', 'disputed')`, and then the query has to use a condition that implies that) or have two indexes.

### Parameters

A query written with a parameter (`WHERE status = $1`) is another way to lose the index. A plan built before the value is known has to work for every value, and a partial index only works for one. PostgreSQL normally plans each of the first few executions of a prepared statement with the actual value, so the index is used. If it switches to a generic plan, or you force one:

```sql
CREATE TABLE tickets (
    ticket_id  INT PRIMARY KEY,
    status     TEXT NOT NULL
);

INSERT INTO tickets
SELECT g, CASE WHEN g % 100 = 0 THEN 'failed' ELSE 'ok' END
FROM generate_series(1, 100000) g;

CREATE INDEX idx_tickets_failed ON tickets (ticket_id) WHERE status = 'failed';
ANALYZE tickets;

PREPARE find_tickets (text) AS SELECT * FROM tickets WHERE status = $1;
SET plan_cache_mode = force_generic_plan;

EXPLAIN (COSTS OFF) EXECUTE find_tickets ('failed');
```

```result
       QUERY PLAN
-------------------------
 Seq Scan on tickets
   Filter: (status = $1)
(2 rows)
```

```sql
SET plan_cache_mode = force_custom_plan;

EXPLAIN (COSTS OFF) EXECUTE find_tickets ('failed');
```

```result
                   QUERY PLAN
------------------------------------------------
 Index Scan using idx_tickets_failed on tickets
(1 row)
```

The generic plan reads the whole table, and the custom plan, planned with `'failed'` in hand, uses the index. Drivers and ORMs that use server-side prepared statements can end up on generic plans, so in an application, check the plans of the queries that are supposed to use a partial index. SQL Server filtered indexes run into the same problem with parameterized queries, because a cached plan must be valid for every parameter value. If an application passes the status as a parameter, a literal in the SQL or an ordinary index serves it better.

## Partial unique indexes

A unique index with a condition enforces uniqueness among the rows that match it. A customer can have one active subscription and any number of expired ones:

```sql
CREATE TABLE subscriptions (
    subscription_id  INT PRIMARY KEY,
    customer_id      INT NOT NULL,
    status           TEXT NOT NULL
);

CREATE UNIQUE INDEX idx_one_active_subscription
ON subscriptions (customer_id)
WHERE status = 'active';

INSERT INTO subscriptions VALUES
    (1, 7, 'expired'),
    (2, 7, 'expired'),
    (3, 7, 'active');
```

A second active subscription for the same customer is rejected:

```sql norun
INSERT INTO subscriptions VALUES (4, 7, 'active');
-- ERROR:  duplicate key value violates unique constraint "idx_one_active_subscription"
-- DETAIL:  Key (customer_id)=(7) already exists.
```

Once the first one expires, it is allowed:

```sql
UPDATE subscriptions SET status = 'expired' WHERE subscription_id = 3;
INSERT INTO subscriptions VALUES (4, 7, 'active');

SELECT * FROM subscriptions ORDER BY subscription_id;
```

```result
 subscription_id | customer_id | status
-----------------+-------------+---------
               1 |           7 | expired
               2 |           7 | expired
               3 |           7 | expired
               4 |           7 | active
(4 rows)
```

`INSERT ... ON CONFLICT` has to name the index condition as well, since a partial index is not a constraint in the usual sense. Without it PostgreSQL reports `there is no unique or exclusion constraint matching the ON CONFLICT specification`:

```sql
INSERT INTO subscriptions VALUES (5, 7, 'active')
ON CONFLICT (customer_id) WHERE status = 'active' DO NOTHING;
```

The same pattern covers "one primary email per user" (`WHERE is_primary`) and "username unique among non-deleted users" (`WHERE deleted_at IS NULL`). The second one lets a new user take a soft-deleted user's name without a hard delete.

## Conditions have to be immutable

The index condition is stored with the index and has to give the same answer for a row today and next year. Functions in it must be `IMMUTABLE`, so `now()` is out:

```sql norun
CREATE INDEX ON orders (created_at)
WHERE created_at > now() - interval '90 days';
-- ERROR:  functions in index predicate must be marked IMMUTABLE
```

A subquery or a reference to another table is not allowed in a predicate either. A "last 90 days" index therefore needs a literal date (`WHERE created_at > '2024-06-01'`), and someone has to build a new index with a later date from time to time and drop the old one (`CREATE INDEX CONCURRENTLY` avoids blocking writes). A generated column cannot help, because it cannot depend on the current date either. A boolean flag column that a scheduled job maintains (`is_recent`) works, at the cost of that job's updates.

Queries must also state the date in a way PostgreSQL can compare with the literal. `created_at > '2024-07-01'` implies `created_at > '2024-06-01'`, so it can use the index. `created_at > $1` does not, for the same reason as the parameter example above.

## Keeping an eye on them

Size is easy to see; usage is in `pg_stat_user_indexes`:

```sql norun
SELECT indexrelname AS index_name,
       idx_scan     AS scans,
       pg_size_pretty(pg_relation_size(indexrelid)) AS size
FROM pg_stat_user_indexes
WHERE relname = 'orders'
ORDER BY idx_scan DESC;
```

An index that shows zero scans after a representative period (including month-end jobs) costs write time for nothing and is a candidate to drop. Every partial index is a promise that some queries use exactly that condition, so write the condition down where the queries live.

On write cost, partial indexes help in one direction. A row that never matches the condition is never added to the index. When an update turns a matching row into a non-matching one (an order moves from `failed` to `completed`), the new row version is not inserted into the index, and the old entry is removed later by `VACUUM` like any other dead entry. When a row starts matching, it is inserted.

## When not to bother

A partial index pays off when the matching rows are a small part of the table and the same condition keeps showing up in queries. If the condition matches half of the table, the partial index is half the size of the full one and the planner would probably scan the table anyway. Whether a partial index is worth it comes down to the two sizes from the comparison above and how often the queries run. On a small table a full index is already small.

## Other databases

- **SQL Server**: filtered indexes, `CREATE INDEX ... ON t (col) WHERE status = 'failed'`. The condition is limited to simple comparison logic, so check the documentation for what a filter may contain.
- **SQLite**: partial indexes since 3.8.0, with PostgreSQL's syntax.
- **Oracle**: no partial index syntax. A function-based index that returns `NULL` for the rows you do not want gives the same effect, because a B-tree entry whose keys are all `NULL` is not stored: `CREATE INDEX ... ON orders (CASE WHEN status = 'failed' THEN created_at END)`. Queries have to use that same `CASE` expression.
- **MySQL**: no partial indexes. A generated column and a normal index on it do not give the same savings: MySQL indexes `NULL`s, so every row still has an entry. An index on `(status, created_at)` is the workable substitute.
- **BigQuery**: no B-tree indexes to filter. Partitioning and clustering do the pruning.

For MySQL, the generated-column form is the closest thing and is good for making a condition queryable by name, not for making the index smaller:

```sql norun
ALTER TABLE orders
    ADD COLUMN is_failed BOOLEAN GENERATED ALWAYS AS (status = 'failed') STORED;
CREATE INDEX idx_orders_is_failed ON orders (is_failed, created_at);
```

## In short

- A partial index holds only the rows that match its `WHERE`. For `status = 'failed'` on this table that was 4,800 entries and about 170 times less space than a full index on the same column.
- The planner uses it only when the query's condition implies the index condition. `= 'failed'` with extra filters qualifies, and `IN ('failed','disputed')`, `<> 'completed'` and a bare parameter do not.
- Partial unique indexes enforce rules such as one active subscription per customer. `ON CONFLICT` has to repeat the condition.
- Conditions must be immutable, so there is no `now()`. Rolling windows need a literal that you move forward.
- MySQL has no equivalent, and Oracle gets there with a function-based index that yields `NULL`.
