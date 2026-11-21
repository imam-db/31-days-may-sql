---
day: 24
title: "The Cost of Correlated Subqueries"
summary: "A correlated scalar subquery in the SELECT list runs once per outer row, and PostgreSQL does not turn it into a join. When that is fine, when it is not, and the grouped-join, LATERAL and window-function rewrites."
teaser: "A correlated subquery runs once per outer row; PostgreSQL keeps it that way. When to rewrite."
---

A customer report with a few scalar subqueries is easy to write and easy to read:

```sql norun
SELECT
    c.customer_id,
    c.name,
    (SELECT count(*)        FROM orders o WHERE o.customer_id = c.customer_id) AS order_count,
    (SELECT max(created_at) FROM orders o WHERE o.customer_id = c.customer_id) AS last_order,
    (SELECT sum(total)      FROM orders o WHERE o.customer_id = c.customer_id) AS lifetime_revenue
FROM customers c
WHERE c.signup_date > '2024-01-01';
```

Each subquery refers to `c.customer_id` from the outer query, which makes it correlated. A correlated subquery cannot be evaluated once and reused, because its result depends on the outer row. This query has three of them, so every customer that passes the `WHERE` triggers three separate lookups in `orders`.

This article runs on PostgreSQL 18, and what it says about the planner is about PostgreSQL. SQL Server and Oracle have optimizers that unnest many correlated subqueries into joins on their own. PostgreSQL does not do that for scalar subqueries, as the plans below show.

## The data

100,000 customers, a million orders (ten per customer on average), and 30,000 support tickets used later in the article:

```sql
CREATE TABLE customers (
    customer_id  INT PRIMARY KEY,
    name         TEXT NOT NULL,
    signup_date  DATE NOT NULL
);

CREATE TABLE orders (
    order_id     INT PRIMARY KEY,
    customer_id  INT NOT NULL REFERENCES customers (customer_id),
    total        NUMERIC(10,2) NOT NULL,
    status       TEXT NOT NULL,
    created_at   TIMESTAMP NOT NULL
);

CREATE TABLE tickets (
    ticket_id    INT PRIMARY KEY,
    customer_id  INT NOT NULL REFERENCES customers (customer_id),
    status       TEXT NOT NULL
);

INSERT INTO customers
SELECT g, 'customer ' || g, DATE '2020-01-01' + (g % 2000)
FROM generate_series(1, 100000) g;

INSERT INTO orders
SELECT g,
       1 + (g::bigint * 7919) % 100000,
       ((g::bigint * 7919) % 9973) / 100.0,
       CASE WHEN g % 97 = 0 THEN 'disputed'
            ELSE (ARRAY['paid','paid','paid','pending','refunded'])[1 + (g + g / 7) % 5]
       END,
       TIMESTAMP '2022-01-01' + (g % 1500) * INTERVAL '1 day'
FROM generate_series(1, 1000000) g;

INSERT INTO tickets
SELECT g,
       1 + (g::bigint * 31) % 100000,
       (ARRAY['open','closed','closed','closed'])[1 + g % 4]
FROM generate_series(1, 30000) g;

CREATE INDEX idx_orders_customer  ON orders  (customer_id);
CREATE INDEX idx_tickets_customer ON tickets (customer_id);

VACUUM ANALYZE customers;
VACUUM ANALYZE orders;
VACUUM ANALYZE tickets;
```

26,900 of the customers signed up after 2024-01-01. The report query goes into a view so the variants below can be compared against each other:

```sql
CREATE VIEW stats_subplans AS
SELECT
    c.customer_id,
    c.name,
    c.signup_date,
    (SELECT count(*)        FROM orders o WHERE o.customer_id = c.customer_id) AS order_count,
    (SELECT max(created_at) FROM orders o WHERE o.customer_id = c.customer_id) AS last_order,
    (SELECT sum(total)      FROM orders o WHERE o.customer_id = c.customer_id) AS lifetime_revenue
FROM customers c;
```

## What the plan shows

```sql
EXPLAIN (COSTS OFF)
SELECT * FROM stats_subplans WHERE signup_date > '2024-01-01';
```

