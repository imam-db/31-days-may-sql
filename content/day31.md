---
day: 31
title: "SQL Is Declarative, and That Has Consequences"
summary: "SQL states what result you want and leaves the how to the planner. What PostgreSQL 18 actually rewrites (checked with EXPLAIN), what it leaves alone, and where wrong estimates or opaque functions take the decision out of its hands."
teaser: "What the PostgreSQL planner rewrites, what it leaves alone, and where it guesses wrong."
---

A query does not say how it should be executed. The same text can run as an index scan today and a sequential scan next month, because the table grew, the statistics changed or the server was upgraded. Two queries that look different can compile to the same plan, and a rewrite you were sure was an optimization can change nothing at all.

All of this follows from SQL being declarative: you describe the rows you want and the planner chooses the algorithm. This last article checks what that means in practice, with `EXPLAIN` output from PostgreSQL 18 for each claim. Other engines make different choices in the details, so the plans are PostgreSQL's.

## The data

```sql
CREATE TABLE customers (
    customer_id  INT PRIMARY KEY,
    name         TEXT NOT NULL,
    region       TEXT NOT NULL
);

CREATE TABLE orders (
    order_id     INT PRIMARY KEY,
    customer_id  INT NOT NULL REFERENCES customers,
    status       TEXT NOT NULL,
    total        NUMERIC(10,2) NOT NULL,
    created_at   DATE NOT NULL
);

CREATE TABLE order_items (
    item_id   INT PRIMARY KEY,
    order_id  INT NOT NULL REFERENCES orders,
    sku       TEXT NOT NULL
);

INSERT INTO customers
SELECT g, 'c' || g, (ARRAY['EU', 'US', 'APAC', 'LATAM'])[1 + g % 4]
FROM generate_series(1, 1000) g;

INSERT INTO orders
SELECT g,
       1 + (g * 7) % 1000,
       CASE WHEN g % 100 = 0 THEN 'cancelled'
            WHEN g % 2 = 0 THEN 'completed'
            ELSE 'pending' END,
       (g % 500) + 0.5,
       DATE '2024-01-01' + (g % 365)
FROM generate_series(1, 20000) g;

INSERT INTO order_items
SELECT g, 1 + (g * 13) % 20000, 'sku' || (g % 50)
FROM generate_series(1, 40000) g;

CREATE INDEX orders_status_idx ON orders (status);
CREATE INDEX orders_created_idx ON orders (created_at);
CREATE INDEX order_items_order_idx ON order_items (order_id);

ANALYZE customers;
ANALYZE orders;
ANALYZE order_items;
```

## The same SQL gets a different plan when the data changes

The planner picks a plan from statistics about the data. A table where one value is rare and another is common shows both choices from the same query shape:

```sql
CREATE TABLE tickets (
    ticket_id  INT PRIMARY KEY,
    state      TEXT NOT NULL,
    payload    TEXT
);

INSERT INTO tickets
SELECT g, CASE WHEN g % 100 = 0 THEN 'rare' ELSE 'common' END, repeat('x', 100)
FROM generate_series(1, 100000) g;

CREATE INDEX tickets_state_idx ON tickets (state);

ANALYZE tickets;

EXPLAIN (COSTS OFF) SELECT * FROM tickets WHERE state = 'rare';
```

```result
                  QUERY PLAN
-----------------------------------------------
 Index Scan using tickets_state_idx on tickets
   Index Cond: (state = 'rare'::text)
(2 rows)
```

```sql
EXPLAIN (COSTS OFF) SELECT * FROM tickets WHERE state = 'common';
```

```result
             QUERY PLAN
------------------------------------
 Seq Scan on tickets
   Filter: (state = 'common'::text)
(2 rows)
```

One percent of the rows through an index, ninety-nine percent with a sequential scan, because reading nearly every page through an index would be slower than reading the table in order. Nobody wrote either plan. If `rare` grows to half the table, the first query changes its plan without anyone touching it, and sometimes that is the "same query, suddenly slower" report.

## Different SQL, same plan

Predicates in a different order, or wrapped in a subquery, mean the same thing, and the planner treats them as the same:

```sql
EXPLAIN (COSTS OFF)
SELECT * FROM orders WHERE status = 'completed' AND total > 100;

EXPLAIN (COSTS OFF)
SELECT * FROM orders WHERE total > 100 AND status = 'completed';

EXPLAIN (COSTS OFF)
SELECT * FROM (SELECT * FROM orders WHERE status = 'completed') s WHERE total > 100;
```

