---
day: 26
title: "LATERAL JOIN: The Context-Aware Subquery"
summary: "LATERAL lets a subquery in FROM refer to the tables before it. Top-N per group, several aggregates from one lookup, row-expanding functions, and how it compares with window functions in PostgreSQL 18."
teaser: "Top-N per group and several aggregates from one lookup, compared with window functions."
---

"Show the three most recent orders for each customer" is awkward with the tools a plain `JOIN` gives you. A scalar subquery returns one value, so it cannot return three rows. A window function can do it, but it ranks every order first. A subquery in `FROM` would be the natural fit, except that it cannot see the customer it is supposed to belong to:

```sql norun
SELECT *
FROM customers c,
     (SELECT * FROM orders WHERE customer_id = c.customer_id) o;
-- ERROR:  invalid reference to FROM-clause entry for table "c"
-- HINT:  To reference that table, you must mark this subquery with LATERAL.
```

`LATERAL` lifts that restriction. A `LATERAL` subquery is evaluated for each row of the tables listed before it and may use their columns. The outer row acts as the parameter of a small inner query that can return any number of rows and columns.

The examples run on PostgreSQL 18. `LATERAL` is part of standard SQL; the last section lists what other databases call it.

## The data

Four customers and eight orders. Diana has none, and Charlie's newest order is still pending:

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

CREATE INDEX idx_orders_customer_created ON orders (customer_id, created_at DESC);

INSERT INTO customers VALUES
    (1, 'Alice',   '2023-05-15'),
    (2, 'Bob',     '2024-01-20'),
    (3, 'Charlie', '2023-11-10'),
    (4, 'Diana',   '2024-03-05');

INSERT INTO orders VALUES
    (101, 1,  99.00, 'completed', '2024-05-01'),
    (102, 1, 149.50, 'completed', '2024-05-15'),
    (103, 1,  79.00, 'completed', '2024-06-02'),
    (104, 1, 220.00, 'completed', '2024-06-20'),
    (105, 2,  50.00, 'completed', '2024-04-10'),
    (106, 2,  75.00, 'completed', '2024-05-20'),
    (107, 3, 999.00, 'completed', '2024-06-01'),
    (108, 3,  50.00, 'pending',   '2024-06-10');
```

## Top-N per group

With a window function, rank each customer's orders and keep the first three:

```sql
SELECT customer_id, order_id, total, created_at
FROM (
    SELECT o.*,
           row_number() OVER (PARTITION BY customer_id ORDER BY created_at DESC) AS rn
    FROM orders o
) ranked
WHERE rn <= 3
ORDER BY customer_id, created_at DESC;
```

```result
 customer_id | order_id | total  |     created_at
-------------+----------+--------+---------------------
           1 |      104 | 220.00 | 2024-06-20 00:00:00
           1 |      103 |  79.00 | 2024-06-02 00:00:00
           1 |      102 | 149.50 | 2024-05-15 00:00:00
           2 |      106 |  75.00 | 2024-05-20 00:00:00
           2 |      105 |  50.00 | 2024-04-10 00:00:00
           3 |      108 |  50.00 | 2024-06-10 00:00:00
           3 |      107 | 999.00 | 2024-06-01 00:00:00
(7 rows)
```

The `LATERAL` version asks the question the way you would say it: for each customer, take that customer's orders, newest first, and stop after three.

```sql
SELECT c.customer_id, c.name, o.order_id, o.total, o.created_at
FROM customers c
CROSS JOIN LATERAL (
    SELECT order_id, total, created_at
    FROM orders
    WHERE customer_id = c.customer_id
    ORDER BY created_at DESC
    LIMIT 3
) o
ORDER BY c.customer_id, o.created_at DESC;
```

```result
 customer_id |  name   | order_id | total  |     created_at
-------------+---------+----------+--------+---------------------
           1 | Alice   |      104 | 220.00 | 2024-06-20 00:00:00
           1 | Alice   |      103 |  79.00 | 2024-06-02 00:00:00
           1 | Alice   |      102 | 149.50 | 2024-05-15 00:00:00
           2 | Bob     |      106 |  75.00 | 2024-05-20 00:00:00
           2 | Bob     |      105 |  50.00 | 2024-04-10 00:00:00
           3 | Charlie |      108 |  50.00 | 2024-06-10 00:00:00
           3 | Charlie |      107 | 999.00 | 2024-06-01 00:00:00
