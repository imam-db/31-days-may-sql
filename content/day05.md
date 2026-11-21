---
day: 5
title: "NULL in ORDER BY: Who Goes First?"
summary: "Where NULLs land in a sorted result depends on the database. How that changes a top-3 query, a ranking and DISTINCT ON, how NULLS FIRST/LAST fixes it, and what it does to index use."
teaser: "NULLs sort last in PostgreSQL and first in MySQL, and top-N queries pay for it."
---

A table where two players have no score yet:

```sql
CREATE TABLE scores (
    player  TEXT,
    points  INT
);

INSERT INTO scores VALUES
    ('Alice', 95),
    ('Bob',   NULL),
    ('Carol', 87),
    ('Dave',  NULL),
    ('Eve',   72),
    ('Frank', 91);

SELECT player, points
FROM scores
ORDER BY points, player;
```

```result
 player | points
--------+--------
 Eve    |     72
 Carol  |     87
 Frank  |     91
 Alice  |     95
 Bob    |
 Dave   |
(6 rows)
```

In PostgreSQL the `NULL`s are at the bottom. MySQL puts them at the top for the same query. SQL does not say where `NULL` sorts; it only requires that all `NULL`s end up together, at one end.

## Defaults by database

PostgreSQL and Oracle treat `NULL` as larger than any value. MySQL, SQL Server, SQLite and BigQuery treat it as smaller.

| Database | `ORDER BY x ASC` | `ORDER BY x DESC` |
| --- | --- | --- |
| PostgreSQL, Oracle | `NULL`s last | `NULL`s first |
| MySQL, SQL Server, SQLite, BigQuery | `NULL`s first | `NULL`s last |

With `DESC` the same PostgreSQL data looks like this, which surprises people who expect "highest scores first":

```sql
SELECT player, points
FROM scores
ORDER BY points DESC, player;
```

```result
 player | points
--------+--------
 Bob    |
 Dave   |
 Alice  |     95
 Frank  |     91
 Carol  |     87
 Eve    |     72
(6 rows)
```

On MySQL the same two queries give `Bob, Dave, Eve, Carol, Frank, Alice` and `Alice, Frank, Carol, Eve, Bob, Dave`.

## NULLS FIRST and NULLS LAST

PostgreSQL, Oracle, SQLite (3.30 and later) and BigQuery let you say it explicitly, so the position no longer depends on the direction or the engine:

```sql
SELECT player, points
FROM scores
ORDER BY points DESC NULLS LAST, player;
```

```result
 player | points
--------+--------
 Alice  |     95
 Frank  |     91
 Carol  |     87
 Eve    |     72
 Bob    |
 Dave   |
(6 rows)
```

MySQL and SQL Server have no `NULLS FIRST` or `NULLS LAST`. A `CASE` expression sorted first does the same and works everywhere:

```sql
SELECT player, points
FROM scores
ORDER BY CASE WHEN points IS NULL THEN 1 ELSE 0 END,
         points DESC,
         player;
```

```result
 player | points
--------+--------
 Alice  |     95
 Frank  |     91
 Carol  |     87
 Eve    |     72
 Bob    |
 Dave   |
(6 rows)
```

The `CASE` is `0` for real values and `1` for `NULL`, so `NULL`s go to the bottom whatever the second sort key does. MySQL also accepts the shorter `ORDER BY points IS NULL, points DESC`. SQL Server does not allow a boolean expression in `ORDER BY`, so there you need the `CASE`.

## Where it hurts

### Top N

"The three highest scores" reads correctly and is wrong in PostgreSQL:

```sql
SELECT player, points
FROM scores
ORDER BY points DESC
LIMIT 3;
```

```result
 player | points
--------+--------
 Bob    |
 Dave   |
 Alice  |     95
(3 rows)
```

Two players with no score take the first two places. The fix is either `NULLS LAST` or filtering the `NULL`s out with `WHERE points IS NOT NULL`.

### Rankings