All three print:

```result
                    QUERY PLAN
--------------------------------------------------
 Bitmap Heap Scan on orders
   Recheck Cond: (status = 'completed'::text)
   Filter: (total > '100'::numeric)
   ->  Bitmap Index Scan on orders_status_idx
         Index Cond: (status = 'completed'::text)
(5 rows)
```

The subquery was pulled up into the outer query (flattening), which is why a "pre-filter in a subquery" is not an optimization: the planner would have done the same. The same holds for `IN` against an `OR` chain, which PostgreSQL turns into the same array comparison:

```sql
EXPLAIN (COSTS OFF)
SELECT * FROM customers WHERE customer_id IN (1, 2, 3);

EXPLAIN (COSTS OFF)
SELECT * FROM customers WHERE customer_id = 1 OR customer_id = 2 OR customer_id = 3;
```

```result
                        QUERY PLAN
----------------------------------------------------------
 Index Scan using customers_pkey on customers
   Index Cond: (customer_id = ANY ('{1,2,3}'::integer[]))
(2 rows)
```

Constants are folded at planning time, so `50 + 50` costs nothing per row:

```sql
EXPLAIN (COSTS OFF) SELECT * FROM orders WHERE total > 50 + 50;
```

```result
             QUERY PLAN
------------------------------------
 Seq Scan on orders
   Filter: (total > '100'::numeric)
(2 rows)
```

`now()` is different. It is stable, not constant: the plan keeps the expression and the executor evaluates it once at the start of the query, which is enough to use it as an index condition but is not "folding":

```sql
EXPLAIN (COSTS OFF) SELECT * FROM orders WHERE created_at > now() - interval '1 day';
```

```result
                        QUERY PLAN
----------------------------------------------------------
 Index Scan using orders_created_idx on orders
   Index Cond: (created_at > (now() - '1 day'::interval))
(2 rows)
```

## What the planner pushes down, and what stops it

Filters on a subquery or view are moved into it when that does not change the result. A filter on a grouping column goes below the aggregation; a filter on an aggregate cannot:

```sql
EXPLAIN (COSTS OFF)
SELECT * FROM (SELECT customer_id, sum(total) AS s FROM orders GROUP BY customer_id) x
WHERE customer_id = 5;
```

```result
            QUERY PLAN
-----------------------------------
 GroupAggregate
   ->  Seq Scan on orders
         Filter: (customer_id = 5)
(3 rows)
```

```sql
EXPLAIN (COSTS OFF)
SELECT * FROM (SELECT customer_id, sum(total) AS s FROM orders GROUP BY customer_id) x
WHERE s > 1000;
```

```result
                   QUERY PLAN
-------------------------------------------------
 HashAggregate
   Group Key: orders.customer_id
   Filter: (sum(orders.total) > '1000'::numeric)
   ->  Seq Scan on orders
(4 rows)
```

The first plan has the `customer_id = 5` test on the scan, before any grouping; the second can only filter after the sums exist. Some subqueries cannot be flattened at all because doing so would change the answer. `LIMIT` is the common case: the 100 earliest orders and the orders of customer 5 are different things, so the filter has to stay outside the limit:

```sql
EXPLAIN (COSTS OFF)
SELECT * FROM (SELECT * FROM orders ORDER BY created_at LIMIT 100) x
WHERE customer_id = 5;
```

```result
                        QUERY PLAN
-----------------------------------------------------------
 Subquery Scan on x
   Filter: (x.customer_id = 5)
   ->  Limit
         ->  Index Scan using orders_created_idx on orders
(4 rows)
```

`OFFSET 0` also stops the subquery being flattened. It is an old trick for forcing a fence around a subquery, and it is a good example of the declarative model leaking: the query means the same with or without it, but the plan does not.

Predicates on one table are not reordered by how selective they are. PostgreSQL orders the conditions of a scan by estimated cost, and conditions that cost the same stay in the order you wrote them:

```sql
EXPLAIN (COSTS OFF) SELECT * FROM orders WHERE total > 10 AND status <> 'x';
```

```result
                          QUERY PLAN
---------------------------------------------------------------
 Seq Scan on orders
   Filter: ((total > '10'::numeric) AND (status <> 'x'::text))
(2 rows)
```

```sql
EXPLAIN (COSTS OFF) SELECT * FROM orders WHERE status <> 'x' AND total > 10;
```

