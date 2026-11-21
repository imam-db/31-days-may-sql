---
day: 2
title: "COUNT(*), COUNT(col) and COUNT(DISTINCT col)"
summary: "COUNT(*) counts rows, COUNT(col) skips NULLs, COUNT(DISTINCT col) skips NULLs and duplicates. How the three diverge, and the GROUP BY and LEFT JOIN mistakes that follow."
teaser: "Three counts over one column, three numbers, and the LEFT JOIN that turns 0 into 1."
---

An `orders` table with two nullable columns: `coupon_code` is `NULL` when no coupon was used, and `salesperson` is `NULL` for direct orders.

```sql
CREATE TABLE orders (
    id           INT,
    customer     TEXT,
    coupon_code  TEXT,
    salesperson  TEXT,
    amount       NUMERIC
);

INSERT INTO orders VALUES
    (1, 'Alice', 'SAVE10',   'Bob',   120.00),
    (2, 'Alice', 'SAVE10',   'Carol',  85.00),
    (3, 'Bob',   NULL,       'Bob',   200.00),
    (4, 'Carol', 'FREESHIP', NULL,     45.00),
    (5, 'Carol', 'FREESHIP', NULL,     60.00),
    (6, 'Dave',  NULL,       NULL,    310.00),
    (7, 'Eve',   'SAVE10',   'Carol',  95.00);
```

Three counts over the same column give three different numbers:

```sql
SELECT COUNT(*)                    AS count_star,
       COUNT(coupon_code)          AS count_coupon,
       COUNT(DISTINCT coupon_code) AS count_distinct_coupon
FROM orders;
```

```result
 count_star | count_coupon | count_distinct_coupon
------------+--------------+-----------------------
          7 |            5 |                     2
(1 row)
```

The queries run on PostgreSQL 18. The `COUNT` rules are part of the SQL standard and apply to the other major databases too.

## What each form counts

| Expression | Counts | Result here |
| --- | --- | --- |
| `COUNT(*)` | every row, whatever its columns contain | 7 |
| `COUNT(coupon_code)` | rows where `coupon_code` is not `NULL` | 5 |
| `COUNT(DISTINCT coupon_code)` | different non-`NULL` values | 2 |

`COUNT(*)` never looks at a column, so a row whose columns are all `NULL` still counts. `COUNT(coupon_code)` skips orders 3 and 6. `COUNT(DISTINCT coupon_code)` skips the same two rows and then collapses `SAVE10` (three rows) and `FREESHIP` (two rows) to one each.

`COUNT(1)` is the same as `COUNT(*)`: the constant is never `NULL`. There is no speed difference to chase.

## Counting NULLs

Every column has its own `NULL` pattern, so subtracting the two counts gives the number of `NULL`s in a column:

```sql
SELECT COUNT(*)                       AS total_rows,
       COUNT(coupon_code)             AS orders_with_coupon,
       COUNT(salesperson)             AS orders_with_rep,
       COUNT(*) - COUNT(coupon_code)  AS orders_no_coupon,
       COUNT(*) - COUNT(salesperson)  AS orders_no_rep
FROM orders;
```

```result
 total_rows | orders_with_coupon | orders_with_rep | orders_no_coupon | orders_no_rep
------------+--------------------+-----------------+------------------+---------------
          7 |                  5 |               4 |                2 |             3
(1 row)
```

## Counting distinct combinations of two columns

MySQL accepts `COUNT(DISTINCT coupon_code, salesperson)`. PostgreSQL does not:

```sql norun
SELECT COUNT(DISTINCT coupon_code, salesperson) FROM orders;
```

```text
ERROR:  function count(text, text) does not exist
```

Two workarounds are common, and they treat `NULL` differently. Taking this data:

```sql
SELECT COUNT(DISTINCT (coupon_code, salesperson))  AS row_constructor,
       COUNT(DISTINCT coupon_code || '|' || salesperson) AS concatenation
FROM orders;
```

```result
 row_constructor | concatenation
-----------------+---------------
               5 |             2
(1 row)
```

The row constructor `(coupon_code, salesperson)` is a single value that is not `NULL` even when one of its fields is, so `COUNT` keeps it. Orders 3, 4, 5 and 6 contribute `(NULL, Bob)`, `(FREESHIP, NULL)` and `(NULL, NULL)`, which makes 5 combinations along with `(SAVE10, Bob)` and `(SAVE10, Carol)`. Concatenation is different: `NULL || 'x'` is `NULL`, so every row with a `NULL` in either column disappears and only the two `SAVE10` combinations remain.

Which of these is right depends on the question. If a missing value should count as a combination of its own, the row constructor does it directly. With concatenation, wrap each column in `COALESCE` and use a separator that cannot occur in the data, otherwise `'a|b'` and `'c'` collide with `'a'` and `'b|c'`:

```sql
SELECT COUNT(DISTINCT COALESCE(coupon_code, '(none)') || '|' || COALESCE(salesperson, '(none)')) AS distinct_combos
FROM orders;
```

```result
 distinct_combos
-----------------
               5
(1 row)
```

## The GROUP BY mistake

