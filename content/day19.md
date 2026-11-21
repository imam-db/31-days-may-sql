---
day: 19
title: "Window Functions Deep Dive: ROWS vs RANGE vs GROUPS"
summary: "SUM(...) OVER (ORDER BY date) does not give a row-by-row running total when dates repeat, because the default frame is RANGE. How ROWS, RANGE and GROUPS differ, with offsets, EXCLUDE, LAST_VALUE and the errors you will hit."
teaser: "Why a running total jumps on repeated dates, and how ROWS, RANGE and GROUPS differ."
---

A running total written as `SUM(amount) OVER (ORDER BY sale_date)` looks right until the same date appears twice. Then several rows show the same total, and each total is higher than the row-by-row sum up to that row. The data is fine. The window frame is doing what the SQL standard says, just not what most people assume.

The examples run on PostgreSQL 18. `GROUPS` needs PostgreSQL 11 or later.

## The data

```sql
CREATE TABLE sales (
    sale_id   INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sale_date DATE NOT NULL,
    amount    NUMERIC(10,2) NOT NULL
);

INSERT INTO sales (sale_date, amount) VALUES
    ('2024-01-01', 100),
    ('2024-01-02', 200),
    ('2024-01-02', 300),   -- same day as the previous row
    ('2024-01-02', 150),   -- and again
    ('2024-01-03', 500),
    ('2024-01-04', 250),
    ('2024-01-04', 350);   -- same day as the previous row
```

Three sales happened on January 2nd and two on January 4th.

## The surprise

```sql
SELECT sale_id, sale_date, amount,
       SUM(amount) OVER (ORDER BY sale_date) AS running_total
FROM sales
ORDER BY sale_id;
```

```result
 sale_id | sale_date  | amount | running_total
---------+------------+--------+---------------
       1 | 2024-01-01 | 100.00 |        100.00
       2 | 2024-01-02 | 200.00 |        750.00
       3 | 2024-01-02 | 300.00 |        750.00
       4 | 2024-01-02 | 150.00 |        750.00
       5 | 2024-01-03 | 500.00 |       1250.00
       6 | 2024-01-04 | 250.00 |       1850.00
       7 | 2024-01-04 | 350.00 |       1850.00
(7 rows)
```

Rows 2, 3 and 4 all show 750.00, and rows 6 and 7 both show 1850.00. The row-by-row running total would be 100, 300, 600, 750, 1250, 1500, 1850. Each row instead gets the total up to and including its whole date, so the three rows of January 2nd all see 100 + 200 + 300 + 150.

## Why: the default frame is RANGE

A window frame says which rows, relative to the current row, the function looks at. When the window has an `ORDER BY` and no frame clause, the standard default is:

```sql norun
RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
```

`CURRENT ROW` in `RANGE` mode means the current row and every row with the same `ORDER BY` value, which the standard calls its peers. All peers share one frame end, so they get the same result. Without an `ORDER BY` the frame is the whole partition. The two spellings give identical results here, which you can verify by comparing them:

```sql
SELECT sale_id, SUM(amount) OVER (ORDER BY sale_date) AS implicit_frame
FROM sales
EXCEPT
SELECT sale_id, SUM(amount) OVER (ORDER BY sale_date RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
FROM sales;
```

```result
 sale_id | implicit_frame
---------+----------------
(0 rows)
```

No rows differ.

## The three frame modes

The mode decides what the frame boundaries count:

- `ROWS` counts physical rows. `2 PRECEDING` is the two rows before this one in the window's order.
- `RANGE` compares values of the `ORDER BY` column. `INTERVAL '1 day' PRECEDING` is every row whose date is within one day before this row's date. `CURRENT ROW` includes all peers.
- `GROUPS` counts peer groups, meaning sets of rows with the same `ORDER BY` value. `1 PRECEDING` is the previous distinct date, however many rows it has and however far back it is.

Here are the three with `UNBOUNDED PRECEDING ... CURRENT ROW`. For `ROWS` the order has to be made unique, here with `sale_id`, because with ties the database is free to order the rows of a date any way it likes, and the totals within January 2nd would change from run to run:

```sql
SELECT sale_id, sale_date, amount,
       SUM(amount) OVER (ORDER BY sale_date, sale_id
                         ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS sum_rows,
       SUM(amount) OVER (ORDER BY sale_date
                         RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS sum_range,
       SUM(amount) OVER (ORDER BY sale_date
                         GROUPS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS sum_groups
FROM sales
ORDER BY sale_id;
```

```result
 sale_id | sale_date  | amount | sum_rows | sum_range | sum_groups
---------+------------+--------+----------+-----------+------------
       1 | 2024-01-01 | 100.00 |   100.00 |    100.00 |     100.00
       2 | 2024-01-02 | 200.00 |   300.00 |    750.00 |     750.00
       3 | 2024-01-02 | 300.00 |   600.00 |    750.00 |     750.00
       4 | 2024-01-02 | 150.00 |   750.00 |    750.00 |     750.00
       5 | 2024-01-03 | 500.00 |  1250.00 |   1250.00 |    1250.00
       6 | 2024-01-04 | 250.00 |  1500.00 |   1850.00 |    1850.00
       7 | 2024-01-04 | 350.00 |  1850.00 |   1850.00 |    1850.00
(7 rows)
```

`ROWS` is the row-by-row total. `RANGE` and `GROUPS` agree here, because with an unbounded start and the end at the last peer, counting groups and comparing values select the same rows. They differ once the frame has a numeric offset, as shown below.

For a running total, either use `ROWS`, or give the `ORDER BY` a unique tie-breaker, which leaves no peers and so makes `RANGE` behave like `ROWS`:

```sql
SELECT sale_id, sale_date, amount,
       SUM(amount) OVER (ORDER BY sale_date, sale_id) AS running_total
FROM sales
ORDER BY sale_id;
```

```result
 sale_id | sale_date  | amount | running_total
---------+------------+--------+---------------
       1 | 2024-01-01 | 100.00 |        100.00
       2 | 2024-01-02 | 200.00 |        300.00
       3 | 2024-01-02 | 300.00 |        600.00
       4 | 2024-01-02 | 150.00 |        750.00
       5 | 2024-01-03 | 500.00 |       1250.00
       6 | 2024-01-04 | 250.00 |       1500.00
       7 | 2024-01-04 | 350.00 |       1850.00
(7 rows)
```

I still spell out `ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW` in running totals. It says what is meant, and it avoids the peer lookup of `RANGE`.

## Offsets

### ROWS: a moving average over a fixed number of rows

The average of the current row and the two before it, in order of `sale_id`:

```sql
SELECT sale_id, amount,
       ROUND(AVG(amount) OVER (ORDER BY sale_id
                               ROWS BETWEEN 2 PRECEDING AND CURRENT ROW), 2) AS avg_last_3
FROM sales
ORDER BY sale_id;
```

```result
 sale_id | amount | avg_last_3
---------+--------+------------
       1 | 100.00 |     100.00
       2 | 200.00 |     150.00
       3 | 300.00 |     200.00
       4 | 150.00 |     216.67
       5 | 500.00 |     316.67
       6 | 250.00 |     300.00
       7 | 350.00 |     366.67
(7 rows)
```

The first two rows average over fewer than three, because the frame is cut off at the start of the partition.

### RANGE: a window defined by values

Sales within one day before the current row's date, including the current date:

```sql
SELECT sale_id, sale_date, amount,
       SUM(amount) OVER (ORDER BY sale_date
                         RANGE BETWEEN INTERVAL '1 day' PRECEDING AND CURRENT ROW) AS sum_1_day_back
FROM sales
ORDER BY sale_id;
```

```result
 sale_id | sale_date  | amount | sum_1_day_back
---------+------------+--------+----------------
       1 | 2024-01-01 | 100.00 |         100.00
       2 | 2024-01-02 | 200.00 |         750.00
       3 | 2024-01-02 | 300.00 |         750.00
       4 | 2024-01-02 | 150.00 |         750.00
       5 | 2024-01-03 | 500.00 |        1150.00
       6 | 2024-01-04 | 250.00 |        1100.00
       7 | 2024-01-04 | 350.00 |        1100.00
(7 rows)
```

