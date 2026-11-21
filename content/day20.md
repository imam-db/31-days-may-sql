---
day: 20
title: "GROUPING SETS, ROLLUP and CUBE: Multi-Level Aggregation in One Query"
summary: "GROUPING SETS, ROLLUP and CUBE produce subtotals and a grand total in one query. What each one generates, how GROUPING() tells a subtotal from a real NULL, how the plans compare with UNION ALL, and why a percent-of-total window can show 50%."
teaser: "Subtotals and a grand total in one pass, and how to tell a subtotal from a real NULL."
---

A sales report needs four levels of detail in one result: revenue per region and category, per region, per category, and a grand total. The straightforward way is four `GROUP BY` queries joined with `UNION ALL`. SQL has a clause that does it in one: `GROUPING SETS`, with `ROLLUP` and `CUBE` as shorthands for two common patterns.

The examples run on PostgreSQL 18. The clauses are standard SQL, and the last section lists which other databases have them.

## The data

```sql
CREATE TABLE sales (
    sale_id      INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    region       VARCHAR(20)   NOT NULL,
    category     VARCHAR(30)   NOT NULL,
    product_name VARCHAR(100)  NOT NULL,
    amount       NUMERIC(10,2) NOT NULL,
    sale_date    DATE          NOT NULL
);

INSERT INTO sales (region, category, product_name, amount, sale_date) VALUES
    ('APAC', 'Electronics', 'Laptop',  1200.00, '2024-01-15'),
    ('APAC', 'Electronics', 'Phone',    800.00, '2024-02-10'),
    ('APAC', 'Furniture',   'Desk',     450.00, '2024-03-05'),
    ('EU',   'Electronics', 'Laptop',  1100.00, '2024-01-20'),
    ('EU',   'Electronics', 'Phone',    750.00, '2024-02-15'),
    ('EU',   'Furniture',   'Chair',    300.00, '2024-03-10'),
    ('EU',   'Furniture',   'Desk',     500.00, '2024-03-12'),
    ('US',   'Electronics', 'Laptop',  1300.00, '2024-01-25'),
    ('US',   'Electronics', 'Phone',    900.00, '2024-02-20'),
    ('US',   'Furniture',   'Chair',    350.00, '2024-03-15'),
    ('US',   'Clothing',    'Jacket',   150.00, '2024-03-18'),
    ('US',   'Clothing',    'Shoes',    120.00, '2024-03-20');
```

Total revenue is 7,920.00 over 3 regions and 3 categories (Clothing is sold only in the US).

## The UNION ALL way

One `GROUP BY` per level, with `NULL` standing in for the column that is not grouped:

```sql
SELECT region, category, SUM(amount) AS revenue FROM sales GROUP BY region, category
UNION ALL
SELECT region, NULL, SUM(amount) FROM sales GROUP BY region
UNION ALL
SELECT NULL, category, SUM(amount) FROM sales GROUP BY category
UNION ALL
SELECT NULL, NULL, SUM(amount) FROM sales
ORDER BY region, category;
```

```result
 region |  category   | revenue
--------+-------------+---------
 APAC   | Electronics | 2000.00
 APAC   | Furniture   |  450.00
 APAC   |             | 2450.00
 EU     | Electronics | 1850.00
 EU     | Furniture   |  800.00
 EU     |             | 2650.00
 US     | Clothing    |  270.00
 US     | Electronics | 2200.00
 US     | Furniture   |  350.00
 US     |             | 2820.00
        | Clothing    |  270.00
        | Electronics | 6050.00
        | Furniture   | 1600.00
        |             | 7920.00
(14 rows)
```

It works, but the table is read four times, and the query grows with every dimension: three dimensions need eight `SELECT`s.

## GROUPING SETS

`GROUPING SETS` lists the groupings you want, and PostgreSQL computes them together:

```sql
SELECT region, category, SUM(amount) AS revenue
FROM sales
GROUP BY GROUPING SETS (
    (region, category),   -- per region and category
    (region),             -- region subtotal
    (category),           -- category subtotal
    ()                    -- grand total
)
ORDER BY region, category;
```

```result
 region |  category   | revenue
--------+-------------+---------
 APAC   | Electronics | 2000.00
 APAC   | Furniture   |  450.00
 APAC   |             | 2450.00
 EU     | Electronics | 1850.00
 EU     | Furniture   |  800.00
 EU     |             | 2650.00
 US     | Clothing    |  270.00
 US     | Electronics | 2200.00
 US     | Furniture   |  350.00
 US     |             | 2820.00
        | Clothing    |  270.00
        | Electronics | 6050.00
        | Furniture   | 1600.00
        |             | 7920.00
(14 rows)
```

