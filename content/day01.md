---
day: 1
title: "SQL Has Three Truth Values, Not Two"
summary: "A comparison with NULL is neither true nor false but unknown, and WHERE throws unknown away. This is why != silently drops rows, and the three ways to keep them."
teaser: "WHERE col != 'x' drops the NULL rows, because a comparison with NULL is unknown, not false."
---

A `products` table where some items have a category and some do not:

```sql
CREATE TABLE products (
    id            INT,
    product_name  TEXT,
    category      TEXT   -- NULL means uncategorized
);

INSERT INTO products VALUES
    (1, 'Keyboard',   'Electronics'),
    (2, 'Desk',       'Furniture'),
    (3, 'USB Cable',  'Electronics'),
    (4, 'Notebook',   NULL),
    (5, 'Headphones', 'Electronics'),
    (6, 'Stapler',    NULL);
```

You want everything that is not furniture, so you write the obvious query:

```sql
SELECT product_name, category
FROM products
WHERE category != 'Furniture'
ORDER BY id;
```

```result
 product_name |  category
--------------+-------------
 Keyboard     | Electronics
 USB Cable    | Electronics
 Headphones   | Electronics
(3 rows)
```

Five products are not furniture, but the query returns three. Notebook and Stapler are missing because their category is `NULL`, and there is no error or warning. The queries here run on PostgreSQL 18, but the logic comes from the SQL standard and the same thing happens in MySQL, SQL Server and BigQuery.

## Three truth values

A comparison in SQL can be `TRUE`, `FALSE` or `UNKNOWN`. Comparing anything with `NULL`, including another `NULL`, gives `UNKNOWN`, because `NULL` means "no value here" and the database cannot say whether a missing value equals `'Furniture'`.

`WHERE` keeps a row only when its condition is `TRUE`. `FALSE` and `UNKNOWN` both discard the row. For this table:

| Expression | Result | Row |
| --- | --- | --- |
| `'Furniture' != 'Furniture'` | `FALSE` | dropped |
| `'Electronics' != 'Furniture'` | `TRUE` | kept |
| `NULL != 'Furniture'` | `UNKNOWN` | dropped |

PostgreSQL prints `UNKNOWN` as an empty cell, the same as `NULL`:

```sql
SELECT 'Electronics' != 'Furniture' AS electronics,
       NULL          != 'Furniture' AS missing_category;
```

```result
 electronics | missing_category
-------------+------------------
 t           |
(1 row)
```

## NOT does not rescue you

If `category = 'Furniture'` is unknown for the `NULL` rows, it is tempting to think `NOT (category = 'Furniture')` flips it to true. It does not: `NOT UNKNOWN` is `UNKNOWN`.

```sql
SELECT product_name, category
FROM products
WHERE NOT (category = 'Furniture')
ORDER BY id;
```

```result
 product_name |  category
--------------+-------------
 Keyboard     | Electronics
 USB Cable    | Electronics
 Headphones   | Electronics
(3 rows)
```

`AND` and `OR` follow the same idea: the result is `UNKNOWN` only when the known operand does not already settle it. `FALSE AND UNKNOWN` is `FALSE`, because no value of the unknown could make the `AND` true. `TRUE OR UNKNOWN` is `TRUE` for the same reason. All nine combinations:

```sql
SELECT a, b, a AND b AS a_and_b, a OR b AS a_or_b
FROM (VALUES (TRUE), (FALSE), (NULL::boolean)) AS x(a)
CROSS JOIN (VALUES (TRUE), (FALSE), (NULL::boolean)) AS y(b)
ORDER BY a DESC NULLS LAST, b DESC NULLS LAST;
```

```result
 a | b | a_and_b | a_or_b
---+---+---------+--------
 t | t | t       | t
 t | f | f       | t
 t |   |         | t
 f | t | f       | t
 f | f | f       | f
 f |   | f       |
   | t |         | t
   | f | f       |
   |   |         |
(9 rows)
```

In a report over millions of rows the damage is quiet: totals come out low, there is no error, and the problem surfaces only when someone compares the numbers with another source.

## Keeping the NULL rows

SQL does not guess what you meant, so you have to say what should happen to the missing category. There are three usual ways.

Test for `NULL` explicitly:

```sql
SELECT product_name, category
FROM products
WHERE category != 'Furniture' OR category IS NULL
ORDER BY id;
```