(7 rows)
```

Diana is missing: `CROSS JOIN LATERAL` drops an outer row when the subquery returns nothing. `LEFT JOIN LATERAL ... ON TRUE` keeps her, with `NULL`s in the order columns:

```sql
SELECT c.customer_id, c.name, o.order_id, o.total, o.created_at
FROM customers c
LEFT JOIN LATERAL (
    SELECT order_id, total, created_at
    FROM orders
    WHERE customer_id = c.customer_id
    ORDER BY created_at DESC
    LIMIT 3
) o ON TRUE
ORDER BY c.customer_id, o.created_at DESC;
```

```result
 customer_id |  name   | order_id | total  |     created_at
-------------+---------+----------+--------+---------------------
           1 | Alice   |      104 | 220.00 | 2024-06-20 00:00:00
           1 | Alice   |      103 |  79.00 | 2024-06-02 00:00:00
           1 | Alice   |      102 | 149.50 | 2024-05-15 00:00:00
           2 | Bob     |      106 |  75.00 | 2024-05-20 00:00:00
           2 | Bob     |      105 |  50.00 | 2024-04-10 00:00:00
           3 | Charlie |      108 |  50.00 | 2024-06-10 00:00:00
           3 | Charlie |      107 | 999.00 | 2024-06-01 00:00:00
           4 | Diana   |          |        |
(8 rows)
```

A `LEFT JOIN` always needs an `ON` clause (leave it out and PostgreSQL reports a syntax error). With `LATERAL`, the matching already happens inside the subquery, so `ON TRUE` is the usual way to satisfy the grammar. You can put a real condition there too, but it filters the subquery's output and does not change which rows the subquery produces.

Two details about the top-N query. If two orders of a customer share a `created_at`, `ORDER BY created_at DESC LIMIT 3` can return either, and which one may change between runs. Add a unique column as a tie-breaker (`ORDER BY created_at DESC, order_id DESC`). And the same pattern with `LIMIT 1` gives you "latest row per group".

## Which is faster, window or LATERAL?

On four customers, neither. To compare plans, a bigger table: 10,000 customers with 100 orders each.

```sql
CREATE TABLE big_customers (customer_id INT PRIMARY KEY);
CREATE TABLE big_orders (
    order_id     INT PRIMARY KEY,
    customer_id  INT NOT NULL REFERENCES big_customers (customer_id),
    created_at   TIMESTAMP NOT NULL
);

INSERT INTO big_customers SELECT g FROM generate_series(1, 10000) g;

INSERT INTO big_orders
SELECT g,
       1 + (g::bigint * 7919) % 10000,
       TIMESTAMP '2023-01-01' + (g % 700) * INTERVAL '1 hour'
FROM generate_series(1, 1000000) g;

CREATE INDEX ON big_orders (customer_id, created_at DESC);
VACUUM ANALYZE big_customers;
VACUUM ANALYZE big_orders;
```

The window version walks through the whole index and numbers every one of the million rows:

```sql
EXPLAIN (COSTS OFF)
SELECT customer_id, order_id
FROM (
    SELECT o.*,
           row_number() OVER (PARTITION BY customer_id ORDER BY created_at DESC) AS rn
    FROM big_orders o
) ranked
WHERE rn <= 3;
```

```result
                                            QUERY PLAN
---------------------------------------------------------------------------------------------------
 Subquery Scan on ranked
   ->  WindowAgg
         Window: w1 AS (PARTITION BY o.customer_id ORDER BY o.created_at ROWS UNBOUNDED PRECEDING)
         Run Condition: (row_number() OVER w1 <= 3)
         ->  Index Scan using big_orders_customer_id_created_at_idx on big_orders o
(5 rows)
```

`Run Condition` is a PostgreSQL optimization (version 15 and later) that stops the window function from producing rows once `rn` passes 3. The scan underneath still reads every row. The `LATERAL` version does one short index probe per customer:

```sql
EXPLAIN (COSTS OFF)
SELECT c.customer_id, o.order_id
FROM big_customers c
CROSS JOIN LATERAL (
    SELECT order_id
    FROM big_orders
    WHERE customer_id = c.customer_id
    ORDER BY created_at DESC
    LIMIT 3
) o;
```

```result
                                    QUERY PLAN
