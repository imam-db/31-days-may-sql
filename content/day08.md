---
day: 8
title: "The Gaps and Islands Problem"
summary: "Find consecutive runs (islands) and the holes between them with window functions: the date-minus-row-number trick, LAG with a running sum, LEAD for gaps, and the duplicate rows that break the trick."
teaser: "Consecutive runs and the holes between them, and the duplicate dates that break the usual trick."
---

A table records which days each user logged in. Product wants two answers: the longest streak of consecutive days per user, and the dates in between when the user did not show up. The streaks are called islands and the holes between them are gaps. The queries below run on PostgreSQL 18 and use only window functions, which MySQL 8, SQL Server and BigQuery also have (date arithmetic differs per database).

## The data

```sql
CREATE TABLE user_logins (
    user_id     INT,
    login_date  DATE
);

INSERT INTO user_logins VALUES
    (1, '2026-05-01'),
    (1, '2026-05-02'),
    (1, '2026-05-03'),
    (1, '2026-05-05'),
    (1, '2026-05-06'),
    (1, '2026-05-09'),
    (2, '2026-05-01'),
    (2, '2026-05-04'),
    (2, '2026-05-05'),
    (2, '2026-05-06'),
    (2, '2026-05-07');
```

User 1 logged in on the 1st to 3rd, the 5th and 6th, and the 9th. User 2 logged in on the 1st, then on the 4th to 7th.

## Two approaches that do not scale

A self-join finds where a streak starts: a login with no login the day before.

```sql
SELECT a.user_id, a.login_date
FROM user_logins a
LEFT JOIN user_logins b
       ON b.user_id = a.user_id
      AND b.login_date = a.login_date - 1
WHERE b.login_date IS NULL
ORDER BY a.user_id, a.login_date;
```

```result
 user_id | login_date
---------+------------
       1 | 2026-05-01
       1 | 2026-05-05
       1 | 2026-05-09
       2 | 2026-05-01
       2 | 2026-05-04
(5 rows)
```

That gives the first day of each streak, but not how long it lasts. To get the length you would have to walk forward from each start, one join per day.

A recursive CTE does walk forward:

```sql
WITH RECURSIVE streaks AS (
    SELECT user_id, login_date, 1 AS streak_len
    FROM user_logins s
    WHERE NOT EXISTS (
        SELECT 1 FROM user_logins p
        WHERE p.user_id = s.user_id AND p.login_date = s.login_date - 1
    )

    UNION ALL

    SELECT s.user_id, l.login_date, s.streak_len + 1
    FROM streaks s
    JOIN user_logins l
      ON l.user_id = s.user_id
     AND l.login_date = s.login_date + 1
)
SELECT user_id, MAX(streak_len) AS longest_streak
FROM streaks
GROUP BY user_id
ORDER BY user_id;
```

```result
 user_id | longest_streak
---------+----------------
       1 |              3
       2 |              4
(2 rows)
```

The answer is right, but it takes a join per day of every streak and a good deal of SQL. Window functions do the same job in a single pass over the sorted rows.

## The key idea: date minus row number

Number each user's logins in date order, then subtract that number from the date:

```sql
SELECT user_id,
       login_date,
       ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY login_date) AS rn,
       login_date - ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY login_date)::INT AS group_key
FROM user_logins
WHERE user_id = 1
ORDER BY login_date;
```

```result
 user_id | login_date | rn | group_key
---------+------------+----+------------
       1 | 2026-05-01 |  1 | 2026-04-30
       1 | 2026-05-02 |  2 | 2026-04-30
       1 | 2026-05-03 |  3 | 2026-04-30
       1 | 2026-05-05 |  4 | 2026-05-01
       1 | 2026-05-06 |  5 | 2026-05-01
       1 | 2026-05-09 |  6 | 2026-05-03
(6 rows)
```

Inside a streak both the date and the row number go up by one per row, so their difference does not change. When a day is skipped, the date jumps by more than one while the row number still goes up by one, and the difference becomes a larger number that stays constant for the next streak. The value of `group_key` has no meaning of its own (it is a date here only because `date - integer` is a date in PostgreSQL); it only has to be equal within a streak and different between streaks.

## Islands

Group by that key:

```sql
WITH grouped AS (
    SELECT user_id,
           login_date,
           login_date - ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY login_date)::INT AS group_key
    FROM user_logins
)
SELECT user_id,
       MIN(login_date) AS streak_start,
       MAX(login_date) AS streak_end,
       COUNT(*)        AS streak_length
FROM grouped
GROUP BY user_id, group_key
ORDER BY user_id, streak_start;
```

```result
 user_id | streak_start | streak_end | streak_length
---------+--------------+------------+---------------
       1 | 2026-05-01   | 2026-05-03 |             3
       1 | 2026-05-05   | 2026-05-06 |             2
       1 | 2026-05-09   | 2026-05-09 |             1
       2 | 2026-05-01   | 2026-05-01 |             1
       2 | 2026-05-04   | 2026-05-07 |             4
(5 rows)
```

