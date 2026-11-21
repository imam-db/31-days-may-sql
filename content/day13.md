---
day: 13
title: "Median and Percentiles Without Built-in Functions"
summary: "A median is the value at a position in the sorted data. Compute it, and any percentile with interpolation, from ROW_NUMBER and COUNT, then check the result against PERCENTILE_CONT."
teaser: "Median and any percentile from ROW_NUMBER and COUNT, checked against PERCENTILE_CONT."
---

A product manager asks for the median response time of an API. `AVG` is the first function you reach for, and it is the wrong one: one slow request among a thousand fast ones moves the mean noticeably and leaves the median where it was. Then you look for a `MEDIAN` function. PostgreSQL does not have one, and neither does MySQL, which has no percentile function of any kind.

PostgreSQL does have `PERCENTILE_CONT`, and the end of this article compares against it. Computing the median by hand is still worth knowing, because it works in any database with window functions, and the same position arithmetic gives you any percentile. The examples run on PostgreSQL 18.

## The data

```sql
CREATE TABLE products (
    product_id    INT PRIMARY KEY,
    category      TEXT NOT NULL,
    product_name  TEXT NOT NULL,
    price         NUMERIC(10,2) NOT NULL
);

INSERT INTO products VALUES
    (1,  'electronics', 'USB Cable',      8.00),
    (2,  'electronics', 'Wireless Mouse', 25.00),
    (3,  'electronics', 'Keyboard',       45.00),
    (4,  'electronics', 'Headphones',     80.00),
    (5,  'electronics', 'Monitor',       350.00),
    (6,  'electronics', 'Laptop',       1200.00),
    (7,  'books',       'Paperback',      12.00),
    (8,  'books',       'Cookbook',       17.00),
    (9,  'books',       'Textbook',       20.00),
    (10, 'books',       'Atlas',          35.00),
    (11, 'furniture',   'Stool',          90.00),
    (12, 'furniture',   'Desk',          150.00),
    (13, 'furniture',   'Chair',         220.00),
    (14, 'furniture',   'Bookshelf',     400.00),
    (15, 'furniture',   'Sofa',          900.00);
```

## Mean and median disagree

Six electronics prices, from $8 to $1,200. The laptop pulls the mean far above what a typical product costs:

```sql
SELECT ROUND(AVG(price), 2) AS mean_price,
       PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY price) AS median_price
FROM products
WHERE category = 'electronics';
```

```result
 mean_price | median_price
------------+--------------
     284.67 |         62.5
(1 row)
```

The mean is $284.67 and the median is $62.50, the average of the 3rd and 4th prices (45 and 80). `PERCENTILE_CONT` is the built-in version, and it is the reference the hand-written queries below are checked against.

## A median is a position

Sort the values, find the middle, read it. With `n` rows:

- Odd `n` (7 rows): the median is the value at position 4, which is `(n+1)/2`.
- Even `n` (6 rows): the median is the average of the values at positions 3 and 4.

Both cases can be written as "average the values at positions `FLOOR((n+1)/2)` and `CEIL((n+1)/2)`". For odd `n` the two positions are the same row, so the average is that row's value.

In SQL, `ROW_NUMBER()` supplies the position and `COUNT(*) OVER ()` supplies `n`:

```sql
SELECT price,
       ROW_NUMBER() OVER (ORDER BY price) AS rn,
       COUNT(*) OVER () AS total
FROM products
WHERE category = 'electronics'
ORDER BY price;
```

```result
  price  | rn | total
---------+----+-------
    8.00 |  1 |     6
   25.00 |  2 |     6
   45.00 |  3 |     6
   80.00 |  4 |     6
  350.00 |  5 |     6
 1200.00 |  6 |     6
(6 rows)
```

Now keep only the middle rows and average them:

```sql
WITH ranked AS (
    SELECT price,
           ROW_NUMBER() OVER (ORDER BY price) AS rn,
           COUNT(*) OVER () AS total
    FROM products
    WHERE category = 'electronics'
)
SELECT AVG(price) AS median_price
FROM ranked
WHERE rn IN (FLOOR((total + 1) / 2.0), CEIL((total + 1) / 2.0));
```

```result
    median_price
---------------------
 62.5000000000000000
(1 row)
```

62.50, the same as `PERCENTILE_CONT`. The `2.0` matters. `total` is an integer, and with `2` the division truncates: `(6 + 1) / 2` is 3, so `FLOOR` and `CEIL` both give 3 and the query returns 45 instead of 62.50.

```sql
SELECT (6 + 1) / 2   AS int_division,
       (6 + 1) / 2.0 AS decimal_division;
```