----------------------------------------------------------------------------------
 Nested Loop
   ->  Seq Scan on big_customers c
   ->  Limit
         ->  Index Scan using big_orders_customer_id_created_at_idx on big_orders
               Index Cond: (customer_id = c.customer_id)
(5 rows)
```

Under the `Limit` the index already delivers each customer's rows in `created_at DESC` order, so the scan stops after three rows. In a single run on my laptop the window version needed a few times longer than the `LATERAL` version for this data; treat that as a direction, not a benchmark. Both return 30,000 rows.

The balance shifts with the shape of the data. `LATERAL` costs one probe per outer row, so it wins when the groups are deep (many orders per customer, few wanted) and an index matches the `WHERE` and `ORDER BY`. With millions of customers and one or two orders each, the probes buy nothing and one pass with a window function is simpler and often faster. Without a matching index each probe has to read and sort all of that customer's orders, so redo the comparison with `EXPLAIN (ANALYZE)` on your own data and indexes.

When the outer side is restricted, `LATERAL` is also easy to keep cheap, because only the surviving outer rows run a probe:

```sql
EXPLAIN (COSTS OFF)
SELECT c.customer_id, o.order_id
FROM big_customers c
CROSS JOIN LATERAL (
    SELECT order_id
    FROM big_orders
    WHERE customer_id = c.customer_id
    ORDER BY created_at DESC
    LIMIT 3
) o
WHERE c.customer_id <= 20;
```

```result
                                    QUERY PLAN
----------------------------------------------------------------------------------
 Nested Loop
   ->  Index Only Scan using big_customers_pkey on big_customers c
         Index Cond: (customer_id <= 20)
   ->  Limit
         ->  Index Scan using big_orders_customer_id_created_at_idx on big_orders
               Index Cond: (customer_id = c.customer_id)
(6 rows)
```

## Filters outside the subquery

Where a condition goes matters once the subquery has a `LIMIT`. Take "each customer's latest completed order". Charlie's newest order (108) is pending.

Filtering inside the subquery picks the newest completed order:

```sql
SELECT c.name, o.order_id, o.status
FROM customers c
JOIN LATERAL (
    SELECT order_id, status
    FROM orders
    WHERE customer_id = c.customer_id
      AND status = 'completed'
    ORDER BY created_at DESC
    LIMIT 1
) o ON TRUE
ORDER BY c.customer_id;
```

```result
  name   | order_id |  status
---------+----------+-----------
 Alice   |      104 | completed
 Bob     |      106 | completed
 Charlie |      107 | completed
(3 rows)
```

Filtering outside takes the newest order first, whatever its status, and then drops it if it is not completed:

```sql
SELECT c.name, o.order_id, o.status
FROM customers c
JOIN LATERAL (
    SELECT order_id, status
    FROM orders
    WHERE customer_id = c.customer_id
    ORDER BY created_at DESC
    LIMIT 1
) o ON TRUE
WHERE o.status = 'completed'
ORDER BY c.customer_id;
```

```result
 name  | order_id |  status
-------+----------+-----------
 Alice |      104 | completed
 Bob   |      106 | completed
(2 rows)
```

Charlie vanishes, because his latest order is the pending one. Without a `LIMIT` the two placements give the same rows and PostgreSQL plans them the same way, since it flattens such a subquery into an ordinary join. With `LIMIT` they are different questions.

## Several aggregates from one lookup

Day 24 covered the cost of writing one correlated scalar subquery per column. A `LATERAL` subquery computes all of them from a single probe:

```sql
SELECT c.customer_id,
       c.name,
       s.order_count,
       s.last_order,
       s.revenue
FROM customers c
LEFT JOIN LATERAL (
    SELECT count(*)        AS order_count,
           max(created_at) AS last_order,
           sum(total)      AS revenue
    FROM orders
    WHERE customer_id = c.customer_id
) s ON TRUE
ORDER BY c.customer_id;
```

```result
 customer_id |  name   | order_count |     last_order      | revenue
-------------+---------+-------------+---------------------+---------
           1 | Alice   |           4 | 2024-06-20 00:00:00 |  547.50
           2 | Bob     |           2 | 2024-05-20 00:00:00 |  125.00
           3 | Charlie |           2 | 2024-06-10 00:00:00 | 1049.00
           4 | Diana   |           0 |                     |