```result
                          QUERY PLAN
---------------------------------------------------------------
 Seq Scan on orders
   Filter: ((status <> 'x'::text) AND (total > '10'::numeric))
(2 rows)
```

The two plans differ only in the order of the filter. Put the cheap, selective test first if the order matters, such as before an expensive function call.

## Join order

For inner joins, the order in which you write the tables does not decide the join order. Three tables, written in two different orders, give the same plan:

```sql
EXPLAIN (COSTS OFF)
SELECT * FROM customers c
JOIN orders o ON o.customer_id = c.customer_id
JOIN order_items i ON i.order_id = o.order_id
WHERE c.region = 'EU' AND i.sku = 'sku7';

EXPLAIN (COSTS OFF)
SELECT * FROM order_items i
JOIN orders o ON i.order_id = o.order_id
JOIN customers c ON o.customer_id = c.customer_id
WHERE c.region = 'EU' AND i.sku = 'sku7';
```

```result
                     QUERY PLAN
----------------------------------------------------
 Hash Join
   Hash Cond: (o.order_id = i.order_id)
   ->  Hash Join
         Hash Cond: (o.customer_id = c.customer_id)
         ->  Seq Scan on orders o
         ->  Hash
               ->  Seq Scan on customers c
                     Filter: (region = 'EU'::text)
   ->  Hash
         ->  Seq Scan on order_items i
               Filter: (sku = 'sku7'::text)
(11 rows)
```

The planner searches orderings and keeps the cheapest one. That search is bounded: only the first `join_collapse_limit` tables of a join list are reordered freely, and past `geqo_threshold` tables PostgreSQL switches to a genetic search instead of exhaustive enumeration:

```sql
SELECT name, setting
FROM pg_settings
WHERE name IN ('join_collapse_limit', 'from_collapse_limit', 'geqo_threshold')
ORDER BY name;
```

```result
        name         | setting
---------------------+---------
 from_collapse_limit | 8
 geqo_threshold      | 12
 join_collapse_limit | 8
(3 rows)
```

Setting `join_collapse_limit` to 1 makes the planner keep the join order as written. The same second query then joins `order_items` to `orders` first, exactly as it was typed:

```sql
SET join_collapse_limit = 1;

EXPLAIN (COSTS OFF)
SELECT * FROM order_items i
JOIN orders o ON i.order_id = o.order_id
JOIN customers c ON o.customer_id = c.customer_id
WHERE c.region = 'EU' AND i.sku = 'sku7';

RESET join_collapse_limit;
```

```result
                    QUERY PLAN
--------------------------------------------------
 Hash Join
   Hash Cond: (o.customer_id = c.customer_id)
   ->  Hash Join
         Hash Cond: (o.order_id = i.order_id)
         ->  Seq Scan on orders o
         ->  Hash
               ->  Seq Scan on order_items i
                     Filter: (sku = 'sku7'::text)
   ->  Hash
         ->  Seq Scan on customers c
               Filter: (region = 'EU'::text)
(11 rows)
```

Outer joins restrict reordering, since moving a table across a `LEFT JOIN` can change the result. In a query with many joins, a plan that looks wrong is sometimes the cut-off at work, not a statistics problem.

## CTEs

Since PostgreSQL 12, a non-recursive CTE that is referenced once and has no side effects is inlined like a subquery, so the outer filter reaches the base table:

```sql
EXPLAIN (COSTS OFF)
WITH c AS (SELECT * FROM orders WHERE status = 'cancelled')
SELECT * FROM c WHERE customer_id = 5;
```

```result
                    QUERY PLAN
--------------------------------------------------
 Bitmap Heap Scan on orders
   Recheck Cond: (status = 'cancelled'::text)
   Filter: (customer_id = 5)
   ->  Bitmap Index Scan on orders_status_idx
         Index Cond: (status = 'cancelled'::text)
(5 rows)
```

`MATERIALIZED` turns the CTE into a separate step that is computed on its own and then scanned:

```sql
EXPLAIN (COSTS OFF)
WITH c AS MATERIALIZED (SELECT * FROM orders WHERE status = 'cancelled')
SELECT * FROM c WHERE customer_id = 5;
```

```result
                        QUERY PLAN
----------------------------------------------------------
 CTE Scan on c
   Filter: (customer_id = 5)
   CTE c
     ->  Bitmap Heap Scan on orders
           Recheck Cond: (status = 'cancelled'::text)
           ->  Bitmap Index Scan on orders_status_idx
                 Index Cond: (status = 'cancelled'::text)
(7 rows)
```