```result
                             QUERY PLAN
---------------------------------------------------------------------
 Seq Scan on customers c
   Filter: (signup_date > '2024-01-01'::date)
   SubPlan 1
     ->  Aggregate
           ->  Index Only Scan using idx_orders_customer on orders o
                 Index Cond: (customer_id = c.customer_id)
   SubPlan 2
     ->  Aggregate
           ->  Bitmap Heap Scan on orders o_1
                 Recheck Cond: (customer_id = c.customer_id)
                 ->  Bitmap Index Scan on idx_orders_customer
                       Index Cond: (customer_id = c.customer_id)
   SubPlan 3
     ->  Aggregate
           ->  Bitmap Heap Scan on orders o_2
                 Recheck Cond: (customer_id = c.customer_id)
                 ->  Bitmap Index Scan on idx_orders_customer
                       Index Cond: (customer_id = c.customer_id)
(18 rows)
```

There is one scan of `customers` and three `SubPlan` nodes underneath it. Each subplan is a complete small query, planned once and executed once per qualifying customer. `EXPLAIN ANALYZE` on a single subquery makes the multiplication visible in the `loops` counter:

```sql
EXPLAIN (ANALYZE, COSTS OFF, TIMING OFF, SUMMARY OFF, BUFFERS OFF)
SELECT c.customer_id,
       (SELECT count(*) FROM orders o WHERE o.customer_id = c.customer_id) AS order_count
FROM customers c
WHERE c.signup_date > '2024-01-01';
```

```result
                                             QUERY PLAN
-----------------------------------------------------------------------------------------------------
 Seq Scan on customers c (actual rows=26900.00 loops=1)
   Filter: (signup_date > '2024-01-01'::date)
   Rows Removed by Filter: 73100
   SubPlan 1
     ->  Aggregate (actual rows=1.00 loops=26900)
           ->  Index Only Scan using idx_orders_customer on orders o (actual rows=10.00 loops=26900)
                 Index Cond: (customer_id = c.customer_id)
                 Heap Fetches: 0
                 Index Searches: 26900
(9 rows)
```

`loops=26900`: the aggregate ran 26,900 times, once per customer, and each run did one index search. With the index on `orders (customer_id)` that is cheap per customer, and for 26,900 customers it is still thousands of small index probes rather than one big scan. Whether that is a problem depends on how many outer rows reach the subquery and how expensive one probe is.

### Without the index

The expensive version is the one where the correlation column has no index. A copy of `orders` without one:

```sql
CREATE TABLE orders_noidx AS SELECT * FROM orders;
ANALYZE orders_noidx;

EXPLAIN (COSTS OFF)
SELECT c.customer_id,
       (SELECT count(*) FROM orders_noidx o WHERE o.customer_id = c.customer_id) AS order_count
FROM customers c
WHERE c.signup_date > '2024-01-01';
```

```result
                      QUERY PLAN
-------------------------------------------------------
 Seq Scan on customers c
   Filter: (signup_date > '2024-01-01'::date)
   SubPlan 1
     ->  Aggregate
           ->  Seq Scan on orders_noidx o
                 Filter: (customer_id = c.customer_id)
(6 rows)
```

Now each of the 26,900 executions scans the whole million-row table, around 27 billion row visits in total. I did not run it. This is the shape behind reports of a query that "was fine in development and never finished in production": the plan is the same and the outer table grew. Check that the correlated column is indexed before anything else.

## Rewrite 1: aggregate once, then join

The standard fix is to aggregate `orders` once and join the result:

```sql
CREATE VIEW stats_join AS
SELECT
    c.customer_id,
    c.name,
    c.signup_date,
    COALESCE(o.order_count, 0) AS order_count,
    o.last_order,
    o.lifetime_revenue
FROM customers c
LEFT JOIN (
    SELECT customer_id,
           count(*)        AS order_count,
           max(created_at) AS last_order,
           sum(total)      AS lifetime_revenue
    FROM orders
    GROUP BY customer_id
) o ON o.customer_id = c.customer_id;
```

The two forms differ for customers without orders. A scalar `count(*)` returns 0 there, but through a `LEFT JOIN` the column would be `NULL`, hence the `COALESCE` on the count. `sum` and `max` are `NULL` in both forms, so they get no `COALESCE`: wrapping the revenue in `COALESCE(..., 0)` would change the output of the report. In this data every customer has ten orders, so the comparison further down cannot show the difference, but it exists.

```sql
EXPLAIN (COSTS OFF)
SELECT * FROM stats_join WHERE signup_date > '2024-01-01';
```

```result
                         QUERY PLAN
------------------------------------------------------------
 Hash Right Join
   Hash Cond: (orders.customer_id = c.customer_id)
   ->  GroupAggregate
         Group Key: orders.customer_id
         ->  Index Scan using idx_orders_customer on orders
   ->  Hash
         ->  Seq Scan on customers c
               Filter: (signup_date > '2024-01-01'::date)
(8 rows)
```

