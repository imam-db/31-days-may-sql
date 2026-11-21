---
day: 12
title: "Running Total With a Conditional Reset"
summary: "SQL has no RESET WHEN clause, so a streak counter is built from two window functions: a running count of reset events that numbers the groups, then a running sum inside each group."
teaser: "A streak counter that resets to zero, built from two window functions."
---

Duolingo shows a flame with your streak: 47 days, 48, 49. Skip a day and it drops to zero. Streaks like this, and their relatives (consecutive months paid, consecutive passing builds, points that restart each quarter), are running totals that start over when some condition is met.

`SUM() OVER (ORDER BY ...)` gives a running total but has no way to say "start over here". Window frames are defined by row positions relative to the current row, not by conditions on the data. The workaround is to number the streaks first and then run a normal running sum inside each number. The examples use PostgreSQL 18, and the window functions involved are standard SQL.

## The data

One row per user per day, including days with no lesson:

```sql
CREATE TABLE user_daily_activity (
    user_id           INT  NOT NULL,
    activity_date     DATE NOT NULL,
    completed_lesson  BOOLEAN NOT NULL,
    xp_earned         INT  NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, activity_date)
);

INSERT INTO user_daily_activity VALUES
    (7, '2024-03-01', true,  20),
    (7, '2024-03-02', true,  15),
    (7, '2024-03-03', true,  30),
    (7, '2024-03-04', false,  0),
    (7, '2024-03-05', true,  25),
    (7, '2024-03-06', true,  20),
    (7, '2024-03-07', true,  40),
    (7, '2024-03-08', true,  15),
    (7, '2024-03-09', false,  0),
    (7, '2024-03-10', true,  20),
    (7, '2024-03-11', true,  30),
    (8, '2024-03-01', false,  0),
    (8, '2024-03-02', true,  10),
    (8, '2024-03-03', true,   5),
    (8, '2024-03-04', true,  12);
```

User 7 misses 4 and 9 March, so the streak values we want are 1, 2, 3, 0, 1, 2, 3, 4, 0, 1, 2. User 8 starts with a miss, which is an edge case the pattern has to survive.

Apps that only store rows for active days need one more step first. That case is covered further down.

## The naive attempt

Counting completed lessons with a running `SUM` is the first thing people try:

```sql
SELECT activity_date, completed_lesson,
       SUM(CASE WHEN completed_lesson THEN 1 ELSE 0 END)
           OVER (ORDER BY activity_date) AS streak
FROM user_daily_activity
WHERE user_id = 7
ORDER BY activity_date;
```

```result
 activity_date | completed_lesson | streak
---------------+------------------+--------
 2024-03-01    | t                |      1
 2024-03-02    | t                |      2
 2024-03-03    | t                |      3
 2024-03-04    | f                |      3
 2024-03-05    | t                |      4
 2024-03-06    | t                |      5
 2024-03-07    | t                |      6
 2024-03-08    | t                |      7
 2024-03-09    | f                |      7
 2024-03-10    | t                |      8
 2024-03-11    | t                |      9
(11 rows)
```

The count never goes back down. On 4 March it stays at 3 where it should be 0, and from 5 March it keeps climbing (4, 5, 6, 7) where the streak should be 1, 2, 3, 4. The window has no idea a streak ended, and no frame clause fixes that: `ROWS BETWEEN n PRECEDING AND CURRENT ROW` takes a fixed `n`, and a frame start that depends on the data does not exist.

## Number the streaks

If every streak had its own number, the problem would go away: partition by that number and a plain running sum does the rest. A streak ends when a miss appears, so count the misses seen so far, including the current row:

```sql
SELECT activity_date, completed_lesson,
       SUM(CASE WHEN NOT completed_lesson THEN 1 ELSE 0 END)
           OVER (ORDER BY activity_date) AS reset_group
FROM user_daily_activity
WHERE user_id = 7
ORDER BY activity_date;
```

```result
 activity_date | completed_lesson | reset_group
---------------+------------------+-------------
 2024-03-01    | t                |           0
 2024-03-02    | t                |           0
 2024-03-03    | t                |           0
 2024-03-04    | f                |           1
 2024-03-05    | t                |           1
 2024-03-06    | t                |           1
 2024-03-07    | t                |           1
 2024-03-08    | t                |           1
 2024-03-09    | f                |           2
 2024-03-10    | t                |           2
 2024-03-11    | t                |           2
(11 rows)
```

The counter goes up on each miss, so a miss row belongs to the group it starts, and the active days that follow share its number. Group 0 is the first streak (1 to 3 March). Group 1 is the miss on 4 March plus the second streak. Group 2 starts with the miss on 9 March.

Now the second window function, partitioned by that number. It needs the first result as an input, so the first goes in a CTE. The query below also covers every user at once:

```sql
WITH marked AS (
    SELECT user_id, activity_date, completed_lesson, xp_earned,
           SUM(CASE WHEN NOT completed_lesson THEN 1 ELSE 0 END)
               OVER (PARTITION BY user_id ORDER BY activity_date) AS reset_group
    FROM user_daily_activity
)
SELECT user_id, activity_date, completed_lesson, reset_group,
       SUM(CASE WHEN completed_lesson THEN 1 ELSE 0 END)
           OVER (PARTITION BY user_id, reset_group ORDER BY activity_date) AS streak
FROM marked
ORDER BY user_id, activity_date;
```

```result
 user_id | activity_date | completed_lesson | reset_group | streak
---------+---------------+------------------+-------------+--------
       7 | 2024-03-01    | t                |           0 |      1
       7 | 2024-03-02    | t                |           0 |      2
       7 | 2024-03-03    | t                |           0 |      3
       7 | 2024-03-04    | f                |           1 |      0
       7 | 2024-03-05    | t                |           1 |      1
       7 | 2024-03-06    | t                |           1 |      2
       7 | 2024-03-07    | t                |           1 |      3
       7 | 2024-03-08    | t                |           1 |      4
       7 | 2024-03-09    | f                |           2 |      0
       7 | 2024-03-10    | t                |           2 |      1
       7 | 2024-03-11    | t                |           2 |      2
       8 | 2024-03-01    | f                |           1 |      0
       8 | 2024-03-02    | t                |           1 |      1
       8 | 2024-03-03    | t                |           1 |      2
       8 | 2024-03-04    | t                |           1 |      3
(15 rows)
```

User 7 gets 1, 2, 3, 0, 1, 2, 3, 4, 0, 1, 2 as intended. Miss rows show 0 without any special handling, because they add 0 to the sum. User 8, who starts with a miss, gets 0 and then 1, 2, 3.

Two window functions replace what would be a loop with a mutable counter in a procedural language. The first is the loop's "if the user missed, start a new group", and the second is "add one to the current streak".

## The same pattern for XP

Only the aggregate changes. This returns the XP earned since the current streak began. `FILTER` limits the sum to lesson days, so a miss row stays at 0 even if it carried some XP:

```sql
WITH marked AS (
    SELECT user_id, activity_date, completed_lesson, xp_earned,
           SUM(CASE WHEN NOT completed_lesson THEN 1 ELSE 0 END)
               OVER (PARTITION BY user_id ORDER BY activity_date) AS reset_group
    FROM user_daily_activity
)
SELECT activity_date, xp_earned,
       COALESCE(SUM(xp_earned) FILTER (WHERE completed_lesson)
           OVER (PARTITION BY user_id, reset_group ORDER BY activity_date), 0) AS streak_xp
FROM marked
WHERE user_id = 7
ORDER BY activity_date;
```

```result
 activity_date | xp_earned | streak_xp
---------------+-----------+-----------
 2024-03-01    |        20 |        20
 2024-03-02    |        15 |        35
 2024-03-03    |        30 |        65
 2024-03-04    |         0 |         0
 2024-03-05    |        25 |        25
 2024-03-06    |        20 |        45
 2024-03-07    |        40 |        85
 2024-03-08    |        15 |       100
 2024-03-09    |         0 |         0
 2024-03-10    |        20 |        20
 2024-03-11    |        30 |        50
(11 rows)
```

`SUM ... FILTER` returns `NULL` when no row in the frame passes the filter, which is the case for the miss rows, hence the `COALESCE`. `FILTER` on window aggregates is available in PostgreSQL; for databases without it, use the `CASE` form from the earlier queries.

## When only active days are stored

Most apps do not store a row for a day with no activity. The table is then just the lesson days, and counting misses is impossible because there is nothing to count. The usual fix is to generate a calendar with `generate_series` and left join it. There is a shorter way that needs no calendar: subtract each row's position from its date.

```sql
CREATE TABLE lesson_days AS
SELECT user_id, activity_date
FROM user_daily_activity
WHERE completed_lesson;

SELECT user_id, activity_date,
       activity_date - (ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY activity_date))::int AS grp
FROM lesson_days
WHERE user_id = 7
ORDER BY activity_date;
```

```result
 user_id | activity_date |    grp
---------+---------------+------------
       7 | 2024-03-01    | 2024-02-29
       7 | 2024-03-02    | 2024-02-29
       7 | 2024-03-03    | 2024-02-29
       7 | 2024-03-05    | 2024-03-01
       7 | 2024-03-06    | 2024-03-01
       7 | 2024-03-07    | 2024-03-01
       7 | 2024-03-08    | 2024-03-01
       7 | 2024-03-10    | 2024-03-02
       7 | 2024-03-11    | 2024-03-02
(9 rows)
```