The longest streak per user is one more aggregation on top. `DISTINCT ON` keeps the first row per user after sorting by length, and the date columns are included so the answer says when the streak happened. The `streak_start` in the `ORDER BY` makes ties deterministic (earliest streak wins):

```sql
WITH grouped AS (
    SELECT user_id,
           login_date,
           login_date - ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY login_date)::INT AS group_key
    FROM user_logins
),
streaks AS (
    SELECT user_id,
           MIN(login_date) AS streak_start,
           MAX(login_date) AS streak_end,
           COUNT(*)        AS streak_length
    FROM grouped
    GROUP BY user_id, group_key
)
SELECT DISTINCT ON (user_id) user_id, streak_start, streak_end, streak_length
FROM streaks
ORDER BY user_id, streak_length DESC, streak_start;
```

```result
 user_id | streak_start | streak_end | streak_length
---------+--------------+------------+---------------
       1 | 2026-05-01   | 2026-05-03 |             3
       2 | 2026-05-04   | 2026-05-07 |             4
(2 rows)
```

## The trick breaks on duplicate rows

The row number only keeps pace with the date if there is exactly one row per user per day. If a user can log in twice on the same day, the row number moves on and the date does not:

```sql
INSERT INTO user_logins VALUES (2, '2026-05-05');

WITH grouped AS (
    SELECT user_id,
           login_date,
           login_date - ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY login_date)::INT AS group_key
    FROM user_logins
)
SELECT user_id,
       MIN(login_date) AS streak_start,
       MAX(login_date) AS streak_end,
       COUNT(*)        AS streak_length
FROM grouped
WHERE user_id = 2
GROUP BY user_id, group_key
ORDER BY streak_start;
```

```result
 user_id | streak_start | streak_end | streak_length
---------+--------------+------------+---------------
       2 | 2026-05-01   | 2026-05-01 |             1
       2 | 2026-05-04   | 2026-05-05 |             2
       2 | 2026-05-05   | 2026-05-07 |             3
(3 rows)
```

The streak from the 4th to the 7th is now reported as two overlapping pieces (the 4th to the 5th, and the 5th to the 7th), because the second login on the 5th pushes the row number ahead of the date. Remove the duplicates before numbering, either with `SELECT DISTINCT user_id, login_date` in the first CTE or by using `DENSE_RANK()` instead of `ROW_NUMBER()`:

```sql
WITH grouped AS (
    SELECT user_id,
           login_date,
           login_date - DENSE_RANK() OVER (PARTITION BY user_id ORDER BY login_date)::INT AS group_key
    FROM user_logins
)
SELECT user_id,
       MIN(login_date) AS streak_start,
       MAX(login_date) AS streak_end,
       COUNT(DISTINCT login_date) AS streak_days
FROM grouped
WHERE user_id = 2
GROUP BY user_id, group_key
ORDER BY streak_start;
```

```result
 user_id | streak_start | streak_end | streak_days
---------+--------------+------------+-------------
       2 | 2026-05-01   | 2026-05-01 |           1
       2 | 2026-05-04   | 2026-05-07 |           4
(2 rows)
```

With a `DATE` column that is the common case. With a `TIMESTAMP` column you first need to truncate to the day, for example `login_at::DATE`, and then deal with duplicates in the same way.

## The LAG version: flag the breaks, then count them

The second way reads closer to how you would describe the problem. Flag every row that starts a new streak (the previous login is more than one day earlier, or there is no previous login), then take a running sum of the flags. Each streak gets its own number:

```sql
WITH flagged AS (
    SELECT user_id,
           login_date,
           CASE
               WHEN login_date - LAG(login_date) OVER (PARTITION BY user_id ORDER BY login_date) = 1
               THEN 0 ELSE 1
           END AS is_new_streak
    FROM (SELECT DISTINCT user_id, login_date FROM user_logins) AS d
),
numbered AS (
    SELECT user_id,
           login_date,
           SUM(is_new_streak) OVER (PARTITION BY user_id ORDER BY login_date) AS streak_id
    FROM flagged
)
SELECT user_id,
       streak_id,
       MIN(login_date) AS streak_start,
       MAX(login_date) AS streak_end,
       COUNT(*)        AS streak_length
FROM numbered
GROUP BY user_id, streak_id
ORDER BY user_id, streak_id;
```

```result
 user_id | streak_id | streak_start | streak_end | streak_length
---------+-----------+--------------+------------+---------------
       1 |         1 | 2026-05-01   | 2026-05-03 |             3
       1 |         2 | 2026-05-05   | 2026-05-06 |             2
       1 |         3 | 2026-05-09   | 2026-05-09 |             1
       2 |         1 | 2026-05-01   | 2026-05-01 |             1
       2 |         2 | 2026-05-04   | 2026-05-07 |             4
(5 rows)
```