`orders` is aggregated once, in a single pass over the `customer_id` index, and joined to the customers that passed the filter. No subplans, no per-customer probes.

That does not make it faster in every case. The grouped subquery cannot see the customer filter: it aggregates all million orders even when only a few customers are wanted. Take the customers who signed up on a single day, 50 of them:

```sql
EXPLAIN (COSTS OFF)
SELECT * FROM stats_join WHERE signup_date = '2024-01-01';
```

```result
                         QUERY PLAN
------------------------------------------------------------
 Merge Left Join
   Merge Cond: (c.customer_id = orders.customer_id)
   ->  Sort
         Sort Key: c.customer_id
         ->  Seq Scan on customers c
               Filter: (signup_date = '2024-01-01'::date)
   ->  GroupAggregate
         Group Key: orders.customer_id
         ->  Index Scan using idx_orders_customer on orders
(9 rows)
```

The plan still aggregates the whole `orders` table, while the three-subquery version does 150 index probes for the same 50 customers:

```sql
EXPLAIN (COSTS OFF)
SELECT * FROM stats_subplans WHERE signup_date = '2024-01-01';
```

```result
                             QUERY PLAN
---------------------------------------------------------------------
 Seq Scan on customers c
   Filter: (signup_date = '2024-01-01'::date)
   SubPlan 1
     ->  Aggregate
           ->  Index Only Scan using idx_orders_customer on orders o
                 Index Cond: (customer_id = c.customer_id)
   SubPlan 2
     ->  Aggregate
           ->  Bitmap Heap Scan on orders o_1
                 Recheck Cond: (customer_id = c.customer_id)
                 ->  Bitmap Index Scan on idx_orders_customer
                       Index Cond: (customer_id = c.customer_id)
   SubPlan 3
     ->  Aggregate
           ->  Bitmap Heap Scan on orders o_2
                 Recheck Cond: (customer_id = c.customer_id)
                 ->  Bitmap Index Scan on idx_orders_customer
                       Index Cond: (customer_id = c.customer_id)
(18 rows)
```

A filter on the join column itself is different. PostgreSQL knows `c.customer_id = 5` implies `orders.customer_id = 5`, and pushes it into the grouped subquery:

```sql
EXPLAIN (COSTS OFF)
SELECT * FROM stats_join WHERE customer_id = 5;
```

```result
                         QUERY PLAN
------------------------------------------------------------
 Nested Loop Left Join
   ->  Index Scan using customers_pkey on customers c
         Index Cond: (customer_id = 5)
   ->  GroupAggregate
         ->  Bitmap Heap Scan on orders
               Recheck Cond: (customer_id = 5)
               ->  Bitmap Index Scan on idx_orders_customer
                     Index Cond: (customer_id = 5)
(8 rows)
```

So the rule is about the number of outer rows. A few outer rows and an index on the correlated column favor the subqueries, or a `LATERAL` join. Most of the outer table and a large `orders` favor one pass and a hash aggregate. In my own runs on a laptop with this data, the three-subquery version, the grouped join and the `LATERAL` version below all finished over the full customer table within the same few seconds, so the gap was much smaller than headline speedups suggest. It grows with the number of probes that miss the cache and with the number of subqueries.

## Rewrite 2: LATERAL, one probe for three columns

If you keep the per-customer lookup but want one probe instead of three, put the three aggregates in one `LATERAL` subquery:

```sql
CREATE VIEW stats_lateral AS
SELECT
    c.customer_id,
    c.name,
    c.signup_date,
    o.order_count,
    o.last_order,
    o.lifetime_revenue
FROM customers c
LEFT JOIN LATERAL (
    SELECT count(*)        AS order_count,
           max(created_at) AS last_order,
           sum(total)      AS lifetime_revenue
    FROM orders
    WHERE customer_id = c.customer_id
) o ON TRUE;

EXPLAIN (COSTS OFF)
SELECT * FROM stats_lateral WHERE signup_date > '2024-01-01';
```

```result
                          QUERY PLAN
---------------------------------------------------------------
 Nested Loop Left Join
   ->  Seq Scan on customers c
         Filter: (signup_date > '2024-01-01'::date)
   ->  Aggregate
         ->  Bitmap Heap Scan on orders
               Recheck Cond: (customer_id = c.customer_id)
               ->  Bitmap Index Scan on idx_orders_customer
                     Index Cond: (customer_id = c.customer_id)
(8 rows)
```

