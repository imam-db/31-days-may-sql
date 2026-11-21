---
day: 22
title: "NOT IN vs NOT EXISTS vs LEFT JOIN IS NULL"
summary: "One NULL in the subquery makes NOT IN return zero rows. Why that happens, how NOT EXISTS and LEFT JOIN avoid it, and what PostgreSQL's planner does with each."
teaser: "One NULL in the subquery and NOT IN returns zero rows. What the planner does with each form."
---

"Which customers have never placed an order?" comes up in almost every reporting job, and there are three usual ways to write it:

```sql norun
-- 1. NOT IN
SELECT * FROM customers
WHERE customer_id NOT IN (SELECT customer_id FROM orders);

-- 2. NOT EXISTS
SELECT * FROM customers c
WHERE NOT EXISTS (
    SELECT 1 FROM orders o WHERE o.customer_id = c.customer_id
);

-- 3. LEFT JOIN ... IS NULL
SELECT c.* FROM customers c
LEFT JOIN orders o ON o.customer_id = c.customer_id
WHERE o.customer_id IS NULL;
```

On clean data all three return the same rows. Add a single order whose `customer_id` is `NULL` and the first one returns nothing, with no error and no warning. The other two keep working.

The examples below run on PostgreSQL 18. The `NOT IN` behaviour comes from the SQL standard, so MySQL, SQL Server and BigQuery give the same empty result. The query plans near the end are PostgreSQL's.

## The data

```sql
CREATE TABLE customers (
    customer_id  INT PRIMARY KEY,
    name         VARCHAR(100) NOT NULL
);

CREATE TABLE orders (
    order_id     INT PRIMARY KEY,
    customer_id  INT,              -- nullable on purpose
    total        NUMERIC(10,2) NOT NULL
);

INSERT INTO customers VALUES
    (1, 'Alice'), (2, 'Bob'), (3, 'Charlie'), (4, 'Diana'), (5, 'Eve');

INSERT INTO orders VALUES
    (101, 1,  99.00),
    (102, 1, 149.50),
    (103, 3, 250.00),
    (104, 4,  75.00);
```

Bob (2) and Eve (5) have no orders, so the correct answer is two rows.

## Clean data: all three agree

```sql
SELECT customer_id, name
FROM customers
WHERE customer_id NOT IN (SELECT customer_id FROM orders)
ORDER BY customer_id;

SELECT c.customer_id, c.name
FROM customers c
WHERE NOT EXISTS (SELECT 1 FROM orders o WHERE o.customer_id = c.customer_id)
ORDER BY c.customer_id;

SELECT c.customer_id, c.name
FROM customers c
LEFT JOIN orders o ON o.customer_id = c.customer_id
WHERE o.customer_id IS NULL
ORDER BY c.customer_id;
```

Each of the three returns:

```result
 customer_id | name
-------------+------
           2 | Bob
           5 | Eve
(2 rows)
```

## One NULL order

A guest checkout, a half-finished migration or a bad import leaves an order without a customer:

```sql
INSERT INTO orders VALUES (105, NULL, 200.00);
```

Run the `NOT IN` query again:

```sql
SELECT customer_id, name
FROM customers
WHERE customer_id NOT IN (SELECT customer_id FROM orders)
ORDER BY customer_id;
```

```result
 customer_id | name
-------------+------
(0 rows)
```

Bob and Eve are gone. `NOT EXISTS` and the `LEFT JOIN` version still return both of them:

```sql
SELECT c.customer_id, c.name
FROM customers c
WHERE NOT EXISTS (SELECT 1 FROM orders o WHERE o.customer_id = c.customer_id)
ORDER BY c.customer_id;

SELECT c.customer_id, c.name
FROM customers c
LEFT JOIN orders o ON o.customer_id = c.customer_id
WHERE o.customer_id IS NULL
ORDER BY c.customer_id;
```

```result
 customer_id | name
-------------+------
           2 | Bob
           5 | Eve
(2 rows)
```

In a report this is easy to miss. The query still runs, it just says "no inactive customers", and that reads like good news.

## Why NOT IN returns nothing

`x NOT IN (a, b, c)` is shorthand for `x <> a AND x <> b AND x <> c`. The subquery now returns `1, 1, 3, 4, NULL`, so for Bob the condition is:

```sql norun
2 <> 1 AND 2 <> 1 AND 2 <> 3 AND 2 <> 4 AND 2 <> NULL
```