A window function's `ORDER BY` follows the same rules. Players without a score rank first in PostgreSQL:

```sql
SELECT player, points,
       RANK() OVER (ORDER BY points DESC) AS rank
FROM scores
ORDER BY rank, player;
```

```result
 player | points | rank
--------+--------+------
 Bob    |        |    1
 Dave   |        |    1
 Alice  |     95 |    3
 Frank  |     91 |    4
 Carol  |     87 |    5
 Eve    |     72 |    6
(6 rows)
```

Alice, who has the best score, is third. With `NULLS LAST` the ranking is what you expect:

```sql
SELECT player, points,
       RANK() OVER (ORDER BY points DESC NULLS LAST) AS rank
FROM scores
ORDER BY rank, player;
```

```result
 player | points | rank
--------+--------+------
 Alice  |     95 |    1
 Frank  |     91 |    2
 Carol  |     87 |    3
 Eve    |     72 |    4
 Bob    |        |    5
 Dave   |        |    5
(6 rows)
```

Whether the unscored players should be ranked at all is a separate question. Filtering them out or giving them a `NULL` rank may be better.

### DISTINCT ON

`DISTINCT ON` keeps the first row of each group according to `ORDER BY`. To get each player's latest score, you sort by date descending, and a `NULL` date sorts first:

```sql
CREATE TABLE score_history (
    player       TEXT,
    points       INT,
    recorded_at  DATE
);

INSERT INTO score_history VALUES
    ('Alice', 70, '2025-01-01'),
    ('Alice', 80, '2025-02-01'),
    ('Alice', 60, NULL),            -- date was never recorded
    ('Bob',   50, '2025-01-15');

SELECT DISTINCT ON (player) player, points, recorded_at
FROM score_history
ORDER BY player, recorded_at DESC;
```

```result
 player | points | recorded_at
--------+--------+-------------
 Alice  |     60 |
 Bob    |     50 | 2025-01-15
(2 rows)
```

Alice's "latest" row is the one with the missing date. Adding `NULLS LAST` returns the row you meant:

```sql
SELECT DISTINCT ON (player) player, points, recorded_at
FROM score_history
ORDER BY player, recorded_at DESC NULLS LAST;
```

```result
 player | points | recorded_at
--------+--------+-------------
 Alice  |     80 | 2025-02-01
 Bob    |     50 | 2025-01-15
(2 rows)
```

### Pagination

`LIMIT` and `OFFSET` only give consistent pages if the order is fully determined. Two things can break that: `NULL`s landing at different ends on different databases, and ties. Players with equal points have no defined order between them, so a row can show up on two pages or on none. Adding a unique column as the last sort key fixes the ties:

```sql
SELECT player, points
FROM scores
ORDER BY points DESC NULLS LAST, player
LIMIT 2 OFFSET 4;
```

```result
 player | points
--------+--------
 Bob    |
 Dave   |
(2 rows)
```

### Several sort columns

Each column has its own `NULL` position. Take a team column that is sometimes empty too:

```sql
CREATE TABLE team_scores (player TEXT, team TEXT, points INT);

INSERT INTO team_scores VALUES
    ('Alice', 'red',  95),
    ('Bob',   'red',  NULL),
    ('Carol', NULL,   87),
    ('Dave',  'blue', NULL),
    ('Eve',   'blue', 72),
    ('Frank', NULL,   NULL);

SELECT player, team, points
FROM team_scores
ORDER BY team, points DESC;
```

```result
 player | team | points
--------+------+--------
 Dave   | blue |
 Eve    | blue |     72
 Bob    | red  |
 Alice  | red  |     95
 Frank  |      |
 Carol  |      |     87
(6 rows)
```

Teams without a name come last because the first key is ascending, but inside each team the unscored players come first because the second key is descending. Saying it for each key gives a consistent result:

```sql
SELECT player, team, points
FROM team_scores
ORDER BY team NULLS LAST, points DESC NULLS LAST;
```