A CTE that is referenced twice is materialized by default, which is right when it is expensive and wrong when the outer filters would cut it down. `NOT MATERIALIZED` inlines it into each reference, so the `order_id = 100` filter can use the primary key:

```sql
EXPLAIN (COSTS OFF)
WITH c AS NOT MATERIALIZED (SELECT * FROM orders WHERE status = 'cancelled')
SELECT * FROM c a JOIN c b ON a.customer_id = b.customer_id WHERE a.order_id = 100;
```

```result
                        QUERY PLAN
----------------------------------------------------------
 Hash Join
   Hash Cond: (orders_1.customer_id = orders.customer_id)
   ->  Bitmap Heap Scan on orders orders_1
         Recheck Cond: (status = 'cancelled'::text)
         ->  Bitmap Index Scan on orders_status_idx
               Index Cond: (status = 'cancelled'::text)
   ->  Hash
         ->  Index Scan using orders_pkey on orders
               Index Cond: (order_id = 100)
               Filter: (status = 'cancelled'::text)
(10 rows)
```

## Equivalent results, different plans

Rewriting a query to something equivalent does not always lead to the same plan. "Customers that have an order" can be written three ways. `EXISTS` (and `IN`, which gives the same plan on this data) becomes a join against a de-duplicated copy of `orders`:

```sql
EXPLAIN (COSTS OFF)
SELECT * FROM customers c
WHERE EXISTS (SELECT 1 FROM orders o WHERE o.customer_id = c.customer_id);
```

```result
                  QUERY PLAN
----------------------------------------------
 Hash Join
   Hash Cond: (c.customer_id = o.customer_id)
   ->  Seq Scan on customers c
   ->  Hash
         ->  HashAggregate
               Group Key: o.customer_id
               ->  Seq Scan on orders o
(7 rows)
```

`JOIN` plus `DISTINCT` returns the same customers but joins first and removes duplicates afterwards, over every matching row:

```sql
EXPLAIN (COSTS OFF)
SELECT DISTINCT c.* FROM customers c JOIN orders o ON c.customer_id = o.customer_id;
```

```result
                     QUERY PLAN
----------------------------------------------------
 HashAggregate
   Group Key: c.customer_id, c.name, c.region
   ->  Hash Join
         Hash Cond: (o.customer_id = c.customer_id)
         ->  Seq Scan on orders o
         ->  Hash
               ->  Seq Scan on customers c
(7 rows)
```

A correlated subquery in the select list is not turned into a join at all. It stays a subplan that runs once per customer row:

```sql
EXPLAIN (COSTS OFF)
SELECT c.customer_id,
       (SELECT max(o.total) FROM orders o WHERE o.customer_id = c.customer_id) AS biggest
FROM customers c;
```

```result
                      QUERY PLAN
-------------------------------------------------------
 Seq Scan on customers c
   SubPlan 1
     ->  Aggregate
           ->  Seq Scan on orders o
                 Filter: (customer_id = c.customer_id)
(5 rows)
```

The planner can only rewrite what it can prove to be equivalent. `NOT IN` and `NOT EXISTS` look interchangeable and are not once a `NULL` is involved (Day 22), so PostgreSQL never converts one into the other.

## Constraints are information

The planner uses what the schema declares. A `NOT NULL` column makes `IS NOT NULL` redundant and `IS NULL` impossible:

```sql
EXPLAIN (COSTS OFF) SELECT * FROM orders WHERE status IS NOT NULL;
```

```result
     QUERY PLAN
--------------------
 Seq Scan on orders
(1 row)
```

```sql
EXPLAIN (COSTS OFF) SELECT * FROM orders WHERE status IS NULL;
```

```result
        QUERY PLAN
--------------------------
 Result
   One-Time Filter: false
(2 rows)
```

A `LEFT JOIN` to a table through its primary key can add no rows and, when no column of that table is used, the join is dropped:

```sql
EXPLAIN (COSTS OFF)
SELECT o.order_id FROM orders o LEFT JOIN customers c ON c.customer_id = o.customer_id;
```

```result
      QUERY PLAN
----------------------
 Seq Scan on orders o
(1 row)
```

This is a place where it helps to check rather than assume. A foreign key does not allow the same removal for an inner join in PostgreSQL, so the same query with `JOIN` still joins `customers`:

```sql
EXPLAIN (COSTS OFF)
SELECT o.order_id FROM orders o JOIN customers c ON c.customer_id = o.customer_id;
```

```result
                  QUERY PLAN
----------------------------------------------
 Hash Join
   Hash Cond: (o.customer_id = c.customer_id)
   ->  Seq Scan on orders o
   ->  Hash
         ->  Seq Scan on customers c
(5 rows)
```

Dropping a unique index or a `NOT NULL` can therefore change plans, not just data validation.

## Combining indexes, and new capabilities

Two single-column indexes can answer a two-column condition. The planner builds a bitmap from each and intersects them:

```sql
CREATE TABLE people (
    person_id  INT PRIMARY KEY,
    city       INT NOT NULL,
    age        INT NOT NULL,
    bio        TEXT
);

INSERT INTO people
SELECT g, g % 200, g % 90, repeat('x', 50) FROM generate_series(1, 100000) g;

CREATE INDEX people_city_idx ON people (city);
CREATE INDEX people_age_idx ON people (age);

ANALYZE people;

EXPLAIN (COSTS OFF) SELECT * FROM people WHERE city = 7 AND age = 30;
```

```result
                    QUERY PLAN
--------------------------------------------------
 Bitmap Heap Scan on people
   Recheck Cond: ((city = 7) AND (age = 30))
   ->  BitmapAnd
         ->  Bitmap Index Scan on people_city_idx
               Index Cond: (city = 7)
         ->  Bitmap Index Scan on people_age_idx
               Index Cond: (age = 30)
(7 rows)
```

An upgrade can also change a plan with no change to the query. PostgreSQL 18 can use a composite b-tree index when the query skips its leading column, by probing each distinct value of that column ("skip scan"). With an index on `(sensor, ts)` and five sensors, a search on `ts` alone needs six index searches (five probes and one more):

```sql
CREATE TABLE readings (
    sensor  INT NOT NULL,
    ts      INT NOT NULL,
    v       INT
) WITH (autovacuum_enabled = off);

INSERT INTO readings SELECT g % 5, g, g FROM generate_series(1, 100000) g;

CREATE INDEX readings_sensor_ts_idx ON readings (sensor, ts);

ANALYZE readings;

EXPLAIN (ANALYZE, COSTS OFF, TIMING OFF, SUMMARY OFF, BUFFERS OFF)
SELECT sensor, ts FROM readings WHERE ts = 500;
```

```result
                                     QUERY PLAN
-------------------------------------------------------------------------------------
 Index Only Scan using readings_sensor_ts_idx on readings (actual rows=1.00 loops=1)
   Index Cond: (ts = 500)
   Heap Fetches: 1
   Index Searches: 6
(4 rows)
```

Earlier versions could not do this; they would have scanned the whole index or the table. It is the declarative model working in your favour: the same SQL, a better plan.

## Where the plan goes wrong

Plans come from estimates, and estimates come from per-column statistics. The planner assumes columns are independent. When they are not, the combined estimate is far off. In `sales`, every country has exactly one currency:

```sql
CREATE TABLE sales (
    sale_id   INT PRIMARY KEY,
    country   TEXT NOT NULL,
    currency  TEXT NOT NULL
);

INSERT INTO sales
SELECT g,
       (ARRAY['JP', 'US', 'DE', 'BR'])[1 + g % 4],
       (ARRAY['JPY', 'USD', 'EUR', 'BRL'])[1 + g % 4]
FROM generate_series(1, 10000) g;

ANALYZE sales;

EXPLAIN (ANALYZE, TIMING OFF, SUMMARY OFF, BUFFERS OFF)
SELECT * FROM sales WHERE country = 'JP' AND currency = 'JPY';
```

```result
                                       QUERY PLAN
----------------------------------------------------------------------------------------
 Seq Scan on sales  (cost=0.00..205.00 rows=625 width=11) (actual rows=2500.00 loops=1)
   Filter: ((country = 'JP'::text) AND (currency = 'JPY'::text))
   Rows Removed by Filter: 7500
(3 rows)
```

A quarter of the rows are `JP` and a quarter are `JPY`, so under independence about one sixteenth match: the estimate is 625 and the real number is 2,500. Telling the planner about the dependency fixes the estimate:

```sql
CREATE STATISTICS sales_dep (dependencies) ON country, currency FROM sales;

ANALYZE sales;

EXPLAIN (ANALYZE, TIMING OFF, SUMMARY OFF, BUFFERS OFF)
SELECT * FROM sales WHERE country = 'JP' AND currency = 'JPY';
```

