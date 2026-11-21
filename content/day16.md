---
day: 16
title: "Interval Overlaps: Scheduling Conflict Detection"
summary: "One condition, start < other_end AND end > other_start, finds every overlap. How to use it for booking checks and conflict reports, why LEAD misses long bookings, and how PostgreSQL range types can forbid overlaps outright."
teaser: "One condition finds every overlap; LEAD misses long bookings; range types can forbid overlaps."
---

A user wants Conference Room A from 14:00 to 15:30. Before confirming, you need to know whether an existing booking overlaps that slot. The check is one `WHERE` clause, and most first attempts get it wrong in some corner.

The queries run on PostgreSQL 18. The overlap condition itself is plain comparison logic and works in any SQL database. The `OVERLAPS` operator, range types and exclusion constraints near the end are PostgreSQL features.

## The seven cases

Two intervals can be positioned against each other in these ways:

```text
1. New starts inside existing        2. New ends inside existing
   Existing:  |---------|               Existing:       |---------|
   New:            |---------|          New:       |---------|

3. New contains existing             4. Existing contains new
   Existing:    |-----|                 Existing:  |---------|
   New:       |---------|               New:          |---|

5. Same interval                     6. New entirely before existing
   Existing:  |---------|               Existing:            |---------|
   New:       |---------|               New:       |-----|

7. New entirely after existing
   Existing:  |---------|
   New:                    |---------|
```

Cases 1 to 5 are overlaps, 6 and 7 are not. A `WHERE` clause has to accept the first five and reject the last two.

## The data

```sql
CREATE TABLE room_bookings (
    booking_id   INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    room_id      INT NOT NULL,
    booked_by    VARCHAR(100) NOT NULL,
    start_time   TIMESTAMP NOT NULL,
    end_time     TIMESTAMP NOT NULL,
    description  VARCHAR(200),
    CHECK (end_time > start_time)
);

INSERT INTO room_bookings (room_id, booked_by, start_time, end_time, description) VALUES
    (1, 'Alice',   '2024-06-10 09:00', '2024-06-10 10:00', 'Sprint Planning'),
    (1, 'Bob',     '2024-06-10 10:00', '2024-06-10 11:30', 'Design Review'),
    (1, 'Charlie', '2024-06-10 13:00', '2024-06-10 14:30', 'Client Call'),
    (1, 'Diana',   '2024-06-10 14:00', '2024-06-10 15:30', 'Team Standup'),
    (1, 'Eve',     '2024-06-10 16:00', '2024-06-10 17:00', 'Interview'),
    (2, 'Frank',   '2024-06-10 09:00', '2024-06-10 12:00', 'Workshop');
```

Charlie (13:00 to 14:30) and Diana (14:00 to 15:30) overlap by 30 minutes, which is a real conflict. Alice and Bob only share the 10:00 boundary, so they do not overlap: Alice's meeting ends at the moment Bob's starts.

## Why listing the cases goes wrong

The usual approach is one condition per case. People tend to cover the two partial overlaps (cases 1 and 2) and forget that the new interval can contain an existing one entirely (case 3):

```sql
SELECT booking_id, booked_by
FROM room_bookings
WHERE room_id = 1
  AND (   ('2024-06-10 12:00' >= start_time AND '2024-06-10 12:00' <  end_time)   -- new start is inside existing
       OR ('2024-06-10 18:00' >  start_time AND '2024-06-10 18:00' <= end_time))  -- new end is inside existing
ORDER BY booking_id;
```

Asking about 12:00 to 18:00, which covers Charlie, Diana and Eve completely, returns nothing:

```result
 booking_id | booked_by
------------+-----------
(0 rows)
```

Neither the start nor the end of the new slot falls inside any existing booking, so both clauses are false. The query reports a free room that has three meetings in it.

## Invert the question

Two intervals do not overlap in exactly two situations:

- the existing one ends before or when the new one starts: `end_time <= new_start`
- the existing one starts at or after the new one ends: `start_time >= new_end`