The first four comparisons are true. The last one is `UNKNOWN`, because comparing anything with `NULL` gives `UNKNOWN`. `TRUE AND UNKNOWN` is `UNKNOWN`, and `WHERE` keeps a row only when the condition is `TRUE`. So Bob is dropped, and so is everyone else.

Put differently: once the list contains a `NULL`, `NOT IN` can be false or unknown, but never true. You can see it without any tables:

```sql
SELECT 2 NOT IN (1, 3, NULL) AS bob,
       1 NOT IN (1, 3, NULL) AS alice;
```

```result
 bob | alice
-----+-------
     | f
(1 row)
```

The empty cell is `NULL`. For Alice the answer is a plain `false`, because `1 <> 1` already fails. For Bob it is unknown, and unknown is filtered out.

## Why NOT EXISTS and LEFT JOIN are not affected

`NOT EXISTS` asks a different question for each customer: is there at least one order row where `o.customer_id = c.customer_id`? For the guest order that comparison is `UNKNOWN`, which counts as "not a match". The `NULL` row never matches anyone, so it cannot hide anyone either.

`LEFT JOIN` uses the same join condition. Customers with no matching order still come out of the join, with `NULL` in every `orders` column, and `WHERE o.customer_id IS NULL` keeps exactly those rows.

### Which column to test after a LEFT JOIN

Two choices are always safe:

- **The join column**, here `o.customer_id`. A row that matched through `o.customer_id = c.customer_id` cannot have `NULL` in that column, because `NULL` never equals anything.
- **A `NOT NULL` column** of the right table, such as the primary key `o.order_id`.

The one to avoid is a nullable column that is not part of the join. Add an optional coupon code and test that instead:

```sql
ALTER TABLE orders ADD COLUMN coupon_code TEXT;  -- no order has one yet

SELECT c.customer_id, c.name
FROM customers c
LEFT JOIN orders o ON o.customer_id = c.customer_id
WHERE o.coupon_code IS NULL
ORDER BY c.customer_id;
```

```result
 customer_id |  name
-------------+---------
           1 | Alice
           1 | Alice
           2 | Bob
           3 | Charlie
           4 | Diana
           5 | Eve
(6 rows)
```

Every real order has an empty coupon, so every customer looks like a non-buyer, and Alice shows up twice. The query is valid and the answer is wrong.

## Fixing NOT IN

If you keep `NOT IN`, filter the `NULL`s out of the subquery:

```sql
SELECT customer_id, name
FROM customers
WHERE customer_id NOT IN (
    SELECT customer_id FROM orders WHERE customer_id IS NOT NULL
)
ORDER BY customer_id;
```

```result
 customer_id | name
-------------+------
           2 | Bob
           5 | Eve
(2 rows)
```

This works, but every `NOT IN` against a nullable column needs that extra line, and nothing tells you when it is missing. Writing `NOT EXISTS` from the start removes the problem instead of patching it.

## What PostgreSQL does with each

Correct results are the main reason to prefer `NOT EXISTS`, but the plans differ too. `NOT EXISTS` becomes an anti-join: PostgreSQL builds a hash of one side and keeps the customers that find no partner.

```sql
EXPLAIN (COSTS OFF)
SELECT c.customer_id, c.name
FROM customers c
WHERE NOT EXISTS (SELECT 1 FROM orders o WHERE o.customer_id = c.customer_id);
```

```result
                  QUERY PLAN
----------------------------------------------
 Hash Right Anti Join
   Hash Cond: (o.customer_id = c.customer_id)
   ->  Seq Scan on orders o
   ->  Hash
         ->  Seq Scan on customers c
(5 rows)
```

`LEFT JOIN ... WHERE o.customer_id IS NULL` gets the same plan. PostgreSQL recognises the pattern because the `IS NULL` test is on a column from the join condition. Test `o.order_id` instead and the rows are the same, but the plan is an ordinary outer join followed by a filter:

```sql
EXPLAIN (COSTS OFF)
SELECT c.customer_id, c.name
FROM customers c
LEFT JOIN orders o ON o.customer_id = c.customer_id
WHERE o.order_id IS NULL;
```

```result
                  QUERY PLAN
----------------------------------------------
 Hash Right Join
   Hash Cond: (o.customer_id = c.customer_id)
   Filter: (o.order_id IS NULL)
   ->  Seq Scan on orders o
   ->  Hash
         ->  Seq Scan on customers c
(6 rows)
```

On five customers this does not matter. On large tables the anti-join can stop at the first match per customer, while the outer join produces every matching row and discards them afterwards.

`NOT IN` with a subquery is never turned into an anti-join in PostgreSQL, not even when the column is declared `NOT NULL`. It runs as a subplan. When the subquery's result fits in `work_mem`, that subplan is hashed and reasonably fast:

```sql
EXPLAIN (COSTS OFF)
SELECT customer_id, name
FROM customers
WHERE customer_id NOT IN (
    SELECT customer_id FROM orders WHERE customer_id IS NOT NULL
);
```

```result
                          QUERY PLAN
---------------------------------------------------------------
 Seq Scan on customers
   Filter: (NOT (ANY (customer_id = (hashed SubPlan 1).col1)))
   SubPlan 1
     ->  Seq Scan on orders
           Filter: (customer_id IS NOT NULL)
(5 rows)
```

When it does not fit, PostgreSQL falls back to a plain subplan that goes through the subquery's rows again for every outer row. With 200,000 customers and 400,000 orders, at the default `work_mem` of 4MB:

```sql
CREATE TABLE big_customers AS
SELECT g AS id FROM generate_series(1, 200000) g;

CREATE TABLE big_orders (id INT NOT NULL, customer_id INT NOT NULL);
INSERT INTO big_orders
SELECT g, (g * 7) % 150000 FROM generate_series(1, 400000) g;

ANALYZE big_customers;
ANALYZE big_orders;

EXPLAIN (COSTS OFF)
SELECT * FROM big_customers
WHERE id NOT IN (SELECT customer_id FROM big_orders);
```

```result
                  QUERY PLAN
-----------------------------------------------
 Seq Scan on big_customers
   Filter: (NOT (ANY (id = (SubPlan 1).col1)))
   SubPlan 1
     ->  Materialize
           ->  Seq Scan on big_orders
(5 rows)
```

`customer_id` is `NOT NULL` here and it is still not an anti-join. That plan checks each of the 200,000 customers against up to 400,000 materialised rows. The `NOT EXISTS` version of the same query is a single hash anti-join:

```sql
EXPLAIN (COSTS OFF)
SELECT * FROM big_customers c
WHERE NOT EXISTS (SELECT 1 FROM big_orders o WHERE o.customer_id = c.id);
```

```result
              QUERY PLAN
--------------------------------------
 Hash Anti Join
   Hash Cond: (c.id = o.customer_id)
   ->  Seq Scan on big_customers c
   ->  Hash
         ->  Seq Scan on big_orders o
(5 rows)
```

Other engines handle this differently. Oracle, for example, has a null-aware anti-join for `NOT IN`. In PostgreSQL, `NOT EXISTS` is the form the planner knows how to optimise.

## NOT EXISTS or LEFT JOIN?

Both are correct and both become anti-joins when written as above. I default to `NOT EXISTS`:

- It reads like the question: customers for whom no order exists.
- There is no `IS NULL` column to pick, so the coupon-code mistake cannot happen.
- Extra conditions ("no order in the last 90 days") go inside the subquery and stay there.

`LEFT JOIN` is the natural form when the anti-join is part of a larger query that already joins `orders` for other columns.

## Two cases that look similar but are fine

`NOT IN` with a list you typed yourself is safe, as long as you do not type `NULL` into it:

```sql
CREATE TABLE events (event_type TEXT);
INSERT INTO events VALUES ('login'), ('logout'), ('purchase'), (NULL);

SELECT event_type FROM events
WHERE event_type NOT IN ('login', 'logout');
```

```result
 event_type
------------
 purchase
(1 row)
```

The row with a `NULL` event type is not returned either, since `NULL NOT IN (...)` is unknown. If you want it, ask for it with `OR event_type IS NULL`.

`IN` with a `NULL` in the list does not empty the result. `IN` is a chain of `OR`s, and `TRUE OR UNKNOWN` is still `TRUE`, so real matches survive:

```sql
SELECT event_type FROM events
WHERE event_type IN ('login', NULL);
```

```result
 event_type
------------
 login
(1 row)
```

The `NULL` in the list never matches anything, including the `NULL` row. If you need `NULL` to equal `NULL`, that is `IS NOT DISTINCT FROM` (`<=>` in MySQL), which is a different tool for a different problem.

## In short

- `NOT IN (subquery)` returns no rows as soon as the subquery returns one `NULL`, and it does so without an error.
- `NOT EXISTS` is not affected by `NULL`s and is the form PostgreSQL turns into an anti-join. Use it by default.
- After a `LEFT JOIN`, test the join column or a `NOT NULL` column for `IS NULL`, never a nullable column outside the join.
- `NOT IN` with a literal list is fine. If you keep `NOT IN` with a subquery, add `WHERE ... IS NOT NULL` to it.
