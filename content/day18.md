---
day: 18
title: "Fibonacci and Mathematical Sequences via Recursive CTEs"
summary: "A recursive CTE is a loop with state: Fibonacci, factorials, compound interest, Collatz, calendars and an amortization schedule, plus how to stop runaway recursion and when generate_series is the better tool."
teaser: "Recursive CTEs as loops: Fibonacci, compound interest, an amortization schedule."
---

Recursive CTEs are usually taught with org charts and bill-of-materials trees. Underneath, a recursive CTE is a loop: it starts from some state, computes the next state from the current one, and stops when a condition fails. Nothing in that needs a table. The examples here use it to generate data from scratch: sequences, a loan schedule, a calendar.

All queries run on PostgreSQL 18 and need no tables unless stated. Recursive CTEs are standard SQL and exist in MySQL 8, SQLite and SQL Server too; the syntax differences are listed near the end.

## The shape of a recursive CTE

Every example below follows the same template:

```sql norun
WITH RECURSIVE name AS (
    SELECT initial_state          -- anchor: runs once

    UNION ALL

    SELECT next_state(name)       -- recursive step: runs on the previous iteration's rows
    FROM name
    WHERE keep_going              -- the only thing that stops it
)
SELECT * FROM name;
```

The anchor produces the first row. The recursive step reads the rows produced by the previous iteration and produces the next ones. When an iteration returns no rows, the recursion ends. A Python loop that updates variables maps directly onto it: the variables become columns.

## Fibonacci

Each number is the sum of the two before it: 0, 1, 1, 2, 3, 5, 8. In Python:

```text
a, b = 0, 1
for i in range(20):
    print(a)
    a, b = b, a + b
```

The SQL version carries `a` and `b` as two columns and shifts them on every iteration:

```sql
WITH RECURSIVE fib AS (
    SELECT 0 AS n, 0::BIGINT AS fib_n, 1::BIGINT AS fib_next

    UNION ALL

    SELECT n + 1, fib_next, fib_n + fib_next
    FROM fib
    WHERE n < 19
)
SELECT n, fib_n AS fibonacci
FROM fib
ORDER BY n;
```

```result
 n  | fibonacci
----+-----------
  0 |         0
  1 |         1
  2 |         1
  3 |         2
  4 |         3
  5 |         5
  6 |         8
  7 |        13
  8 |        21
  9 |        34
 10 |        55
 11 |        89
 12 |       144
 13 |       233
 14 |       377
 15 |       610
 16 |       987
 17 |      1597
 18 |      2584
 19 |      4181
(20 rows)
```

`fib_next` is the number one step ahead of `fib_n`, which is what `a, b = b, a + b` does. The `WHERE n < 19` stops the loop after the row with `n = 19`, which gives 20 rows.

The two columns are typed `BIGINT` on purpose. In a recursive CTE the column types are fixed by the anchor, and a plain `0` would make them `integer`, which overflow at the 47th number. With `BIGINT` the limit is the 93rd number, which no longer fits in 64 bits. The CTE does not widen types for you:

```sql norun
-- same query with WHERE n < 93
ERROR:  bigint out of range
```

For larger values, start from `NUMERIC` and the same query runs as far as you need:

```sql
WITH RECURSIVE fib AS (
    SELECT 0 AS n, 0::NUMERIC AS fib_n, 1::NUMERIC AS fib_next
    UNION ALL
    SELECT n + 1, fib_next, fib_n + fib_next
    FROM fib
    WHERE n < 100
)
SELECT n, fib_n
FROM fib
WHERE n IN (50, 92, 93, 100)
ORDER BY n;
```

```result
  n  |         fib_n
-----+-----------------------
  50 |           12586269025
  92 |   7540113804746346429
  93 |  12200160415121876738
 100 | 354224848179261915075
(4 rows)
```

## Numeric sequences

Numbers from 1 to 10, then only the even ones, by changing the start and the step:

```sql
WITH RECURSIVE numbers AS (
    SELECT 1 AS n
    UNION ALL
    SELECT n + 1 FROM numbers WHERE n < 10
)
SELECT n FROM numbers ORDER BY n;
```

```result
 n
----
  1
  2
  3
  4
  5
  6
  7
  8
  9
 10
(10 rows)
```

Powers of 2 show exponential growth, and why the data type matters: `value * 2` would overflow `integer` at 2^31, so the anchor is `BIGINT`.

```sql
WITH RECURSIVE powers AS (
    SELECT 0 AS exponent, 1::BIGINT AS value
    UNION ALL
    SELECT exponent + 1, value * 2
    FROM powers
    WHERE exponent < 40
)
SELECT exponent, value
FROM powers
WHERE exponent IN (0, 1, 2, 3, 10, 20, 30, 40)
ORDER BY exponent;
```

```result
 exponent |     value
----------+---------------
        0 |             1
        1 |             2
        2 |             4
        3 |             8
       10 |          1024
       20 |       1048576
       30 |    1073741824
       40 | 1099511627776
(8 rows)
```

## Factorials

`n! = n × (n-1) × ... × 1`. The running product is the state:

```sql
WITH RECURSIVE factorial AS (
    SELECT 1 AS n, 1::BIGINT AS fact
    UNION ALL
    SELECT n + 1, fact * (n + 1)
    FROM factorial
    WHERE n < 20
)
SELECT n, fact
FROM factorial
WHERE n IN (1, 2, 3, 4, 5, 10, 15, 20)
ORDER BY n;
```

```result
 n  |        fact
----+---------------------
  1 |                   1
  2 |                   2
  3 |                   6
  4 |                  24
  5 |                 120
 10 |             3628800
 15 |       1307674368000
 20 | 2432902008176640000
(8 rows)
```

20! is the largest factorial that fits in a `BIGINT`. Change the limit to 21 and the query fails with `bigint out of range`. Use `NUMERIC` as the anchor type for bigger values.

## Compound interest

If you invest 10,000 at 7% a year, what is the balance after 30 years? Rounding to cents each year, as a bank statement would:

```sql
WITH RECURSIVE investment AS (
    SELECT 0 AS year, 10000.00::NUMERIC AS balance
    UNION ALL
    SELECT year + 1, ROUND(balance * 1.07, 2)
    FROM investment
    WHERE year < 30
)
SELECT year, balance
FROM investment
WHERE year IN (0, 1, 2, 5, 10, 20, 30)
ORDER BY year;
```

```result
 year | balance
------+----------
    0 | 10000.00
    1 | 10700.00
    2 | 11449.00
    5 | 14025.52
   10 | 19671.52
   20 | 38696.87
   30 | 76122.60
(7 rows)
```

The closed form `10000 * 1.07^30` gives 76122.55. The loop's 76122.60 is 5 cents higher because the balance is rounded to cents every year, which is also what a real statement does. The loop earns its place when the closed form does not exist: a contribution every year, a rate that changes, a withdrawal. Adding 500 at the end of each year is one more term in the recursive step:

```sql
WITH RECURSIVE investment AS (
    SELECT 0 AS year, 10000.00::NUMERIC AS balance
    UNION ALL
    SELECT year + 1, ROUND(balance * 1.07 + 500, 2)
    FROM investment
    WHERE year < 30
)
SELECT balance AS balance_after_30_years_with_contributions
FROM investment
WHERE year = 30;
```

```result
 balance_after_30_years_with_contributions
-------------------------------------------
                                 123352.94
(1 row)
```

## Collatz: a loop with no known end

Start with any positive integer. If it is even, halve it; if it is odd, triple it and add 1. The Collatz conjecture says every start eventually reaches 1, and nobody has proved that. For 27:

```sql
WITH RECURSIVE collatz AS (
    SELECT 27::BIGINT AS n, 0 AS step
    UNION ALL
    SELECT CASE WHEN n % 2 = 0 THEN n / 2 ELSE n * 3 + 1 END, step + 1
    FROM collatz
    WHERE n <> 1
)
SELECT max(step) AS steps_to_reach_1, max(n) AS highest_value
FROM collatz;
```