Negate the OR of those two (De Morgan) and you get the condition for an overlap:

```sql norun
start_time < new_end AND end_time > new_start
```

It covers all five overlapping cases without listing them. Take case 4, where the existing booking contains the new one: its start is before the new end and its end is after the new start, so it matches.

## Using it

### Can I book 12:00 to 18:00?

Same question as before, now with the condition above:

```sql
SELECT booking_id, booked_by, start_time::time AS starts, end_time::time AS ends
FROM room_bookings
WHERE room_id = 1
  AND start_time < '2024-06-10 18:00'   -- existing starts before the new one ends
  AND end_time   > '2024-06-10 12:00'   -- existing ends after the new one starts
ORDER BY start_time;
```

```result
 booking_id | booked_by |  starts  |   ends
------------+-----------+----------+----------
          3 | Charlie   | 13:00:00 | 14:30:00
          4 | Diana     | 14:00:00 | 15:30:00
          5 | Eve       | 16:00:00 | 17:00:00
(3 rows)
```

All three are found. An empty result means the slot is free.

### Moving an existing booking

When someone edits a booking, it must not be compared with itself. Can booking 4 (Diana) move to 15:00 to 16:30?

```sql
SELECT booking_id, booked_by, start_time::time AS starts, end_time::time AS ends
FROM room_bookings
WHERE room_id = 1
  AND booking_id <> 4
  AND start_time < '2024-06-10 16:30'
  AND end_time   > '2024-06-10 15:00'
ORDER BY start_time;
```

```result
 booking_id | booked_by |  starts  |   ends
------------+-----------+----------+----------
          5 | Eve       | 16:00:00 | 17:00:00
(1 row)
```

Eve's interview starts at 16:00, so the move is refused. Without `booking_id <> 4`, Diana's current slot would also match.

### All conflicts already in the table

To audit existing data, join the table to itself. `a.booking_id < b.booking_id` keeps each pair once instead of twice:

```sql
SELECT a.booking_id AS booking_a, a.booked_by AS person_a,
       b.booking_id AS booking_b, b.booked_by AS person_b,
       least(a.end_time, b.end_time) - greatest(a.start_time, b.start_time) AS overlap
FROM room_bookings a
JOIN room_bookings b
  ON  a.room_id = b.room_id
  AND a.booking_id < b.booking_id
  AND a.start_time < b.end_time
  AND a.end_time   > b.start_time
ORDER BY a.booking_id, b.booking_id;
```

```result
 booking_a | person_a | booking_b | person_b | overlap
-----------+----------+-----------+----------+----------
         3 | Charlie  |         4 | Diana    | 00:30:00
(1 row)
```

The length of the overlap is `least(ends) - greatest(starts)`, here the 30 minutes between 14:00 and 14:30.

## Touching boundaries

Whether 09:00 to 10:00 and 10:00 to 11:00 conflict is a business rule. Time slots are normally treated as half-open: the start is included, the end is not, so 10:00 belongs only to the second slot. The strict `<` and `>` above implement exactly that. If both endpoints count (closed intervals), the operators become `<=` and `>=`.

The difference shows up when you ask for 10:00 to 11:00 in room 1, where Alice ends at 10:00 and Bob starts at 10:00:

```sql
SELECT 'half-open' AS rule, booked_by
FROM room_bookings
WHERE room_id = 1 AND start_time < '2024-06-10 11:00' AND end_time > '2024-06-10 10:00'
UNION ALL
SELECT 'closed', booked_by
FROM room_bookings
WHERE room_id = 1 AND start_time <= '2024-06-10 11:00' AND end_time >= '2024-06-10 10:00'
ORDER BY 1, 2;
```

```result
   rule    | booked_by
-----------+-----------
 closed    | Alice
 closed    | Bob
 half-open | Bob
(3 rows)
```