Inside a run of consecutive days, the date and the row number both go up by 1, so their difference is constant. When a day is skipped, the date jumps and the row number does not, so the difference changes. Each distinct `grp` value is one streak, with no filler rows needed.

The streak counter is then a `ROW_NUMBER()` partitioned by that value, and the longest streak per user, with its dates, is a `GROUP BY`:

```sql
WITH islands AS (
    SELECT user_id, activity_date,
           activity_date - (ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY activity_date))::int AS grp
    FROM lesson_days
),
streaks AS (
    SELECT user_id, MIN(activity_date) AS started, MAX(activity_date) AS ended, COUNT(*) AS days
    FROM islands
    GROUP BY user_id, grp
)
SELECT DISTINCT ON (user_id) user_id, days AS longest_streak, started, ended
FROM streaks
ORDER BY user_id, days DESC, started;
```

```result
 user_id | longest_streak |  started   |   ended
---------+----------------+------------+------------
       7 |              4 | 2024-03-05 | 2024-03-08
       8 |              3 | 2024-03-02 | 2024-03-04
(2 rows)
```

The longest streak for user 7 is 4 days, 5 to 8 March. `DISTINCT ON` keeps the first row per user after sorting by length, with the earlier streak winning a tie.

The trick only works if there is at most one row per user and day. A duplicate day would advance the row number without advancing the date and cut a streak in two. The primary key in the first table prevents that, and `lesson_days` inherits the guarantee because it is derived from it.

## Why ties and the default frame matter

All the running sums above rely on `(user_id, activity_date)` being unique. When a window has `ORDER BY` and no frame clause, PostgreSQL uses `RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW`, and rows with equal sort keys are peers that all see the same total. With a unique key that never shows. With ties it does:

```sql
SELECT d, x,
       SUM(x) OVER (ORDER BY d) AS default_frame,
       SUM(x) OVER (ORDER BY d, x ROWS UNBOUNDED PRECEDING) AS rows_frame
FROM (VALUES (1, 10), (2, 5), (2, 7), (3, 1)) AS t(d, x)
ORDER BY d, x;
```

```result
 d | x  | default_frame | rows_frame
---+----+---------------+------------
 1 | 10 |            10 |         10
 2 |  5 |            22 |         15
 2 |  7 |            22 |         22
 3 |  1 |            23 |         23
(4 rows)
```

The two rows for `d = 2` both show 22 under the default frame, while the `ROWS` version counts them one at a time. If your data can have two events with the same timestamp, spell out the frame and add a tiebreaker to `ORDER BY`.

## Other reset conditions

Only the condition inside the `CASE` changes. These are fragments, not complete queries:

```sql norun
-- reset on any of several statuses
SUM(CASE WHEN status IN ('missed', 'cancelled') THEN 1 ELSE 0 END)
    OVER (PARTITION BY user_id ORDER BY activity_date) AS reset_group

-- reset when a day falls under a threshold
SUM(CASE WHEN xp_earned < 10 THEN 1 ELSE 0 END)
    OVER (PARTITION BY user_id ORDER BY activity_date) AS reset_group
```

When the reset depends on the calendar and not on the data, such as a total that restarts every Monday, you do not need to count anything. Partition by the week instead:

```sql
SELECT activity_date, xp_earned,
       SUM(xp_earned) OVER (
           PARTITION BY user_id, date_trunc('week', activity_date)
           ORDER BY activity_date
       ) AS xp_this_week
FROM user_daily_activity
WHERE user_id = 7
ORDER BY activity_date;
```

```result
 activity_date | xp_earned | xp_this_week
---------------+-----------+--------------
 2024-03-01    |        20 |           20
 2024-03-02    |        15 |           35
 2024-03-03    |        30 |           65
 2024-03-04    |         0 |            0
 2024-03-05    |        25 |           25
 2024-03-06    |        20 |           45
 2024-03-07    |        40 |           85
 2024-03-08    |        15 |          100
 2024-03-09    |         0 |          100
 2024-03-10    |        20 |          120
 2024-03-11    |        30 |           30
(11 rows)
```

`date_trunc('week', ...)` returns the Monday of that week. The sum starts over on Monday 4 March (0 that day, since there was no lesson) and again on Monday 11 March, which restarts at 30. The first three rows belong to the week that began on Monday 26 February.

## In short

- `SUM() OVER` cannot reset on a condition. Turn the resets into group numbers with a running count of reset events, then partition a second window function by that number.
- A miss row starts a new group, so it counts as zero in a lesson-based sum without special handling. When you aggregate something else (XP, amounts), use `FILTER` or a `CASE` so the reset row does not contribute.
- If missing days have no rows, `date - ROW_NUMBER()` groups consecutive dates without a calendar table. It requires one row per user per day.
- When ordering can have ties, write the frame as `ROWS` and add a tiebreaker.
- If the reset is calendar-based, `PARTITION BY date_trunc(...)` is simpler than counting resets.