(4 rows)
```

An aggregate without `GROUP BY` always returns one row, so Diana appears with a count of 0 and `NULL` for the others. This is also the place to compute derived values once per customer. A "churn risk" label needs a reference date, so this one is fixed at 2024-07-01 to make the output repeatable (in a report you would use `CURRENT_DATE`):

```sql
SELECT c.name,
       b.completed,
       b.pending,
       CASE
           WHEN b.last_order IS NULL                      THEN 'never ordered'
           WHEN b.last_order < DATE '2024-07-01' - 60     THEN 'high'
           WHEN b.last_order < DATE '2024-07-01' - 30     THEN 'medium'
           ELSE 'low'
       END AS churn_risk
FROM customers c
LEFT JOIN LATERAL (
    SELECT count(*) FILTER (WHERE status = 'completed') AS completed,
           count(*) FILTER (WHERE status = 'pending')   AS pending,
           max(created_at)                              AS last_order
    FROM orders
    WHERE customer_id = c.customer_id
) b ON TRUE
ORDER BY c.customer_id;
```

```result
  name   | completed | pending |  churn_risk
---------+-----------+---------+---------------
 Alice   |         4 |       0 | low
 Bob     |         2 |       0 | medium
 Charlie |         1 |       1 | low
 Diana   |         0 |       0 | never ordered
(4 rows)
```

Without the `'never ordered'` branch Diana would be labeled `low`, because `NULL < date` is unknown and a `CASE` falls through to `ELSE`. The mistake looks like a good customer.

## Functions that return rows

A set-returning function in `FROM` is implicitly `LATERAL`, so it can refer to earlier tables without the keyword. Here each customer is expanded into the twelve months of their signup year:

```sql
SELECT c.name, m.month_start::date AS month_start
FROM customers c
CROSS JOIN generate_series(
        date_trunc('year', c.signup_date::timestamp),
        date_trunc('year', c.signup_date::timestamp) + INTERVAL '11 months',
        INTERVAL '1 month'
     ) AS m(month_start)
WHERE c.customer_id = 1
ORDER BY m.month_start;
```

```result
 name  | month_start
-------+-------------
 Alice | 2023-01-01
 Alice | 2023-02-01
 Alice | 2023-03-01
 Alice | 2023-04-01
 Alice | 2023-05-01
 Alice | 2023-06-01
 Alice | 2023-07-01
 Alice | 2023-08-01
 Alice | 2023-09-01
 Alice | 2023-10-01
 Alice | 2023-11-01
 Alice | 2023-12-01
(12 rows)
```

The `::timestamp` casts matter. `generate_series` with date arguments returns `timestamptz`, which is shown in the session's time zone. The same trick with `unnest(array_column)` or `jsonb_array_elements(json_column)` expands arrays and JSON arrays into rows, one outer row at a time.

## What other databases call it

- **PostgreSQL**: `LATERAL` since 9.3, with `CROSS JOIN LATERAL` and `LEFT JOIN LATERAL ... ON TRUE`.
- **MySQL**: `LATERAL` derived tables since 8.0.14, same syntax as PostgreSQL.
- **SQL Server**: no `LATERAL` keyword. `CROSS APPLY` and `OUTER APPLY` do the same job, and `TOP` replaces `LIMIT`.
- **Oracle**: `LATERAL` and `CROSS APPLY` / `OUTER APPLY` since 12c.
- **BigQuery**: no `LATERAL`. A comma join against `UNNEST(array_column)` covers the array case, and correlated subqueries cover some of the rest.
- **SQLite**: no `LATERAL`; a window function or a correlated subquery has to do the work.

The SQL Server form of the top-three query:

```sql norun
SELECT c.name, o.order_id, o.total, o.created_at
FROM customers c
CROSS APPLY (
    SELECT TOP 3 order_id, total, created_at
    FROM orders
    WHERE customer_id = c.customer_id
    ORDER BY created_at DESC
) o;
-- OUTER APPLY keeps customers without orders, like LEFT JOIN LATERAL.
```

## In short

- `LATERAL` makes a `FROM` subquery run once per outer row and lets it use the outer row's columns.
- Top-N per group is the main use: with an index on `(group_column, sort_column)`, each group costs a short index probe. With very small groups, a window function is the better tool.
- `CROSS JOIN LATERAL` drops outer rows with no result, `LEFT JOIN LATERAL ... ON TRUE` keeps them.
- With a `LIMIT` inside, a condition inside the subquery and the same condition outside return different rows.
- Several aggregates over the same table belong in one `LATERAL` subquery instead of one scalar subquery each.