```result
 int_division |  decimal_division
--------------+--------------------
            3 | 3.5000000000000000
(1 row)
```

If you prefer to stay in integers, `FLOOR((n+1)/2)` is `(n+1)/2` with truncation and `CEIL((n+1)/2)` is `(n+2)/2`. The filter becomes `rn IN ((total + 1) / 2, (total + 2) / 2)`, which gives the same rows without any decimal arithmetic.

An odd count works with no change to the query. Here are seven values:

```sql
WITH ranked AS (
    SELECT x,
           ROW_NUMBER() OVER (ORDER BY x) AS rn,
           COUNT(*) OVER () AS total
    FROM (VALUES (3), (9), (1), (7), (5), (11), (13)) AS t(x)
)
SELECT AVG(x) AS median
FROM ranked
WHERE rn IN ((total + 1) / 2, (total + 2) / 2);
```

```result
       median
--------------------
 7.0000000000000000
(1 row)
```

Both expressions are 4 when `total` is 7, so the filter matches one row, and the average of one value is that value.

## A shortcut that only half works

Because you know there are 6 rows, you might skip the window functions:

```sql
SELECT price
FROM products
WHERE category = 'electronics'
ORDER BY price
LIMIT 1 OFFSET 3;
```

```result
 price
-------
 80.00
(1 row)
```

That returns 80, the upper of the two middle values and not the median. `OFFSET` takes a number you must already have computed, and for an even row count one row is not enough. Running a `COUNT(*)` first and building the `OFFSET` in application code works only if both queries see the same data.

## Median per group

Add `PARTITION BY` to both window functions and `GROUP BY` to the outer query:

```sql
WITH ranked AS (
    SELECT category, price,
           ROW_NUMBER() OVER (PARTITION BY category ORDER BY price) AS rn,
           COUNT(*) OVER (PARTITION BY category) AS total
    FROM products
)
SELECT category, AVG(price) AS median_price
FROM ranked
WHERE rn IN ((total + 1) / 2, (total + 2) / 2)
GROUP BY category
ORDER BY category;
```

```result
  category   |     median_price
-------------+----------------------
 books       |  18.5000000000000000
 electronics |  62.5000000000000000
 furniture   | 220.0000000000000000
(3 rows)
```

Books has four prices (12, 17, 20, 35), so its median is (17 + 20) / 2 = 18.50. Furniture has five, and the median is the third, 220.

## NULLs distort the position

The pattern counts rows, so a `NULL` in the column is counted and then sorted last, and the "middle" shifts. With the values 1, 2, 3 and one `NULL` the median should be 2:

```sql
WITH ranked AS (
    SELECT x,
           ROW_NUMBER() OVER (ORDER BY x) AS rn,
           COUNT(*) OVER () AS total
    FROM (VALUES (1), (2), (3), (NULL::int)) AS t(x)
)
SELECT AVG(x) AS median_with_null
FROM ranked
WHERE rn IN ((total + 1) / 2, (total + 2) / 2);
```

```result
  median_with_null
--------------------
 2.5000000000000000
(1 row)
```

It reports 2.5, from positions 2 and 3, because `total` is 4. Filter the `NULL`s out in the CTE (`WHERE x IS NOT NULL`) so that the count and the positions agree. `PERCENTILE_CONT` ignores `NULL` inputs, so the comparison with the built-in only holds after that filter.

## Any percentile, with interpolation

The median is the 50th percentile. For a fraction `p` between 0 and 1, the target position is `1 + p * (n - 1)`. For `p = 0.5` and `n = 6` that is 3.5, halfway between positions 3 and 4. If the position falls between two rows, take the lower row and add the fractional part of the distance to the next row. `LEAD()` gives each row its successor's value, so the lower row has what it needs:

```sql
WITH ranked AS (
    SELECT price,
           ROW_NUMBER() OVER w AS rn,
           COUNT(*) OVER () AS total,
           LEAD(price) OVER w AS next_price
    FROM products
    WHERE category = 'electronics'
    WINDOW w AS (ORDER BY price)
),
target AS (
    SELECT *, 1 + 0.9 * (total - 1) AS pos
    FROM ranked
)
SELECT price + (pos - rn) * COALESCE(next_price - price, 0) AS p90_price
FROM target
WHERE rn = FLOOR(pos);
```

```result
 p90_price
-----------
   775.000
(1 row)
```

For the 90th percentile, `pos` is 5.5, so the row at position 5 ($350) is chosen and half the distance to the next row ($1,200) is added: 350 + 0.5 * 850 = 775. At the very top, `p = 1`, `pos` equals `n` and the last row has no successor, which is what the `COALESCE` covers.