With closed intervals Alice blocks the slot even though her meeting is over at 10:00. For rooms, shifts and most bookings, half-open is the right choice. Closed intervals fit things like whole-day dates, where an end of `06-10` really means "through the end of the 10th".

## Speed

The self-join compares every pair in a room. With 10,000 bookings in one room that is 10,000 × 9,999 / 2, about 50 million comparisons, and the cost grows with the square of the room's bookings. A single availability check is a different job: it only needs the rows around one time slot, and how fast that is depends on the index.

A B-tree on `(room_id, start_time)` helps with only one half of the condition. To see it, generate 200,000 one-hour bookings across 20 rooms:

```sql
CREATE TABLE big_bookings (
    booking_id  INT PRIMARY KEY,
    room_id     INT NOT NULL,
    start_time  TIMESTAMP NOT NULL,
    end_time    TIMESTAMP NOT NULL
);

INSERT INTO big_bookings
SELECT g, 1 + g % 20, ts, ts + interval '1 hour'
FROM (SELECT g, timestamp '2024-01-01' + (g / 20) * interval '90 minutes' AS ts
      FROM generate_series(1, 200000) g) s;

CREATE INDEX big_bookings_start_idx ON big_bookings (room_id, start_time);
ANALYZE big_bookings;

EXPLAIN (COSTS OFF)
SELECT * FROM big_bookings
WHERE room_id = 5
  AND start_time < '2024-03-01 15:30'
  AND end_time   > '2024-03-01 14:00';
```

```result
                                                QUERY PLAN
-----------------------------------------------------------------------------------------------------------
 Bitmap Heap Scan on big_bookings
   Recheck Cond: ((room_id = 5) AND (start_time < '2024-03-01 15:30:00'::timestamp without time zone))
   Filter: (end_time > '2024-03-01 14:00:00'::timestamp without time zone)
   ->  Bitmap Index Scan on big_bookings_start_idx
         Index Cond: ((room_id = 5) AND (start_time < '2024-03-01 15:30:00'::timestamp without time zone))
(5 rows)
```

The index can only say "starts before 15:30", which is every booking of room 5 from January 1st up to that day. The `end_time` condition is applied afterwards as a filter. A GiST index on the interval as a range lets PostgreSQL search both ends at once. `btree_gist` is needed so that the plain integer `room_id` can be part of a GiST index:

```sql
CREATE EXTENSION IF NOT EXISTS btree_gist;

CREATE INDEX big_bookings_range_idx
    ON big_bookings USING gist (room_id, tsrange(start_time, end_time));
DROP INDEX big_bookings_start_idx;
ANALYZE big_bookings;

EXPLAIN (COSTS OFF)
SELECT * FROM big_bookings
WHERE room_id = 5
  AND tsrange(start_time, end_time) && tsrange('2024-03-01 14:00', '2024-03-01 15:30');
```

```result
                                                             QUERY PLAN
-------------------------------------------------------------------------------------------------------------------------------------
 Bitmap Heap Scan on big_bookings
   Recheck Cond: ((room_id = 5) AND (tsrange(start_time, end_time) && '["2024-03-01 14:00:00","2024-03-01 15:30:00")'::tsrange))
   ->  Bitmap Index Scan on big_bookings_range_idx
         Index Cond: ((room_id = 5) AND (tsrange(start_time, end_time) && '["2024-03-01 14:00:00","2024-03-01 15:30:00")'::tsrange))
(4 rows)
```

Both conditions now sit in the `Index Cond`. `&&` is the range "overlaps" operator, and `tsrange(a, b)` is the half-open range `[a, b)`, so it gives the same answer as the explicit `<` and `>` condition. The two queries return the same two bookings for this slot.

## Finding every conflict in one pass: running maximum

For an audit of a whole table there is a cheaper approach than the self-join: sort each room by start time and compare each booking with the latest end time seen so far. The tempting shortcut is `LEAD`, which compares each booking only with the next one. It works on the sample data and is wrong in general. Room 3 has one all-day booking with three short meetings inside it:

```sql
INSERT INTO room_bookings (room_id, booked_by, start_time, end_time, description) VALUES
    (3, 'Gina', '2024-06-10 09:00', '2024-06-10 17:00', 'Offsite'),
    (3, 'Hugo', '2024-06-10 10:00', '2024-06-10 11:00', 'Interview A'),
    (3, 'Ivy',  '2024-06-10 13:00', '2024-06-10 14:00', 'Interview B'),
    (3, 'Jon',  '2024-06-10 15:00', '2024-06-10 16:00', 'Interview C');
```

The self-join finds Gina's three conflicts:

```sql
SELECT a.booked_by AS person_a, b.booked_by AS person_b
FROM room_bookings a
JOIN room_bookings b
  ON  a.room_id = b.room_id
  AND a.booking_id < b.booking_id
  AND a.start_time < b.end_time
  AND a.end_time   > b.start_time
WHERE a.room_id = 3
ORDER BY a.booking_id, b.booking_id;
```

```result
 person_a | person_b
----------+----------
 Gina     | Hugo
 Gina     | Ivy
 Gina     | Jon
(3 rows)
```

Now the `LEAD` version, which compares each booking's end with the next booking's start:

```sql
WITH ordered AS (
    SELECT booking_id, room_id, booked_by, end_time,
           LEAD(booked_by)  OVER w AS next_person,
           LEAD(start_time) OVER w AS next_start
    FROM room_bookings
    WINDOW w AS (PARTITION BY room_id ORDER BY start_time, booking_id)
)
SELECT room_id, booked_by, next_person
FROM ordered
WHERE end_time > next_start
ORDER BY room_id, next_start;
```

```result
 room_id | booked_by | next_person
---------+-----------+-------------
       1 | Charlie   | Diana
       3 | Gina      | Hugo
(2 rows)
```

Ivy and Jon are missing. Hugo's meeting ends at 11:00, and the next booking (Ivy, 13:00) starts later, so the check passes even though Gina's booking is still running. A booking can overlap anything that started earlier, not just its immediate predecessor.

The fix is to compare each booking's start with the maximum end time of all earlier bookings in the room. `ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING` leaves the current row out of the maximum:

```sql
WITH ordered AS (
    SELECT booking_id, room_id, booked_by, start_time,
           max(end_time) OVER (
               PARTITION BY room_id
               ORDER BY start_time, booking_id
               ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
           ) AS busy_until
    FROM room_bookings
)
SELECT room_id, booked_by, start_time::time AS starts, busy_until::time AS busy_until
FROM ordered
WHERE start_time < busy_until
ORDER BY room_id, start_time;
```

```result
 room_id | booked_by |  starts  | busy_until
---------+-----------+----------+------------
       1 | Diana     | 14:00:00 | 14:30:00
       3 | Hugo      | 10:00:00 | 17:00:00
       3 | Ivy       | 13:00:00 | 17:00:00
       3 | Jon       | 15:00:00 | 17:00:00
(4 rows)
```

Each row is a booking that starts while something earlier in the same room is still running. Gina is not listed because nothing runs before her. Diana, Hugo, Ivy and Jon are exactly the later members of the four conflicting pairs found by the self-join (Charlie-Diana, and Gina with each of the three). This takes one sort and one pass per room instead of comparing every pair.

The result is a list of bookings that have a conflict, not the partner each one conflicts with. When you need the partner, run the self-join on the flagged rooms. Ordering by `booking_id` as well as `start_time` makes bookings that start at the same moment sort the same way every time.

## OVERLAPS

PostgreSQL has the standard SQL `OVERLAPS` operator for two `(start, end)` pairs:

```sql
SELECT booking_id, booked_by
FROM room_bookings
WHERE room_id = 1
  AND (start_time, end_time) OVERLAPS (TIMESTAMP '2024-06-10 14:00', TIMESTAMP '2024-06-10 15:30')
ORDER BY booking_id;
```

```result
 booking_id | booked_by
------------+-----------
          3 | Charlie
          4 | Diana
(2 rows)
```