```result
 player | team | points
--------+------+--------
 Eve    | blue |     72
 Dave   | blue |
 Alice  | red  |     95
 Bob    | red  |
 Carol  |      |     87
 Frank  |      |
(6 rows)
```

## NULLS FIRST and indexes

A B-tree index has its own `NULL` order. By default PostgreSQL stores an ascending index with `NULL`s last, and a backward scan of that index gives `DESC NULLS FIRST`. A query that asks for `NULL`s in the opposite position cannot be served from it. Building a table large enough for the planner to prefer the index:

```sql
CREATE TABLE big_scores (id SERIAL PRIMARY KEY, points INT);

INSERT INTO big_scores (points)
SELECT CASE WHEN g % 10 = 0 THEN NULL ELSE (g * 7919) % 100000 END
FROM generate_series(1, 200000) AS g;

CREATE INDEX big_scores_points ON big_scores (points);
ANALYZE big_scores;
```

The two orderings that match the index, ascending with `NULL`s last and descending with `NULL`s first, read the index and stop after ten rows:

```sql
EXPLAIN (COSTS OFF)
SELECT * FROM big_scores ORDER BY points LIMIT 10;
```

```result
                       QUERY PLAN
--------------------------------------------------------
 Limit
   ->  Index Scan using big_scores_points on big_scores
(2 rows)
```

```sql
EXPLAIN (COSTS OFF)
SELECT * FROM big_scores ORDER BY points DESC LIMIT 10;
```

```result
                           QUERY PLAN
-----------------------------------------------------------------
 Limit
   ->  Index Scan Backward using big_scores_points on big_scores
(2 rows)
```

The two orderings that do not match need a sort of the whole table:

```sql
EXPLAIN (COSTS OFF)
SELECT * FROM big_scores ORDER BY points NULLS FIRST LIMIT 10;
```

```result
              QUERY PLAN
--------------------------------------
 Limit
   ->  Sort
         Sort Key: points NULLS FIRST
         ->  Seq Scan on big_scores
(4 rows)
```

If the query needs that order, create an index in the same order:

```sql
CREATE INDEX big_scores_points_nf ON big_scores (points NULLS FIRST);

EXPLAIN (COSTS OFF)
SELECT * FROM big_scores ORDER BY points NULLS FIRST LIMIT 10;
```

```result
                        QUERY PLAN
-----------------------------------------------------------
 Limit
   ->  Index Scan using big_scores_points_nf on big_scores
(2 rows)
```

That index also serves `ORDER BY points DESC NULLS LAST` when scanned backward. The extra index costs space and write time, so add it only for a query that needs it, or change the query to the order the existing index already provides.

## Avoiding the problem

The most reliable fix is to keep `NULL` out of columns that are sorted: a `NOT NULL` constraint, or a default. Where a missing value is real, `COALESCE` in the `ORDER BY` is possible but changes the meaning:

```sql
SELECT player, COALESCE(points, 0) AS points
FROM scores
ORDER BY COALESCE(points, 0), player;
```

```result
 player | points
--------+--------
 Bob    |      0
 Dave   |      0
 Eve    |     72
 Carol  |     87
 Frank  |     91
 Alice  |     95
(6 rows)
```

A player who did not play now looks the same as one who scored zero, and an expression in the `ORDER BY` cannot use a plain index on `points`. Usually `NULLS FIRST` or `NULLS LAST` says what you mean more directly.

## In short

- `NULL` sorts as the largest value in PostgreSQL and Oracle, and as the smallest in MySQL, SQL Server, SQLite and BigQuery.
- With `ORDER BY ... DESC LIMIT n`, PostgreSQL returns the `NULL` rows first. Write `NULLS LAST`, or filter them out.
- Rankings, `DISTINCT ON` and pagination follow the same rule, so spell out `NULLS FIRST` or `NULLS LAST` and add a unique tie-breaker.
- MySQL and SQL Server need a `CASE` expression in `ORDER BY`.
- An index can serve only the `NULL` ordering it was built with, in either scan direction.