`PERCENTILE_CONT` does the same interpolation, so it should agree:

```sql
SELECT PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY price) AS p90_price
FROM products
WHERE category = 'electronics';
```

```result
 p90_price
-----------
       775
(1 row)
```

## Percentiles per endpoint

The case that started this: latency percentiles per endpoint. The table below has 40 requests per endpoint with synthetic response times. Percentiles go in a `VALUES` list, so adding p99 means adding a row:

```sql
CREATE TABLE api_requests (
    endpoint          TEXT NOT NULL,
    response_time_ms  INT  NOT NULL
);

INSERT INTO api_requests
SELECT e.endpoint, 20 + (g * g * 7 + e.k * 31) % 480
FROM (VALUES ('/search', 1), ('/login', 2), ('/checkout', 3)) AS e(endpoint, k),
     generate_series(1, 40) AS g;

WITH ranked AS (
    SELECT endpoint, response_time_ms AS v,
           ROW_NUMBER() OVER w AS rn,
           COUNT(*) OVER (PARTITION BY endpoint) AS n,
           LEAD(response_time_ms) OVER w AS next_v
    FROM api_requests
    WINDOW w AS (PARTITION BY endpoint ORDER BY response_time_ms)
)
SELECT r.endpoint, q.p,
       ROUND(r.v + (l.pos - r.rn) * COALESCE(r.next_v - r.v, 0), 2) AS value_ms
FROM ranked r
CROSS JOIN (VALUES (0.5), (0.95)) AS q(p)
CROSS JOIN LATERAL (SELECT 1 + q.p * (r.n - 1) AS pos) AS l
WHERE r.rn = FLOOR(l.pos)
ORDER BY r.endpoint, q.p;
```

```result
 endpoint  |  p   | value_ms
-----------+------+----------
 /checkout |  0.5 |   200.00
 /checkout | 0.95 |   461.20
 /login    |  0.5 |   213.00
 /login    | 0.95 |   494.00
 /search   |  0.5 |   250.50
 /search   | 0.95 |   483.80
(6 rows)
```

The built-in gives the same numbers:

```sql
SELECT endpoint, q.p,
       ROUND(PERCENTILE_CONT(q.p) WITHIN GROUP (ORDER BY response_time_ms)::numeric, 2) AS value_ms
FROM api_requests
CROSS JOIN (VALUES (0.5), (0.95)) AS q(p)
GROUP BY endpoint, q.p
ORDER BY endpoint, q.p;
```

```result
 endpoint  |  p   | value_ms
-----------+------+----------
 /checkout |  0.5 |   200.00
 /checkout | 0.95 |   461.20
 /login    |  0.5 |   213.00
 /login    | 0.95 |   494.00
 /search   |  0.5 |   250.50
 /search   | 0.95 |   483.80
(6 rows)
```

With 40 rows the 95th percentile sits at position 38.05, so p95 is the 38th value plus 5 percent of the gap to the 39th. Averaging the two neighbouring rows instead of weighting them is a common shortcut. It gives the right answer only when the fraction happens to be 0.5, which is the case for the median of an even count and for few other percentiles.

## What the databases offer

- PostgreSQL: `PERCENTILE_CONT(p) WITHIN GROUP (ORDER BY x)` interpolates, as above. `PERCENTILE_DISC` returns an actual value from the data with no interpolation, so `PERCENTILE_DISC(0.5)` over the electronics prices is 45, the lower middle value, and not 62.50. Both are aggregates and take `GROUP BY` directly.
- SQL Server and BigQuery have `PERCENTILE_CONT`, but only as a window function (`... OVER (PARTITION BY ...)`), so you cannot use it with `GROUP BY` directly.
- Oracle has a `MEDIAN` aggregate. MariaDB has `MEDIAN()` as a window function.
- MySQL has none of these, which is where the hand-written version earns its keep.

The basic median query (`ROW_NUMBER`, `COUNT(*) OVER ()`, `AVG`) runs on MySQL 8 as written. Division with `/` returns a decimal there, so the `2.0` is not needed.

## In short

- A median is the average of the values at positions `(n+1)/2` and `(n+2)/2` (integer division) of the sorted data. `ROW_NUMBER()` and `COUNT(*) OVER ()` supply both numbers.
- For any percentile `p`, the target position is `1 + p * (n - 1)`, and the answer interpolates between that row and the next one. `LEAD()` supplies the next value.
- Remove `NULL`s before ranking, or the count and the positions disagree.
- When the database has `PERCENTILE_CONT` as an aggregate, use it, and use this version to understand what it computes.