It treats the periods as half-open, so touching periods do not overlap. Two details differ from the hand-written condition. A period whose start equals its end counts as a single instant, and an instant at the start of another period does overlap it. And if the two values of a pair are given in reverse order, PostgreSQL swaps them instead of rejecting them:

```sql
SELECT (TIMESTAMP '2024-06-10 09:00', TIMESTAMP '2024-06-10 10:00')
           OVERLAPS (TIMESTAMP '2024-06-10 10:00', TIMESTAMP '2024-06-10 11:00') AS touching,
       (TIMESTAMP '2024-06-10 09:00', TIMESTAMP '2024-06-10 09:00')
           OVERLAPS (TIMESTAMP '2024-06-10 09:00', TIMESTAMP '2024-06-10 11:00') AS instant_at_start,
       (TIMESTAMP '2024-06-10 11:00', TIMESTAMP '2024-06-10 09:00')
           OVERLAPS (TIMESTAMP '2024-06-10 10:00', TIMESTAMP '2024-06-10 10:30') AS reversed;
```

```result
 touching | instant_at_start | reversed
----------+------------------+----------
 f        | t                | t
(1 row)
```

`CHECK (end_time > start_time)` in the table rules out the instant case. `OVERLAPS` cannot use a GiST range index like the one above, so for large tables the explicit condition or `&&` on a range is the better tool.

## Range types and exclusion constraints

Everything so far finds conflicts after they exist. PostgreSQL can refuse them instead. Store the interval as a range and add an exclusion constraint:

```sql
CREATE TABLE room_bookings_v2 (
    booking_id  INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    room_id     INT NOT NULL,
    booked_by   VARCHAR(100) NOT NULL,
    during      TSRANGE NOT NULL,
    EXCLUDE USING gist (room_id WITH =, during WITH &&)
);

INSERT INTO room_bookings_v2 (room_id, booked_by, during) VALUES
    (1, 'Alice', '[2024-06-10 09:00, 2024-06-10 10:00)'),
    (1, 'Bob',   '[2024-06-10 10:00, 2024-06-10 11:30)'),
    (2, 'Frank', '[2024-06-10 09:30, 2024-06-10 10:30)');
```

The constraint reads: no two rows may have equal `room_id` and overlapping `during`. Bob's booking starts exactly where Alice's ends, and the half-open range `[...)` makes that legal. Frank is in a different room, so his overlapping time is also fine.

An insert that really overlaps is rejected by the database, whichever application sends it:

```sql norun
INSERT INTO room_bookings_v2 (room_id, booked_by, during)
VALUES (1, 'Carol', '[2024-06-10 09:30, 2024-06-10 10:30)');
```

```text
ERROR:  conflicting key value violates exclusion constraint "room_bookings_v2_room_id_during_excl"
DETAIL:  Key (room_id, during)=(1, ["2024-06-10 09:30:00","2024-06-10 10:30:00")) conflicts with existing key (room_id, during)=(1, ["2024-06-10 09:00:00","2024-06-10 10:00:00")).
```

This is also the safe way to prevent double bookings. The application-side pattern "run the overlap query, insert if it is empty" has a race: two sessions can both see an empty result and both insert. The constraint is enforced by the database, which makes one of the two inserts fail. The index behind it is the same kind of GiST index as in the Speed section, so the check stays cheap on large tables.

## In short

- Two intervals overlap when `a_start < b_end AND a_end > b_start`. Write it once instead of listing cases.
- Decide whether touching ends conflict. Half-open (strict `<` and `>`) is right for time slots.
- For a single availability check, a GiST index on `tsrange(start, end)` serves both ends of the condition; a B-tree on the start column serves only one.
- To scan a whole table, use a running `max(end_time)` over the earlier rows, not `LEAD`, which misses a long booking that spans several short ones.
- To prevent overlaps instead of detecting them, use a range column with `EXCLUDE USING gist`.