The most common bug is writing `COUNT(*)` where `COUNT(column)` was meant. Here the second column was supposed to count orders that have a salesperson:

```sql
SELECT coupon_code,
       COUNT(*) AS total,
       COUNT(*) AS with_rep   -- bug: always equal to total
FROM orders
GROUP BY coupon_code
ORDER BY coupon_code NULLS FIRST;
```

```result
 coupon_code | total | with_rep
-------------+-------+----------
             |     2 |        2
 FREESHIP    |     2 |        2
 SAVE10      |     3 |        3
(3 rows)
```

The numbers look plausible and the query runs, which is why this passes review. Only the `SAVE10` row is right. Orders without a coupon include Dave's, which has no salesperson, and the `FREESHIP` orders have none at all. With `COUNT(salesperson)`:

```sql
SELECT COALESCE(coupon_code, '(no coupon)') AS coupon,
       COUNT(*)                             AS total_orders,
       COUNT(salesperson)                   AS orders_with_rep
FROM orders
GROUP BY coupon_code
ORDER BY coupon_code NULLS FIRST;
```

```result
   coupon    | total_orders | orders_with_rep
-------------+--------------+-----------------
 (no coupon) |            2 |               1
 FREESHIP    |            2 |               0
 SAVE10      |            3 |               3
(3 rows)
```

## COUNT with FILTER

`COUNT(column)` only handles the condition "is not `NULL`". For any other condition, PostgreSQL has the `FILTER` clause, which is standard SQL (SQL:2003) and replaces `COUNT(CASE WHEN ... THEN 1 END)`:

```sql
SELECT COALESCE(coupon_code, '(no coupon)')                      AS coupon,
       COUNT(*)                                                  AS total_orders,
       COUNT(*) FILTER (WHERE salesperson IS NOT NULL)           AS orders_with_rep,
       COUNT(*) FILTER (WHERE amount > 100)                      AS high_value_orders
FROM orders
GROUP BY coupon_code
ORDER BY coupon_code NULLS FIRST;
```

```result
   coupon    | total_orders | orders_with_rep | high_value_orders
-------------+--------------+-----------------+-------------------
 (no coupon) |            2 |               1 |                 2
 FREESHIP    |            2 |               0 |                 0
 SAVE10      |            3 |               3 |                 1
(3 rows)
```

SQL Server and MySQL do not support `FILTER`. The portable form is `COUNT(CASE WHEN amount > 100 THEN 1 END)`, which works because `CASE` without `ELSE` returns `NULL` and `COUNT` skips `NULL`s.

## The same mistake after a LEFT JOIN

The `COUNT(*)` bug is worst when it follows an outer join. List customers with their number of orders, including customers with none:

```sql
CREATE TABLE customers (id INT PRIMARY KEY, name TEXT);
INSERT INTO customers VALUES (1, 'Alice'), (2, 'Bob'), (3, 'Zoe');

CREATE TABLE purchases (id INT PRIMARY KEY, customer_id INT);
INSERT INTO purchases VALUES (10, 1), (11, 1), (12, 2);

SELECT c.name,
       COUNT(*)    AS count_star,
       COUNT(p.id) AS count_purchases
FROM customers c
LEFT JOIN purchases p ON p.customer_id = c.id
GROUP BY c.name
ORDER BY c.name;
```

```result
 name  | count_star | count_purchases
-------+------------+-----------------
 Alice |          2 |               2
 Bob   |          1 |               1
 Zoe   |          1 |               0
(3 rows)
```

Zoe has no purchases, but the join still produces one row for her, with `NULL` in every `purchases` column. `COUNT(*)` counts that row and reports 1. `COUNT(p.id)` counts only real purchases and reports 0.

## Related behaviour worth knowing

`AVG(col)` ignores `NULL`s, so it divides by `COUNT(col)`, not by `COUNT(*)`. Dividing the sum by the row count gives a different number as soon as the column has a `NULL`:

```sql
SELECT AVG(x)               AS avg_x,
       SUM(x) / COUNT(*)    AS sum_over_rows
FROM (VALUES (10), (20), (NULL::int)) AS t(x);
```

```result
        avg_x        | sum_over_rows
---------------------+---------------
 15.0000000000000000 |            10
(1 row)
```

Over an empty set, `COUNT` returns `0` while `SUM` and `AVG` return `NULL`:

```sql
SELECT COUNT(*) AS count_of_none,
       SUM(amount) AS sum_of_none
FROM orders
WHERE amount < 0;
```

```result
 count_of_none | sum_of_none
---------------+-------------
             0 |
(1 row)
```

## In short

- `COUNT(*)` counts rows. `COUNT(col)` counts rows where `col` is not `NULL`. `COUNT(DISTINCT col)` counts different non-`NULL` values.
- `COUNT(*) - COUNT(col)` is the number of `NULL`s in `col`.
- After a `LEFT JOIN`, count a column of the right-hand table (`COUNT(p.id)`), not `COUNT(*)`.
- In PostgreSQL, count distinct combinations with `COUNT(DISTINCT (a, b))`, which keeps combinations that contain a `NULL`.