Row 5 (January 3rd) covers January 2nd (650) and the 3rd (500), 1150 in total. If 50 sales had been made on the 2nd, all 50 would count, and the frame would still be correct. A `ROWS` frame cannot express "the previous day" when the number of rows per day varies.

The offset has to match the type of the `ORDER BY` column. For a `DATE` column it is an interval; `RANGE BETWEEN 1 PRECEDING` on a date fails with `RANGE with offset PRECEDING/FOLLOWING is not supported for column type date and offset type integer`. `RANGE` with an offset also needs exactly one `ORDER BY` column, and the column must be numeric or a date/time type, so a text column does not qualify.

### GROUPS: a window counted in distinct values

`GROUPS` is the same idea as `RANGE` but counts groups instead of measuring distance. The two give the same answer while there are no gaps between dates. Add a sale on January 7th, three days after the last one, and they separate:

```sql
INSERT INTO sales (sale_date, amount) VALUES ('2024-01-07', 400);

SELECT sale_id, sale_date, amount,
       SUM(amount) OVER (ORDER BY sale_date
                         RANGE BETWEEN INTERVAL '1 day' PRECEDING AND CURRENT ROW) AS range_1_day,
       SUM(amount) OVER (ORDER BY sale_date
                         GROUPS BETWEEN 1 PRECEDING AND CURRENT ROW) AS groups_1
FROM sales
ORDER BY sale_id;
```

```result
 sale_id | sale_date  | amount | range_1_day | groups_1
---------+------------+--------+-------------+----------
       1 | 2024-01-01 | 100.00 |      100.00 |   100.00
       2 | 2024-01-02 | 200.00 |      750.00 |   750.00
       3 | 2024-01-02 | 300.00 |      750.00 |   750.00
       4 | 2024-01-02 | 150.00 |      750.00 |   750.00
       5 | 2024-01-03 | 500.00 |     1150.00 |  1150.00
       6 | 2024-01-04 | 250.00 |     1100.00 |  1100.00
       7 | 2024-01-04 | 350.00 |     1100.00 |  1100.00
       8 | 2024-01-07 | 400.00 |      400.00 |  1000.00
(8 rows)
```

For the sale on the 7th, `RANGE` looks for dates from January 6th to the 7th and finds only itself (400). `GROUPS` takes the previous distinct date that exists in the data, January 4th, plus the 7th: 600 + 400 = 1000. Use `RANGE` when the question is about elapsed time, and `GROUPS` when it is about "the last N distinct values that actually occur", for instance the last two trading days in a table that has no rows for weekends. `GROUPS` also works on an `ORDER BY` column that is not numeric at all, since it never does arithmetic on it.

### The frames side by side

For the sales on January 2nd and 3rd (rows 2 to 5), which sale ids does each frame include?

| Row | ROWS 1 PRECEDING (ordered by id) | RANGE 1 day PRECEDING | GROUPS 1 PRECEDING |
| --- | --- | --- | --- |
| 1 (Jan 1) | 1 | 1 | 1 |
| 2 (Jan 2) | 1, 2 | 1, 2, 3, 4 | 1, 2, 3, 4 |
| 3 (Jan 2) | 2, 3 | 1, 2, 3, 4 | 1, 2, 3, 4 |
| 4 (Jan 2) | 3, 4 | 1, 2, 3, 4 | 1, 2, 3, 4 |
| 5 (Jan 3) | 4, 5 | 2, 3, 4, 5 | 2, 3, 4, 5 |

`ROWS` moves one physical row at a time. `RANGE` and `GROUPS` move in whole dates, so the three rows of January 2nd always have the same frame.

## EXCLUDE

PostgreSQL 11 and later support an `EXCLUDE` clause on the frame. `EXCLUDE GROUP` removes the current row and all its peers, which gives "everything from earlier dates":