It is still a nested loop with one index lookup per customer, but orders are fetched once per customer and all three aggregates are computed from the same rows. An aggregate without `GROUP BY` always returns exactly one row, even for a customer with no orders, so there is no `COALESCE` to forget and `LEFT JOIN ... ON TRUE` behaves like the scalar subqueries. `LATERAL` exists in PostgreSQL (9.3 and later), MySQL (8.0.14 and later) and Oracle (12c and later); SQL Server writes the same idea as `CROSS APPLY` or `OUTER APPLY`.

All three views return the same rows. `EXCEPT` treats two `NULL`s as equal, so comparing in both directions shows any difference:

```sql
SELECT count(*) AS differing_rows
FROM (
    (SELECT * FROM stats_subplans EXCEPT SELECT * FROM stats_join)
    UNION ALL
    (SELECT * FROM stats_join EXCEPT SELECT * FROM stats_subplans)
    UNION ALL
    (SELECT * FROM stats_subplans EXCEPT SELECT * FROM stats_lateral)
    UNION ALL
    (SELECT * FROM stats_lateral EXCEPT SELECT * FROM stats_subplans)
) d;
```

```result
 differing_rows
----------------
              0
(1 row)
```

## What PostgreSQL does turn into a join

`EXISTS` and `IN` with a subquery are different from scalar subqueries. PostgreSQL pulls them up into a semi-join (and `NOT EXISTS` into an anti-join, see Day 22), which can start from either table:

```sql
EXPLAIN (COSTS OFF)
SELECT c.customer_id
FROM customers c
WHERE c.signup_date > '2024-01-01'
  AND EXISTS (SELECT 1 FROM orders o WHERE o.customer_id = c.customer_id);
```

```result
                         QUERY PLAN
-------------------------------------------------------------
 Nested Loop Semi Join
   ->  Seq Scan on customers c
         Filter: (signup_date > '2024-01-01'::date)
   ->  Index Only Scan using idx_orders_customer on orders o
         Index Cond: (customer_id = c.customer_id)
(5 rows)
```

There is no `SubPlan` here and no per-row restart. A correlated `EXISTS` is usually fine as written. A correlated scalar aggregate in the `SELECT` list or in a comparison is the one that stays a subplan.

## Comparing a row with its group: window functions

The same applies in `WHERE`. "Orders larger than the customer's average order" is natural to write as a correlated subquery:

```sql
EXPLAIN (COSTS OFF)
SELECT *
FROM orders o
WHERE total > (SELECT avg(total) FROM orders WHERE customer_id = o.customer_id);
```

```result
                           QUERY PLAN
-----------------------------------------------------------------
 Seq Scan on orders o
   Filter: (total > (SubPlan 1))
   SubPlan 1
     ->  Aggregate
           ->  Bitmap Heap Scan on orders
                 Recheck Cond: (customer_id = o.customer_id)
                 ->  Bitmap Index Scan on idx_orders_customer
                       Index Cond: (customer_id = o.customer_id)
(8 rows)
```

That is one index probe per order, a million of them for the whole table. A window function computes every customer's average in one pass:

```sql
EXPLAIN (COSTS OFF)
SELECT *
FROM (
    SELECT *, avg(total) OVER (PARTITION BY customer_id) AS customer_avg
    FROM orders
) t
WHERE total > customer_avg;
```

```result
                         QUERY PLAN
------------------------------------------------------------
 Subquery Scan on t
   Filter: (t.total > t.customer_avg)
   ->  WindowAgg
         Window: w1 AS (PARTITION BY orders.customer_id)
         ->  Index Scan using idx_orders_customer on orders
(5 rows)
```

Both give the same rows. A check on the first 100 customers, so the correlated version finishes quickly:

```sql
SELECT count(*) AS above_avg_correlated
FROM orders o
WHERE o.customer_id <= 100
  AND total > (SELECT avg(total) FROM orders WHERE customer_id = o.customer_id);
```

```result
 above_avg_correlated
----------------------
                  500
(1 row)
```

```sql
SELECT count(*) AS above_avg_window
FROM (
    SELECT *, avg(total) OVER (PARTITION BY customer_id) AS customer_avg
    FROM orders
) t
WHERE customer_id <= 100
  AND total > customer_avg;
```

```result
 above_avg_window
------------------
              500
(1 row)
```

Filtering on the partition column outside the subquery works because PostgreSQL pushes that condition below the window function. A filter on any other column cannot be pushed down that way, since it would change which rows each average is computed from. A join against a grouped `avg(total)` is the third way to write it and is just as good.

## EXISTS combined with OR

Two `EXISTS` conditions joined by `OR` cannot become two semi-joins, because a row that passes through either one has to stay. PostgreSQL still has a way out: it can turn each `EXISTS` into a hashed subplan, built once for the whole query.