```result
                                       QUERY PLAN
-----------------------------------------------------------------------------------------
 Seq Scan on sales  (cost=0.00..205.00 rows=2500 width=11) (actual rows=2500.00 loops=1)
   Filter: ((country = 'JP'::text) AND (currency = 'JPY'::text))
   Rows Removed by Filter: 7500
(3 rows)
```

The plan did not change here because a sequential scan was right either way. The estimate matters once other choices depend on it, such as the join order above a filter like this one. Comparing `rows=` with `actual rows=` in `EXPLAIN ANALYZE` shows where the planner's model and the data disagree.

Prepared statements are another source. After a few executions PostgreSQL may switch to a generic plan that does not look at the parameter value. With a very skewed column that plan can suit one value and not the other:

```sql
CREATE TABLE jobs (
    job_id  INT PRIMARY KEY,
    state   TEXT NOT NULL
);

INSERT INTO jobs
SELECT g, CASE WHEN g % 1000 = 0 THEN 'failed' ELSE 'done' END
FROM generate_series(1, 100000) g;

CREATE INDEX jobs_state_idx ON jobs (state);

ANALYZE jobs;

PREPARE jobs_in_state(TEXT) AS SELECT * FROM jobs WHERE state = $1;

SET plan_cache_mode = force_generic_plan;

EXPLAIN (COSTS OFF) EXECUTE jobs_in_state('done');
```

```result
               QUERY PLAN
-----------------------------------------
 Index Scan using jobs_state_idx on jobs
   Index Cond: (state = $1)
(2 rows)
```

```sql
SET plan_cache_mode = force_custom_plan;

EXPLAIN (COSTS OFF) EXECUTE jobs_in_state('done');

RESET plan_cache_mode;
```

```result
            QUERY PLAN
----------------------------------
 Seq Scan on jobs
   Filter: (state = 'done'::text)
(2 rows)
```

With the generic plan, a query for the 99,900 `done` rows goes through an index. With the actual value known, the planner chooses a sequential scan. `plan_cache_mode` is a debugging switch and a setting you can use per session, as shown. The usual fix is accurate statistics, or `force_custom_plan` for the statements that need it.

## What the planner cannot see into

A plain SQL function can be inlined: its body is substituted into the query and then optimized with it. A PL/pgSQL function is a black box that the planner can only call:

```sql
CREATE FUNCTION with_tax_sql(n NUMERIC) RETURNS NUMERIC
LANGUAGE sql IMMUTABLE AS 'SELECT n * 1.2';

CREATE FUNCTION with_tax_plpgsql(n NUMERIC) RETURNS NUMERIC
LANGUAGE plpgsql IMMUTABLE AS $$ BEGIN RETURN n * 1.2; END $$;

EXPLAIN (VERBOSE, COSTS OFF) SELECT with_tax_sql(total) FROM orders;
```

```result
        QUERY PLAN
---------------------------
 Seq Scan on public.orders
   Output: (total * 1.2)
(2 rows)
```

```sql
EXPLAIN (VERBOSE, COSTS OFF) SELECT with_tax_plpgsql(total) FROM orders;
```

```result
            QUERY PLAN
-----------------------------------
 Seq Scan on public.orders
   Output: with_tax_plpgsql(total)
(2 rows)
```

In the second plan the planner has no idea what the function does, so it cannot use an index on `total * 1.2`, estimate its selectivity or move it around. Row-by-row procedural code, cursors and trigger side effects work the same way: they are outside the declarative model, and the planner optimizes around them instead of through them.

When the planner chooses badly, the usual levers are statistics, constraints, indexes and cost settings such as `random_page_cost`. PostgreSQL has no query hints in core; the `pg_hint_plan` extension provides them if you really need to force a plan.

## In short

- Describe what you want first and look at the plan second. Reordering predicates, wrapping a filter in a subquery and `IN` versus `OR` change nothing, and `EXPLAIN` shows it.
- Check an optimization by comparing plans, not by trusting the rewrite. Some rewrites that look equivalent produce different plans, and some that look different produce the same one.
- When a plan is wrong, look at `rows=` against `actual rows=` first. The usual causes are stale statistics, correlated columns and generic plans.
- Declare constraints and keep functions inlinable. Both give the planner more to work with.

That is the last of the 31 days.