```result
 steps_to_reach_1 | highest_value
------------------+---------------
              111 |          9232
(1 row)
```

It takes 111 steps and climbs as high as 9,232 before falling to 1. The stopping condition `n <> 1` is only safe because every start tried so far does reach 1. For a sequence where termination is not guaranteed, add a second guard such as `AND step < 1000`.

## Calendars and filling gaps

A recursive CTE can produce a run of dates. Here is June 2024 with the daily sales that exist, where the days without sales still show up with zero:

```sql
CREATE TABLE daily_sales (sale_date DATE PRIMARY KEY, amount NUMERIC(10,2) NOT NULL);

INSERT INTO daily_sales VALUES
    ('2024-06-01', 120.00),
    ('2024-06-02',  80.50),
    ('2024-06-05', 200.00),
    ('2024-06-06',  45.25);

WITH RECURSIVE calendar AS (
    SELECT DATE '2024-06-01' AS dt
    UNION ALL
    SELECT dt + 1
    FROM calendar
    WHERE dt < DATE '2024-06-07'
)
SELECT c.dt,
       to_char(c.dt, 'FMDay') AS weekday,
       COALESCE(s.amount, 0) AS amount
FROM calendar c
LEFT JOIN daily_sales s ON s.sale_date = c.dt
ORDER BY c.dt;
```

```result
     dt     |  weekday  | amount
------------+-----------+--------
 2024-06-01 | Saturday  | 120.00
 2024-06-02 | Sunday    |  80.50
 2024-06-03 | Monday    |      0
 2024-06-04 | Tuesday   |      0
 2024-06-05 | Wednesday | 200.00
 2024-06-06 | Thursday  |  45.25
 2024-06-07 | Friday    |      0
(7 rows)
```

Adding 1 to a `DATE` adds one day in PostgreSQL. In MySQL you would write `dt + INTERVAL 1 DAY`, and in SQL Server `DATEADD(day, 1, dt)`.

In PostgreSQL there is a built-in for this, and it is the better choice for plain runs of numbers or dates:

```sql
SELECT d::DATE AS dt
FROM generate_series(DATE '2024-06-01', DATE '2024-06-07', INTERVAL '1 day') AS d
ORDER BY dt;
```

```result
     dt
------------
 2024-06-01
 2024-06-02
 2024-06-03
 2024-06-04
 2024-06-05
 2024-06-06
 2024-06-07
(7 rows)
```

The recursive version matters when the database has no such function, or when the next value depends on the previous one in a way `generate_series` cannot express. The plans show why the built-in is cheaper: it is one function call, while the CTE runs its recursive step once per row.

```sql
EXPLAIN (COSTS OFF)
WITH RECURSIVE numbers AS (
    SELECT 1 AS n
    UNION ALL
    SELECT n + 1 FROM numbers WHERE n < 1000000
)
SELECT n FROM numbers;
```

```result
                    QUERY PLAN
---------------------------------------------------
 CTE Scan on numbers
   CTE numbers
     ->  Recursive Union
           ->  Result
           ->  WorkTable Scan on numbers numbers_1
                 Filter: (n < 1000000)
(6 rows)
```

```sql
EXPLAIN (COSTS OFF)
SELECT n FROM generate_series(1, 1000000) AS n;
```

```result
             QUERY PLAN
------------------------------------
 Function Scan on generate_series n
(1 row)
```

## Splitting a string without a split function

In PostgreSQL you would use `string_to_table`, but the recursive version works anywhere that has `POSITION` and `SUBSTRING`, and it shows state being passed along. Each iteration peels the first item off `remaining`:

```sql
WITH RECURSIVE split AS (
    SELECT 0 AS position,
           NULL::TEXT AS item,
           'apple,banana,cherry,date,elderberry'::TEXT AS remaining

    UNION ALL

    SELECT position + 1,
           CASE WHEN POSITION(',' IN remaining) > 0
                THEN SUBSTRING(remaining FROM 1 FOR POSITION(',' IN remaining) - 1)
                ELSE remaining END,
           CASE WHEN POSITION(',' IN remaining) > 0
                THEN SUBSTRING(remaining FROM POSITION(',' IN remaining) + 1)
                ELSE '' END
    FROM split
    WHERE remaining <> ''
)
SELECT position, item
FROM split
WHERE position > 0
ORDER BY position;
```

```result
 position |    item
----------+------------
        1 | apple
        2 | banana
        3 | cherry
        4 | date
        5 | elderberry
(5 rows)
```

The loop ends when `remaining` is empty. That also means a trailing delimiter is lost: for `'a,b,'` the last, empty item never appears, because the loop stops as soon as nothing is left. If empty items matter, you need a different stopping rule, for example a flag column.

## Loan amortization

A fixed-rate loan is a good fit because each month's interest depends on the balance left by the previous month. A 300,000 loan at 6.5% a year over 360 months has a payment of 1,896.20, rounded to cents. Interest is also rounded each month, as a lender would do, and that leaves a few cents of drift, so the last payment is adjusted to clear the balance exactly:

```sql
WITH RECURSIVE terms AS (
    SELECT 300000.00::NUMERIC AS principal,
           0.065 / 12         AS monthly_rate,
           360                AS months,
           ROUND(300000.00 * (0.065 / 12) * POWER(1 + 0.065 / 12, 360)
                 / (POWER(1 + 0.065 / 12, 360) - 1), 2) AS payment
),
schedule AS (
    SELECT 0 AS month, principal AS balance,
           0.00::NUMERIC AS paid, 0.00::NUMERIC AS interest, 0.00::NUMERIC AS principal_paid
    FROM terms

    UNION ALL

    SELECT s.month + 1,
           s.balance - (p.paid - i.interest),
           p.paid,
           i.interest,
           p.paid - i.interest
    FROM schedule s
    CROSS JOIN terms t
    CROSS JOIN LATERAL (SELECT ROUND(s.balance * t.monthly_rate, 2) AS interest) i
    CROSS JOIN LATERAL (SELECT CASE WHEN s.month + 1 = t.months
                                    THEN s.balance + i.interest   -- last payment clears the balance
                                    ELSE t.payment END AS paid) p
    WHERE s.month < t.months
)
SELECT month, balance, paid, principal_paid, interest
FROM schedule
WHERE month % 60 = 0 OR month IN (1, 359)
ORDER BY month;
```

```result
 month |  balance  |  paid   | principal_paid | interest
-------+-----------+---------+----------------+----------
     0 | 300000.00 |    0.00 |           0.00 |     0.00
     1 | 299728.80 | 1896.20 |         271.20 |  1625.00
    60 | 280833.26 | 1896.20 |         373.00 |  1523.20
   120 | 254329.14 | 1896.20 |         515.79 |  1380.41
   180 | 217678.77 | 1896.20 |         713.24 |  1182.96
   240 | 166997.98 | 1896.20 |         986.29 |   909.91
   300 |  96915.68 | 1896.20 |        1363.85 |   532.35
   359 |   1890.67 | 1896.20 |        1875.80 |    20.40
   360 |      0.00 | 1900.91 |        1890.67 |    10.24
(9 rows)
```

The loan is repaid after exactly 360 rows. The regular payment is 1,896.20 and the final one is 1,900.91, because the unrounded payment is 1,896.2041 and 360 small roundings add up to a few cents. The first month shows the usual shape of an annuity loan: 1,625.00 of the first payment is interest and only 271.20 reduces the principal. The totals from the whole schedule:

```sql
WITH RECURSIVE terms AS (
    SELECT 300000.00::NUMERIC AS principal,
           0.065 / 12         AS monthly_rate,
           360                AS months,
           ROUND(300000.00 * (0.065 / 12) * POWER(1 + 0.065 / 12, 360)
                 / (POWER(1 + 0.065 / 12, 360) - 1), 2) AS payment
),
schedule AS (
    SELECT 0 AS month, principal AS balance,
           0.00::NUMERIC AS paid, 0.00::NUMERIC AS interest
    FROM terms
    UNION ALL
    SELECT s.month + 1,
           s.balance - (p.paid - i.interest),
           p.paid,
           i.interest
    FROM schedule s
    CROSS JOIN terms t
    CROSS JOIN LATERAL (SELECT ROUND(s.balance * t.monthly_rate, 2) AS interest) i
    CROSS JOIN LATERAL (SELECT CASE WHEN s.month + 1 = t.months
                                    THEN s.balance + i.interest
                                    ELSE t.payment END AS paid) p
    WHERE s.month < t.months
)
SELECT sum(paid) AS total_paid, sum(interest) AS total_interest, min(balance) AS lowest_balance
FROM schedule;
```

```result
 total_paid | total_interest | lowest_balance
------------+----------------+----------------
  682636.71 |      382636.71 |           0.00
(1 row)
```

Over 30 years the borrower pays back 682,636.71 for a 300,000 loan, of which 382,636.71 is interest.

## Stopping a runaway recursion

The `WHERE` clause is the only brake. If it never becomes false, PostgreSQL does not stop on its own: with `UNION ALL` the recursion keeps producing rows until the query is cancelled or the server runs out of memory or disk. Set a `statement_timeout` when you experiment with recursive queries.

There are three ways to bound a recursion that has no obvious end. The first is a step counter in the `WHERE` clause, as in the Collatz example, and it is the right default.



The second is a `LIMIT` in the outer query. PostgreSQL evaluates a recursive CTE lazily, so when the outer query stops asking for rows the recursion stops too:

```sql
WITH RECURSIVE naturals AS (
    SELECT 1 AS n
    UNION ALL
    SELECT n + 1 FROM naturals
)
SELECT n FROM naturals LIMIT 5;
```

```result
 n
---
 1
 2
 3
 4
 5
(5 rows)
```

The recursion above has no stopping condition at all and still finishes, because only five rows are requested. That works only as long as the outer query does not need all rows: add an `ORDER BY` or an aggregate and it never returns. Treat it as a convenience, not as a safeguard.

The third is `UNION` instead of `UNION ALL`. It discards a row that has already been produced, so a sequence that returns to an earlier value stops by itself. Here `x -> (3x + 1) mod 7` visits six values and then repeats:

```sql
WITH RECURSIVE walk AS (
    SELECT 1 AS n
    UNION
    SELECT (n * 3 + 1) % 7 FROM walk
)
SELECT n FROM walk ORDER BY n;
```

```result
 n
---
 0
 1
 2
 4
 5
 6
(6 rows)
```

This only works when the row consists of the value alone. As soon as a step counter is part of the row, every row is different and the dedup never triggers.

## Differences between databases

- PostgreSQL: no iteration limit, see above.
- MySQL 8: stops with an error after 1,000 iterations by default (`cte_max_recursion_depth`). A 1,001-row sequence needs that setting raised.
- SQL Server: writes `WITH` without the `RECURSIVE` keyword, and stops after 100 recursion levels unless the query ends with `OPTION (MAXRECURSION n)`.
- SQLite: same syntax as PostgreSQL, `WITH RECURSIVE`.

## In short

- A recursive CTE is a loop whose variables are columns. Carry whatever the next step needs (the previous value, a running product, a remaining string) as columns.
- The anchor fixes the column types. Pick `BIGINT` or `NUMERIC` there if the values grow.
- Bound the recursion yourself: a step limit in `WHERE`, or `UNION` when the sequence can repeat.
- For plain runs of numbers or dates in PostgreSQL, `generate_series` is simpler and cheaper. Use recursion when each value depends on the one before.