The first login of each user has no `LAG`, so the difference is `NULL`, the `CASE` falls to `ELSE`, and the row counts as the start of streak 1. That is why the test is `= 1` with the break in the `ELSE` branch: a test like `> 1` would leave `NULL` unflagged and the numbering would start at 0.

This version deduplicates in its own subquery, and it does not depend on the step being exactly one, since the test can be any rule ("more than 30 minutes since the previous event" is how sessions are usually cut). It needs two window passes instead of one, which hardly matters until the table is large.

## Finding gaps

For gaps, look forward instead of backward: `LEAD` gives the next login, and a difference of more than one day means days were missed in between.

```sql
WITH with_next AS (
    SELECT user_id,
           login_date,
           LEAD(login_date) OVER (PARTITION BY user_id ORDER BY login_date) AS next_date
    FROM (SELECT DISTINCT user_id, login_date FROM user_logins) AS d
)
SELECT user_id,
       login_date + 1                AS gap_start,
       next_date - 1                 AS gap_end,
       next_date - login_date - 1    AS missing_days
FROM with_next
WHERE next_date > login_date + 1
ORDER BY user_id, gap_start;
```

```result
 user_id | gap_start  |  gap_end   | missing_days
---------+------------+------------+--------------
       1 | 2026-05-04 | 2026-05-04 |            1
       1 | 2026-05-07 | 2026-05-08 |            2
       2 | 2026-05-02 | 2026-05-03 |            2
(3 rows)
```

Gaps only exist between logins. This query says nothing about the days before a user's first login or after the last one; those depend on the window you are measuring (the whole month, the user's signup date) and need a calendar to compare against.

### One row per missing day

If you need the individual missing dates (to fill a chart axis, for instance), generate the calendar and anti-join it. The range is bounded per user by their first and last login:

```sql
SELECT u.user_id, d::DATE AS missing_date
FROM (SELECT user_id, MIN(login_date) AS first_day, MAX(login_date) AS last_day
      FROM user_logins GROUP BY user_id) AS u
CROSS JOIN LATERAL generate_series(u.first_day, u.last_day, INTERVAL '1 day') AS d
WHERE NOT EXISTS (
    SELECT 1 FROM user_logins l
    WHERE l.user_id = u.user_id AND l.login_date = d::DATE
)
ORDER BY u.user_id, missing_date;
```

```result
 user_id | missing_date
---------+--------------
       1 | 2026-05-04
       1 | 2026-05-07
       1 | 2026-05-08
       2 | 2026-05-02
       2 | 2026-05-03
(5 rows)
```

## Other kinds of sequences

The same idea works on anything with a fixed step.

Integer IDs: `id - ROW_NUMBER() OVER (ORDER BY id)` is constant over each unbroken run of ids, so runs of invoice numbers, and the missing ranges between them, come out of the same two queries.

```sql
CREATE TABLE invoices (invoice_no INT PRIMARY KEY);
INSERT INTO invoices VALUES (1001), (1002), (1003), (1006), (1007), (1010);

SELECT MIN(invoice_no) AS first_no,
       MAX(invoice_no) AS last_no,
       COUNT(*)        AS invoices
FROM (SELECT invoice_no,
             invoice_no - ROW_NUMBER() OVER (ORDER BY invoice_no) AS grp
      FROM invoices) AS t
GROUP BY grp
ORDER BY first_no;
```

```result
 first_no | last_no | invoices
----------+---------+----------
     1001 |    1003 |        3
     1006 |    1007 |        2
     1010 |    1010 |        1
(3 rows)
```

Fixed-interval timestamps: subtract the row number times the interval, for example `ts - ROW_NUMBER() OVER (ORDER BY ts) * INTERVAL '1 hour'` for hourly readings. Any other step works the same way, including steps larger than a day.

Months: the day-based version does not work, because months have different lengths. Convert each month to a month index first, for example `EXTRACT(year FROM m) * 12 + EXTRACT(month FROM m)`, and subtract the row number from that.

When the step is not constant at all (business days, trading days), the subtraction cannot be used. Number the rows of a calendar table that holds only valid days, join to it, and subtract the two row numbers; or use the `LAG` version with a rule that decides what counts as "next".

## In short

- Islands and gaps are the two sides of one problem; both are solved with window functions over rows sorted by the sequence value.
- Islands: `value - ROW_NUMBER()` is constant inside a run, so group by it. Make sure the sequence has no duplicates first, or use `DENSE_RANK()`.
- Gaps: compare each row with `LEAD`, or generate the full range and anti-join it.
- When the rule for "same run" is not a fixed step, flag the breaks with `LAG` and take a running sum.