```sql
EXPLAIN (COSTS OFF)
SELECT *
FROM customers c
WHERE EXISTS (SELECT 1 FROM orders  o WHERE o.customer_id = c.customer_id AND o.status = 'disputed')
   OR EXISTS (SELECT 1 FROM tickets t WHERE t.customer_id = c.customer_id AND t.status = 'open');
```

```result
                                                 QUERY PLAN
------------------------------------------------------------------------------------------------------------
 Seq Scan on customers c
   Filter: ((ANY (customer_id = (hashed SubPlan 2).col1)) OR (ANY (customer_id = (hashed SubPlan 4).col1)))
   SubPlan 2
     ->  Gather
           Workers Planned: 2
           ->  Parallel Seq Scan on orders o
                 Filter: (status = 'disputed'::text)
   SubPlan 4
     ->  Seq Scan on tickets t
           Filter: (status = 'open'::text)
(10 rows)
```

Both `SubPlan` nodes are marked `hashed`, so each table is scanned once and probed from a hash table. That is not per-row evaluation. It does mean the plan scans all disputed orders and all open tickets however few customers you ask for, and the plan can turn into a true per-row subplan when the hash would not fit in `work_mem` or the planner expects very few outer rows. Writing the `UNION` version makes the choice explicit:

```sql
EXPLAIN (COSTS OFF)
SELECT *
FROM customers c
WHERE c.customer_id IN (
    SELECT customer_id FROM orders  WHERE status = 'disputed'
    UNION
    SELECT customer_id FROM tickets WHERE status = 'open'
);
```

```result
                             QUERY PLAN
---------------------------------------------------------------------
 Hash Join
   Hash Cond: (c.customer_id = orders.customer_id)
   ->  Seq Scan on customers c
   ->  Hash
         ->  HashAggregate
               Group Key: orders.customer_id
               ->  Append
                     ->  Gather
                           Workers Planned: 2
                           ->  Parallel Seq Scan on orders
                                 Filter: (status = 'disputed'::text)
                     ->  Seq Scan on tickets
                           Filter: (status = 'open'::text)
(13 rows)
```

The two forms return the same customers:

```sql
SELECT count(*) AS with_exists_or
FROM customers c
WHERE EXISTS (SELECT 1 FROM orders  o WHERE o.customer_id = c.customer_id AND o.status = 'disputed')
   OR EXISTS (SELECT 1 FROM tickets t WHERE t.customer_id = c.customer_id AND t.status = 'open');
```

```result
 with_exists_or
----------------
          17034
(1 row)
```

```sql
SELECT count(*) AS with_union
FROM customers c
WHERE c.customer_id IN (
    SELECT customer_id FROM orders  WHERE status = 'disputed'
    UNION
    SELECT customer_id FROM tickets WHERE status = 'open'
);
```

```result
 with_union
------------
      17034
(1 row)
```

I would not call the `UNION` form better here. Both scan each table once. It is the form to try when `EXPLAIN` shows the first plan degrading into a per-row subplan.

## Finding these in a real database

Look for the pattern in two places. In a plan, a `SubPlan` node (not marked `hashed`) under a scan or filter, with `loops` far above 1 on `EXPLAIN (ANALYZE)`, is a per-row subquery. Across the workload, `pg_stat_statements` shows which statements spend the time:

```sql norun
SELECT calls,
       round(total_exec_time) AS total_ms,
       round(mean_exec_time)  AS mean_ms,
       rows,
       left(query, 80)        AS query
FROM pg_stat_statements
ORDER BY total_exec_time DESC
LIMIT 10;
```

Take the top statements, run `EXPLAIN (ANALYZE)` on a production-sized copy, and look at the loop counts of the subplans.

## In short

- A correlated scalar subquery in `SELECT` or `WHERE` is executed once per outer row in PostgreSQL. `EXPLAIN` shows it as `SubPlan`, and `loops` on `EXPLAIN ANALYZE` shows how many times it ran.
- Cost is the number of outer rows times the cost of one probe. An index on the correlated column keeps the probe cheap; without it each probe scans the table.
- For a few outer rows with an index, leave the subqueries alone. If several subqueries read the same table, merge them into one `LATERAL` subquery.
- When most of the outer table is needed, aggregate once and join. Remember that `LEFT JOIN` gives `NULL` where a scalar `count(*)` gave 0.
- To compare rows with a group value, use a window function or a grouped join.
- `EXISTS` and `IN` are handled as semi-joins and need no rewrite.