It returns the same 14 rows as the `UNION ALL` version. A `NULL` in `region` or `category` means that column was not part of the grouping for that row: `(APAC, NULL)` is the APAC subtotal and `(NULL, NULL)` the grand total.

The plan shows what happens to the table. With four `GROUP BY` queries, each reads it:

```sql
EXPLAIN (COSTS OFF)
SELECT region, category, SUM(amount) FROM sales GROUP BY region, category
UNION ALL
SELECT region, NULL, SUM(amount) FROM sales GROUP BY region
UNION ALL
SELECT NULL, category, SUM(amount) FROM sales GROUP BY category
UNION ALL
SELECT NULL, NULL, SUM(amount) FROM sales;
```

```result
                   QUERY PLAN
-------------------------------------------------
 Append
   ->  HashAggregate
         Group Key: sales.region, sales.category
         ->  Seq Scan on sales
   ->  HashAggregate
         Group Key: sales_1.region
         ->  Seq Scan on sales sales_1
   ->  HashAggregate
         Group Key: sales_2.category
         ->  Seq Scan on sales sales_2
   ->  Aggregate
         ->  Seq Scan on sales sales_3
(12 rows)
```

and with `GROUPING SETS` there is one scan feeding all four groupings:

```sql
EXPLAIN (COSTS OFF)
SELECT region, category, SUM(amount)
FROM sales
GROUP BY GROUPING SETS ((region, category), (region), (category), ());
```

```result
          QUERY PLAN
------------------------------
 MixedAggregate
   Hash Key: region, category
   Hash Key: region
   Hash Key: category
   Group Key: ()
   ->  Seq Scan on sales
(6 rows)
```

`MixedAggregate` keeps one hash table per grouping and updates all of them as the rows pass by. That saves the repeated reads, which matters most when the table is large and not already in memory. The aggregation work itself is still done once per grouping set. When the planner cannot hash every set, for example because of its memory settings or a data type that cannot be hashed, it falls back to sorted aggregation, which can mean sorting the input for some of the sets.

## ROLLUP

`ROLLUP (a, b, c)` is shorthand for the sets `(a, b, c)`, `(a, b)`, `(a)` and `()`. It drops one column at a time from the right, so it suits hierarchies:

```sql
SELECT region, category, SUM(amount) AS revenue
FROM sales
GROUP BY ROLLUP (region, category)
ORDER BY region, category;
```

```result
 region |  category   | revenue
--------+-------------+---------
 APAC   | Electronics | 2000.00
 APAC   | Furniture   |  450.00
 APAC   |             | 2450.00
 EU     | Electronics | 1850.00
 EU     | Furniture   |  800.00
 EU     |             | 2650.00
 US     | Clothing    |  270.00
 US     | Electronics | 2200.00
 US     | Furniture   |  350.00
 US     |             | 2820.00
        |             | 7920.00
(11 rows)
```

There are subtotals per region and a grand total, but no per-category subtotal, because `(category)` alone is not a prefix of `(region, category)`. You can check that `ROLLUP` is only shorthand by comparing it with the explicit sets. The `EXCEPT` in both directions returns nothing when the results are identical:

```sql
(SELECT region, category, SUM(amount) FROM sales GROUP BY ROLLUP (region, category)
 EXCEPT
 SELECT region, category, SUM(amount) FROM sales GROUP BY GROUPING SETS ((region, category), (region), ()))
UNION ALL
(SELECT region, category, SUM(amount) FROM sales GROUP BY GROUPING SETS ((region, category), (region), ())
 EXCEPT
 SELECT region, category, SUM(amount) FROM sales GROUP BY ROLLUP (region, category));
```

```result
 region | category | sum
--------+----------+-----
(0 rows)
```

Typical uses are Year > Quarter > Month, Country > City, or Department > Team: any case where each level belongs to exactly one parent.

## CUBE

`CUBE (a, b)` generates every combination of its columns: `(a, b)`, `(a)`, `(b)` and `()`. For two columns, that is exactly the `GROUPING SETS` above:

```sql
SELECT region, category, SUM(amount) AS revenue
FROM sales
GROUP BY CUBE (region, category)
ORDER BY region, category;
```

```result
 region |  category   | revenue
--------+-------------+---------
 APAC   | Electronics | 2000.00
 APAC   | Furniture   |  450.00
 APAC   |             | 2450.00
 EU     | Electronics | 1850.00
 EU     | Furniture   |  800.00
 EU     |             | 2650.00
 US     | Clothing    |  270.00
 US     | Electronics | 2200.00
 US     | Furniture   |  350.00
 US     |             | 2820.00
        | Clothing    |  270.00
        | Electronics | 6050.00
        | Furniture   | 1600.00
        |             | 7920.00
(14 rows)
```