Use `IS DISTINCT FROM`, the standard operator that treats `NULL` as an ordinary value and so always returns `TRUE` or `FALSE`:

```sql
SELECT product_name, category
FROM products
WHERE category IS DISTINCT FROM 'Furniture'
ORDER BY id;
```

Test the comparison itself with `IS NOT TRUE`. It is `TRUE` for both `FALSE` and `UNKNOWN`, so the `NULL` rows pass:

```sql
SELECT product_name, category
FROM products
WHERE (category = 'Furniture') IS NOT TRUE
ORDER BY id;
```

All three return the five rows you expected:

```result
 product_name |  category
--------------+-------------
 Keyboard     | Electronics
 USB Cable    | Electronics
 Notebook     |
 Headphones   | Electronics
 Stapler      |
(5 rows)
```

MySQL has no `IS DISTINCT FROM`; its null-safe equality operator is `<=>`, so the equivalent there is `NOT (category <=> 'Furniture')`.

### COALESCE, with a catch

`COALESCE` swaps `NULL` for a placeholder just for the comparison:

```sql
SELECT product_name, category
FROM products
WHERE COALESCE(category, 'Unknown') != 'Furniture'
ORDER BY id;
```

```result
 product_name |  category
--------------+-------------
 Keyboard     | Electronics
 USB Cable    | Electronics
 Notebook     |
 Headphones   | Electronics
 Stapler      |
(5 rows)
```

The placeholder must be a value that does not occur in the data, and it must not be the value you are filtering on. With `COALESCE(category, 'Furniture') != 'Furniture'` the `NULL` rows become `'Furniture'`, the comparison is `FALSE`, and you are back to three rows. `COALESCE` also hides the intent, so I prefer `IS DISTINCT FROM`.

## Comparing NULL with NULL

Two `NULL`s are not equal to each other, and they are not different either. Both comparisons are unknown:

```sql
SELECT NULL = NULL                AS is_equal,
       NULL != NULL               AS is_not_equal,
       NULL IS NULL               AS is_null,
       NULL IS DISTINCT FROM NULL AS distinct_from;
```

```result
 is_equal | is_not_equal | is_null | distinct_from
----------+--------------+---------+---------------
          |              | t       | f
(1 row)
```

To test for a missing value, use `IS NULL` or `IS NOT NULL`. To compare two columns that may both be `NULL` and count two `NULL`s as a match, use `IS NOT DISTINCT FROM`.

## Not every part of SQL discards UNKNOWN

`WHERE` treats `UNKNOWN` like `FALSE`, but a `CHECK` constraint treats it like `TRUE`: a row is rejected only when the check evaluates to `FALSE`.

```sql
CREATE TABLE prices (
    product_id  INT,
    price       NUMERIC CHECK (price > 0)
);

INSERT INTO prices VALUES (1, 9.99);
INSERT INTO prices VALUES (2, NULL);
```

Both inserts succeed. A `price` of `NULL` passes `CHECK (price > 0)`, so a check constraint is no substitute for `NOT NULL`:

```sql
SELECT product_id, price FROM prices ORDER BY product_id;
```

```result
 product_id | price
------------+-------
          1 |  9.99
          2 |
(2 rows)
```

## Finding the problem in real data

Before writing `!=` or `NOT IN` against a column, count how many rows hold `NULL` in it:

```sql
SELECT COUNT(*)                                  AS total_rows,
       COUNT(*) FILTER (WHERE category IS NULL)  AS null_category
FROM products;
```

```result
 total_rows | null_category
------------+---------------
          6 |             2
(1 row)
```

If the second number is not zero, decide what those rows should mean in the query. The same logic breaks `NOT IN (subquery)` when the subquery returns a `NULL`: the query returns no rows at all. Day 22 covers that.

## In short

- A comparison with `NULL` is `UNKNOWN`, and `WHERE` keeps only `TRUE`. `!=` and `NOT (...)` therefore drop the `NULL` rows.
- To keep them, use an explicit `IS NULL`, `IS DISTINCT FROM`, or `IS NOT TRUE` on the comparison.
- Test for `NULL` with `IS NULL`, never `= NULL`.
- `CHECK` constraints accept `UNKNOWN`, so they do not stop `NULL`s.
- `COUNT(*)` and `COUNT(column)` also disagree about `NULL`s: for `category` above they give 6 and 4. Day 2 is about that.