```sql
SELECT sale_id, sale_date, amount,
       SUM(amount) OVER (ORDER BY sale_date
                         RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                         EXCLUDE GROUP) AS before_this_date
FROM sales
ORDER BY sale_id;
```

```result
 sale_id | sale_date  | amount | before_this_date
---------+------------+--------+------------------
       1 | 2024-01-01 | 100.00 |
       2 | 2024-01-02 | 200.00 |           100.00
       3 | 2024-01-02 | 300.00 |           100.00
       4 | 2024-01-02 | 150.00 |           100.00
       5 | 2024-01-03 | 500.00 |           750.00
       6 | 2024-01-04 | 250.00 |          1250.00
       7 | 2024-01-04 | 350.00 |          1250.00
       8 | 2024-01-07 | 400.00 |          1850.00
(8 rows)
```

The result is `NULL` for the first date because nothing comes before it. The other options are `EXCLUDE CURRENT ROW`, which removes only the row itself, and `EXCLUDE TIES`, which removes the peers but keeps the current row.

## Functions and the frame

Not every window function looks at the frame.

`LAG`, `LEAD`, `ROW_NUMBER`, `RANK` and `NTILE` ignore it. They depend only on the order of the rows in the partition.

The aggregates (`SUM`, `AVG`, `COUNT`, `MIN`, `MAX`) and `FIRST_VALUE`, `LAST_VALUE` and `NTH_VALUE` use it. `LAST_VALUE` is the one that bites. Its name suggests the last row of the partition, but with the default frame it returns the last row of the frame, which ends at the current row:

```sql
SELECT sale_id, sale_date, amount,
       LAST_VALUE(amount) OVER (ORDER BY sale_date, sale_id) AS last_value_default,
       LAST_VALUE(amount) OVER (ORDER BY sale_date, sale_id
                                ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) AS last_value_whole_partition
FROM sales
ORDER BY sale_id;
```

```result
 sale_id | sale_date  | amount | last_value_default | last_value_whole_partition
---------+------------+--------+--------------------+----------------------------
       1 | 2024-01-01 | 100.00 |             100.00 |                     400.00
       2 | 2024-01-02 | 200.00 |             200.00 |                     400.00
       3 | 2024-01-02 | 300.00 |             300.00 |                     400.00
       4 | 2024-01-02 | 150.00 |             150.00 |                     400.00
       5 | 2024-01-03 | 500.00 |             500.00 |                     400.00
       6 | 2024-01-04 | 250.00 |             250.00 |                     400.00
       7 | 2024-01-04 | 350.00 |             350.00 |                     400.00
       8 | 2024-01-07 | 400.00 |             400.00 |                     400.00
(8 rows)
```

`last_value_default` is just the row's own amount. To get the last row of the partition, extend the frame to `UNBOUNDED FOLLOWING`, as in the last column. When the `ORDER BY` has ties, the default frame ends at the last peer instead, and which of the peers is the last one is not defined.

## Support in other databases

`ROWS` and `RANGE` with `UNBOUNDED` and `CURRENT ROW` bounds are widely supported. The differences are in the rest:

- `RANGE` with a numeric or interval offset: PostgreSQL and MySQL 8 support it. SQL Server accepts only `UNBOUNDED` and `CURRENT ROW` for `RANGE`.
- `GROUPS`: supported in PostgreSQL 11+ and SQLite 3.28+. MySQL 8 and SQL Server do not have it.

If you need `GROUPS` behaviour elsewhere, one workaround is to `DENSE_RANK()` the `ORDER BY` column in a subquery and then use `RANGE` or `ROWS` on that rank.

## In short

- `SUM(...) OVER (ORDER BY x)` uses `RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW`, so rows with the same `x` get the same total.
- For a row-by-row running total, write `ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW` and make the `ORDER BY` unique so the tie order is defined.
- `RANGE` windows are measured in value (days, amounts); `GROUPS` windows are counted in distinct values; they differ as soon as the data has gaps.
- `LAST_VALUE` with the default frame returns the current row or its last peer, not the end of the partition.