`CUBE` over n columns produces 2^n grouping sets: 4 for two columns, 8 for three, 32 for five. That is a lot of output and work for a report that only needs a hierarchy. Use `CUBE` when readers really may slice by any subset of the dimensions, and `ROLLUP` or an explicit `GROUPING SETS` list otherwise.

You can also mix plain columns with these clauses. `GROUP BY region, ROLLUP (category)` always groups by `region`, and rolls up only `category`: region details and region subtotals, with no grand total.

```sql
SELECT region, category, SUM(amount) AS revenue
FROM sales
GROUP BY region, ROLLUP (category)
ORDER BY region, category;
```

```result
 region |  category   | revenue
--------+-------------+---------
 APAC   | Electronics | 2000.00
 APAC   | Furniture   |  450.00
 APAC   |             | 2450.00
 EU     | Electronics | 1850.00
 EU     | Furniture   |  800.00
 EU     |             | 2650.00
 US     | Clothing    |  270.00
 US     | Electronics | 2200.00
 US     | Furniture   |  350.00
 US     |             | 2820.00
(10 rows)
```

## Telling a subtotal from a NULL in the data

In the outputs above, `NULL` means "not grouped by this column". If a grouping column can itself contain `NULL`, a real `NULL` group looks the same. Suppose some sign-ups have no recorded channel:

```sql
CREATE TABLE signups (
    channel TEXT,
    plan    TEXT NOT NULL,
    users   INT  NOT NULL
);

INSERT INTO signups VALUES
    ('web',   'free', 100),
    ('web',   'pro',   20),
    (NULL,    'free',  30),
    (NULL,    'pro',    5);

SELECT channel, SUM(users) AS users
FROM signups
GROUP BY ROLLUP (channel)
ORDER BY channel, users;
```

```result
 channel | users
---------+-------
 web     |   120
         |    35
         |   155
(3 rows)
```

Two rows have a `NULL` channel. One is the group of sign-ups with unknown channel (35) and the other is the grand total (155), and nothing in the output says which is which. `GROUPING(column)` returns 1 when the column was rolled up for the row and 0 when it is a real group value:

```sql
SELECT CASE WHEN GROUPING(channel) = 1 THEN '(all channels)'
            ELSE COALESCE(channel, '(unknown)') END AS channel,
       SUM(users) AS users
FROM signups
GROUP BY ROLLUP (channel)
ORDER BY GROUPING(channel), channel;
```

```result
    channel     | users
----------------+-------
 (unknown)      |    35
 web            |   120
 (all channels) |   155
(3 rows)
```

Plain `COALESCE(channel, 'ALL')` would label the unknown group "ALL" too, which is why `GROUPING()` is needed. With several columns, `GROUPING(a, b)` returns a bit mask: 0 for a detail row, 1 when only `b` is rolled up, 2 when only `a` is, and 3 for the grand total. That makes a single sort key or a single level column:

```sql
SELECT region, category, SUM(amount) AS revenue,
       GROUPING(region, category) AS level
FROM sales
GROUP BY CUBE (region, category)
ORDER BY level, region, category;
```

```result
 region |  category   | revenue | level
--------+-------------+---------+-------
 APAC   | Electronics | 2000.00 |     0
 APAC   | Furniture   |  450.00 |     0
 EU     | Electronics | 1850.00 |     0
 EU     | Furniture   |  800.00 |     0
 US     | Clothing    |  270.00 |     0
 US     | Electronics | 2200.00 |     0
 US     | Furniture   |  350.00 |     0
 APAC   |             | 2450.00 |     1
 EU     |             | 2650.00 |     1
 US     |             | 2820.00 |     1
        | Clothing    |  270.00 |     2
        | Electronics | 6050.00 |     2
        | Furniture   | 1600.00 |     2
        |             | 7920.00 |     3
(14 rows)
```

Sorting by `GROUPING(...)` first also keeps the rows of each level together. Without an `ORDER BY`, the order of subtotal rows among detail rows is not defined.

## Filtering the subtotals

`HAVING` runs after the grouping sets have been built, and it can look at `GROUPING()`. This keeps the regions above 2,500 and the grand total:

```sql
SELECT region, SUM(amount) AS revenue
FROM sales
GROUP BY ROLLUP (region)
HAVING GROUPING(region) = 1 OR SUM(amount) > 2500
ORDER BY GROUPING(region), region;
```

```result
 region | revenue
--------+---------
 EU     | 2650.00
 US     | 2820.00
        | 7920.00
(3 rows)
```

Without the `GROUPING(region) = 1` condition, the grand total would only survive because 7,920 is also above 2,500, which is a coincidence rather than a rule.

## Percent of total: why the grand total shows 50%

A natural next step is a share-of-total column, with a window function over the grouped result:

```sql
SELECT region, category, SUM(amount) AS revenue,
       ROUND(100.0 * SUM(amount) / SUM(SUM(amount)) OVER (), 2) AS pct_of_total
FROM sales
GROUP BY GROUPING SETS ((region, category), ())
ORDER BY GROUPING(region), region, category;
```

```result
 region |  category   | revenue | pct_of_total
--------+-------------+---------+--------------
 APAC   | Electronics | 2000.00 |        12.63
 APAC   | Furniture   |  450.00 |         2.84
 EU     | Electronics | 1850.00 |        11.68
 EU     | Furniture   |  800.00 |         5.05
 US     | Clothing    |  270.00 |         1.70
 US     | Electronics | 2200.00 |        13.89
 US     | Furniture   |  350.00 |         2.21
        |             | 7920.00 |        50.00
(8 rows)
```

The grand total row says 50.00%, and the details add up to 50%. Window functions run after grouping, on the rows that grouping produced, and those rows include both the seven detail rows (7,920 together) and the grand total row (7,920). The `OVER ()` denominator is therefore 15,840.

Partition the window by `GROUPING(region)`, so the detail rows and the grand total row are each compared with their own level:

```sql
SELECT region, category, SUM(amount) AS revenue,
       ROUND(100.0 * SUM(amount) / SUM(SUM(amount)) OVER (PARTITION BY GROUPING(region)), 2) AS pct_of_total
FROM sales
GROUP BY GROUPING SETS ((region, category), ())
ORDER BY GROUPING(region), region, category;
```

```result
 region |  category   | revenue | pct_of_total
--------+-------------+---------+--------------
 APAC   | Electronics | 2000.00 |        25.25
 APAC   | Furniture   |  450.00 |         5.68
 EU     | Electronics | 1850.00 |        23.36
 EU     | Furniture   |  800.00 |        10.10
 US     | Clothing    |  270.00 |         3.41
 US     | Electronics | 2200.00 |        27.78
 US     | Furniture   |  350.00 |         4.42
        |             | 7920.00 |       100.00
(8 rows)
```

Now the detail rows add up to 100% and the grand total is 100%. With more than two levels, partition by `GROUPING(region, category)` so each level gets its own denominator.

## A dashboard query

Region and month, with details, region totals and a grand total. The labels come from `GROUPING()`:

```sql
SELECT CASE WHEN GROUPING(region) = 1 THEN '(all regions)' ELSE region END AS region,
       CASE WHEN GROUPING(month)  = 1 THEN '(all months)'  ELSE month  END AS month,
       SUM(amount) AS revenue,
       COUNT(*)    AS sales_count
FROM (SELECT region, TO_CHAR(sale_date, 'YYYY-MM') AS month, amount FROM sales) s
GROUP BY ROLLUP (region, month)
ORDER BY GROUPING(region), region, GROUPING(month), month;
```

```result
    region     |    month     | revenue | sales_count
---------------+--------------+---------+-------------
 APAC          | 2024-01      | 1200.00 |           1
 APAC          | 2024-02      |  800.00 |           1
 APAC          | 2024-03      |  450.00 |           1
 APAC          | (all months) | 2450.00 |           3
 EU            | 2024-01      | 1100.00 |           1
 EU            | 2024-02      |  750.00 |           1
 EU            | 2024-03      |  800.00 |           2
 EU            | (all months) | 2650.00 |           4
 US            | 2024-01      | 1300.00 |           1
 US            | 2024-02      |  900.00 |           1
 US            | 2024-03      |  620.00 |           3
 US            | (all months) | 2820.00 |           5
 (all regions) | (all months) | 7920.00 |          12
(13 rows)
```

The month is computed in a subquery so that `ROLLUP` and `GROUPING()` can refer to it by name.

## Other databases

- `ROLLUP`, `CUBE`, `GROUPING SETS` and `GROUPING()`: PostgreSQL (9.5+), SQL Server, Oracle and BigQuery have all of them. SQLite has none.
- MySQL has `ROLLUP` only, written `GROUP BY region, category WITH ROLLUP`, plus the `GROUPING()` function in 8.0. It has no `GROUPING SETS` and no `CUBE`, so those need `UNION ALL`.

## In short

- `GROUPING SETS` lists exactly the groupings you want. `ROLLUP (a, b, c)` is the hierarchy `(a,b,c)`, `(a,b)`, `(a)`, `()`. `CUBE (a, b, c)` is all 2^3 combinations.
- PostgreSQL reads the table once for all the sets (`MixedAggregate`), where `UNION ALL` reads it once per level.
- A `NULL` in a grouping column can be a subtotal marker or real data. Use `GROUPING()` to tell them apart and to sort levels.
- A window function over grouped output sees the subtotal rows too. Partition it by `GROUPING(...)` when you compute shares.
