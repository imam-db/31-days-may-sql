---
day: 10
title: "Build a Football League Table with SQL"
summary: "Turn one row per match into a league table with points, goal difference, ranking with tie-breakers and a last-five form string, using a two-rows-per-match view, conditional aggregation and window functions."
teaser: "From one row per match to a full table with tie-breakers and a last-five form column."
---

A league table looks like something an application has to maintain, but it can be computed from a single table of results, one row per match. The pieces are a view that gives every match two rows (one per team), conditional aggregation for wins, draws and losses, a window function for the ranking with tie-breakers, and a window function again for the last-five form string. The queries run on PostgreSQL 18. The MySQL differences are noted where they matter.

The same queries work on decades of real Premier League results. The data here is a small invented league instead, so that every query can be run as printed and the ties are visible: six clubs, ten gameweeks, one postponed match.

## The data

```sql
CREATE TABLE matches (
    match_id    INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    season      TEXT NOT NULL,
    gameweek    INT  NOT NULL,
    match_date  DATE NOT NULL,
    home_team   TEXT NOT NULL,
    away_team   TEXT NOT NULL,
    home_score  INT  NOT NULL,
    away_score  INT  NOT NULL
);

INSERT INTO matches (season, gameweek, match_date, home_team, away_team, home_score, away_score) VALUES
    ('2023/24',  1, '2023-08-12', 'Ashford', 'Fenwick', 1, 1),
    ('2023/24',  1, '2023-08-12', 'Eastleigh', 'Brightwater', 0, 3),
    ('2023/24',  1, '2023-08-12', 'Carrow', 'Dunmore', 2, 2),
    ('2023/24',  2, '2023-08-19', 'Eastleigh', 'Ashford', 1, 0),
    ('2023/24',  2, '2023-08-19', 'Fenwick', 'Dunmore', 2, 1),
    ('2023/24',  2, '2023-08-19', 'Carrow', 'Brightwater', 0, 0),
    ('2023/24',  3, '2023-08-26', 'Ashford', 'Dunmore', 1, 1),
    ('2023/24',  3, '2023-08-26', 'Carrow', 'Eastleigh', 3, 2),
    ('2023/24',  3, '2023-08-26', 'Fenwick', 'Brightwater', 3, 1),
    ('2023/24',  4, '2023-09-02', 'Carrow', 'Ashford', 1, 0),
    ('2023/24',  4, '2023-09-02', 'Dunmore', 'Brightwater', 2, 2),
    ('2023/24',  4, '2023-09-02', 'Fenwick', 'Eastleigh', 2, 1),
    ('2023/24',  5, '2023-09-09', 'Ashford', 'Brightwater', 2, 0),
    ('2023/24',  5, '2023-10-18', 'Fenwick', 'Carrow', 3, 1),   -- postponed, played later
    ('2023/24',  5, '2023-09-09', 'Dunmore', 'Eastleigh', 0, 0),
    ('2023/24',  6, '2023-09-16', 'Fenwick', 'Ashford', 1, 0),
    ('2023/24',  6, '2023-09-16', 'Brightwater', 'Eastleigh', 1, 0),
    ('2023/24',  6, '2023-09-16', 'Dunmore', 'Carrow', 0, 0),
    ('2023/24',  7, '2023-09-23', 'Ashford', 'Eastleigh', 1, 2),
    ('2023/24',  7, '2023-09-23', 'Dunmore', 'Fenwick', 1, 1),
    ('2023/24',  7, '2023-09-23', 'Brightwater', 'Carrow', 1, 0),
    ('2023/24',  8, '2023-09-30', 'Dunmore', 'Ashford', 0, 0),
    ('2023/24',  8, '2023-09-30', 'Eastleigh', 'Carrow', 1, 2),
    ('2023/24',  8, '2023-09-30', 'Brightwater', 'Fenwick', 1, 3),
    ('2023/24',  9, '2023-10-07', 'Ashford', 'Carrow', 1, 2),
    ('2023/24',  9, '2023-10-07', 'Brightwater', 'Dunmore', 1, 0),
    ('2023/24',  9, '2023-10-07', 'Eastleigh', 'Fenwick', 0, 3),
    ('2023/24', 10, '2023-10-14', 'Brightwater', 'Ashford', 1, 1),
    ('2023/24', 10, '2023-10-14', 'Carrow', 'Fenwick', 1, 2),
    ('2023/24', 10, '2023-10-14', 'Eastleigh', 'Dunmore', 2, 2),
    -- first gameweek of another season, used later to show tied teams
    ('2022/23',  1, '2022-08-13', 'Ashford', 'Brightwater', 1, 0),
    ('2022/23',  1, '2022-08-13', 'Carrow', 'Dunmore', 1, 0),
    ('2022/23',  1, '2022-08-13', 'Eastleigh', 'Fenwick', 1, 1);
```

Two details of the design come from mistakes that are easy to make. The match date is a real `DATE`, not text: text dates sort alphabetically, so `'10/12/2023'` comes before `'9/1/2023'`. And the gameweek is stored separately from the date, because a postponed match keeps its gameweek and moves to another day. Fenwick v Carrow, gameweek 5, was played on 18 October, after gameweek 10. That match matters for the form string later.

## Two rows per match

A match row says two things: for the home club it is goals for and against, for the away club the same numbers reversed. A league table is about clubs, so the first step is a view that returns one row per club per match:

```sql
CREATE VIEW team_matches AS
SELECT season, gameweek, match_date,
       home_team AS club, away_team AS opponent, 'home' AS venue,
       home_score AS goals_for, away_score AS goals_against,
       CASE WHEN home_score > away_score THEN 'W'
            WHEN home_score = away_score THEN 'D'
            ELSE 'L' END AS result
FROM matches
UNION ALL
SELECT season, gameweek, match_date,
       away_team, home_team, 'away',
       away_score, home_score,
       CASE WHEN away_score > home_score THEN 'W'
            WHEN away_score = home_score THEN 'D'
            ELSE 'L' END
FROM matches;
```

`UNION ALL`, not `UNION`: `UNION` would add a de-duplication step and could silently drop a genuine repeat, and no duplicates are expected here. Thirty matches become sixty rows. Fenwick's season looks like this:

```sql
SELECT gameweek, match_date, venue, opponent, goals_for, goals_against, result
FROM team_matches
WHERE season = '2023/24' AND club = 'Fenwick'
ORDER BY match_date;
```

```result
 gameweek | match_date | venue |  opponent   | goals_for | goals_against | result
----------+------------+-------+-------------+-----------+---------------+--------
        1 | 2023-08-12 | away  | Ashford     |         1 |             1 | D
        2 | 2023-08-19 | home  | Dunmore     |         2 |             1 | W
        3 | 2023-08-26 | home  | Brightwater |         3 |             1 | W
        4 | 2023-09-02 | home  | Eastleigh   |         2 |             1 | W
        6 | 2023-09-16 | home  | Ashford     |         1 |             0 | W
        7 | 2023-09-23 | away  | Dunmore     |         1 |             1 | D
        8 | 2023-09-30 | away  | Brightwater |         3 |             1 | W
        9 | 2023-10-07 | away  | Eastleigh   |         3 |             0 | W
       10 | 2023-10-14 | away  | Carrow      |         2 |             1 | W
        5 | 2023-10-18 | home  | Carrow      |         3 |             1 | W
(10 rows)
```

The same unpivot idea applies whenever a row holds two parties: sender and receiver, buyer and seller, payer and payee.

## Counting results

Once each row is one club's experience of one match, the table is a `GROUP BY club`. Wins, draws and losses are conditional counts, and points follow from them:

```sql
SELECT club,
       COUNT(*)                                  AS p,
       COUNT(*) FILTER (WHERE result = 'W')      AS w,
       COUNT(*) FILTER (WHERE result = 'D')      AS d,
       COUNT(*) FILTER (WHERE result = 'L')      AS l,
       SUM(goals_for)                            AS gf,
       SUM(goals_against)                        AS ga,
       SUM(goals_for - goals_against)            AS gd,
       3 * COUNT(*) FILTER (WHERE result = 'W')
         + COUNT(*) FILTER (WHERE result = 'D')  AS pts
FROM team_matches
WHERE season = '2023/24'
GROUP BY club
ORDER BY pts DESC, gd DESC, gf DESC, club;
```

```result
    club     | p  | w | d | l | gf | ga | gd | pts
-------------+----+---+---+---+----+----+----+-----
 Fenwick     | 10 | 8 | 2 | 0 | 21 |  8 | 13 |  26
 Carrow      | 10 | 4 | 3 | 3 | 12 | 12 |  0 |  15
 Brightwater | 10 | 4 | 3 | 3 | 11 | 11 |  0 |  15
 Dunmore     | 10 | 0 | 8 | 2 |  9 | 11 | -2 |   8
 Eastleigh   | 10 | 2 | 2 | 6 |  9 | 17 | -8 |   8
 Ashford     | 10 | 1 | 4 | 5 |  7 | 10 | -3 |   7
(6 rows)
```

`COUNT(*) FILTER (WHERE ...)` is PostgreSQL and SQLite syntax (and standard SQL). The portable spelling, which also works in MySQL and SQL Server, is `SUM(CASE WHEN result = 'W' THEN 1 ELSE 0 END)`. One pass over the data replaces a separate query for each of wins, draws and losses.

Carrow and Brightwater have the same 15 points, and Dunmore and Eastleigh both have 8. The order within those pairs comes from tie-breakers.

## Positions and tie-breakers

The Premier League orders clubs by points, then goal difference, then goals scored. Head-to-head results come into play only for ties that survive those three, and this article does not model them. Other leagues order differently (La Liga looks at head-to-head first), so the tie-breakers are rules to be read from the competition, not something to hard-code from memory.

The ordering goes into the window function that numbers the rows:

```sql
WITH totals AS (
    SELECT club,
           SUM(goals_for)                         AS gf,
           SUM(goals_for - goals_against)         AS gd,
           3 * COUNT(*) FILTER (WHERE result = 'W')
             + COUNT(*) FILTER (WHERE result = 'D') AS pts
    FROM team_matches
    WHERE season = '2023/24'
    GROUP BY club
)
SELECT RANK() OVER (ORDER BY pts DESC, gd DESC, gf DESC) AS pos,
       club, pts, gd, gf
FROM totals
ORDER BY pos, club;
```

```result
 pos |    club     | pts | gd | gf
-----+-------------+-----+----+----
   1 | Fenwick     |  26 | 13 | 21
   2 | Carrow      |  15 |  0 | 12
   3 | Brightwater |  15 |  0 | 11
   4 | Dunmore     |   8 | -2 |  9
   5 | Eastleigh   |   8 | -8 |  9
   6 | Ashford     |   7 | -3 |  7
(6 rows)
```

Carrow ranks above Brightwater: equal on points and goal difference (both 0), 12 goals scored against 11. Dunmore ranks above Eastleigh on goal difference alone, -2 against -8.

`RANK()` and `ROW_NUMBER()` differ when two clubs are equal on every criterion. The first season in the table (the 2022/23 rows, one gameweek) has two such pairs:

```sql
SELECT pos AS rank_pos,
       ROW_NUMBER() OVER (ORDER BY pts DESC, gd DESC, gf DESC, club) AS row_no,
       club, pts, gd, gf
FROM (
    SELECT RANK() OVER (ORDER BY pts DESC, gd DESC, gf DESC) AS pos,
           club, pts, gd, gf
    FROM (
        SELECT club,
               SUM(goals_for)                         AS gf,
               SUM(goals_for - goals_against)         AS gd,
               3 * COUNT(*) FILTER (WHERE result = 'W')
                 + COUNT(*) FILTER (WHERE result = 'D') AS pts
        FROM team_matches
        WHERE season = '2022/23'
        GROUP BY club
    ) AS t
) AS r
ORDER BY row_no;
```

```result
 rank_pos | row_no |    club     | pts | gd | gf
----------+--------+-------------+-----+----+----
        1 |      1 | Ashford     |   3 |  1 |  1
        1 |      2 | Carrow      |   3 |  1 |  1
        3 |      3 | Eastleigh   |   1 |  0 |  1
        3 |      4 | Fenwick     |   1 |  0 |  1
        5 |      5 | Brightwater |   0 | -1 |  0
        5 |      6 | Dunmore     |   0 | -1 |  0
(6 rows)
```

Ashford and Carrow both have 3 points, +1 and 1 goal scored. `RANK()` gives them the same position 1 and skips to 3 for the next club, which is how real tables show clubs level on everything. `ROW_NUMBER()` has to separate them, and it can only do that by an arbitrary rule (here the club name). Without such a final column in the `ORDER BY`, the order of fully tied rows is whatever the executor happens to produce, so the position would be unstable between runs. Use `RANK()` unless the table must give each club a unique number.

## The form column

Form is the results of a club's last five matches, written like `WWDLW`. Number each club's matches from the most recent, keep the first five, and join their result letters in date order, oldest on the left and the latest on the right:

```sql
WITH ranked AS (
    SELECT club, result, match_date,
           ROW_NUMBER() OVER (PARTITION BY club ORDER BY match_date DESC) AS rn
    FROM team_matches
    WHERE season = '2023/24'
)
SELECT club,
       STRING_AGG(result, '' ORDER BY match_date) AS form
FROM ranked
WHERE rn <= 5
GROUP BY club
ORDER BY club;
```

```result
    club     | form
-------------+-------
 Ashford     | LLDLD
 Brightwater | WWLWD
 Carrow      | LWWLL
 Dunmore     | DDDLD
 Eastleigh   | LWLLD
 Fenwick     | DWWWW
(6 rows)
```

A plain `LIMIT 5` limits the whole result, not each club, so the window function does the per-club cut. (PostgreSQL has another way, `CROSS JOIN LATERAL (... ORDER BY ... LIMIT 5)`, which runs the limited subquery once per club.) In MySQL the aggregate is `GROUP_CONCAT(result ORDER BY match_date SEPARATOR '')`, and SQL Server 2017 and later has `STRING_AGG(result, '') WITHIN GROUP (ORDER BY match_date)`.

### Order by date, not by gameweek

A gameweek number looks like a clock but is not one. Compare the form computed from dates with the form computed from gameweeks for the two clubs that played the postponed match:

```sql
WITH ranked AS (
    SELECT club, result, gameweek, match_date,
           ROW_NUMBER() OVER (PARTITION BY club ORDER BY match_date DESC) AS rn_date,
           ROW_NUMBER() OVER (PARTITION BY club ORDER BY gameweek DESC, match_date DESC) AS rn_gw
    FROM team_matches
    WHERE season = '2023/24'
)
SELECT club,
       STRING_AGG(result, '' ORDER BY match_date) FILTER (WHERE rn_date <= 5) AS form_by_date,
       STRING_AGG(result, '' ORDER BY gameweek)   FILTER (WHERE rn_gw   <= 5) AS form_by_gameweek
FROM ranked
GROUP BY club
HAVING club IN ('Carrow', 'Fenwick', 'Ashford')
ORDER BY club;
```

```result
  club   | form_by_date | form_by_gameweek
---------+--------------+------------------
 Ashford | LLDLD        | LLDLD
 Carrow  | LWWLL        | DLWWL
 Fenwick | DWWWW        | WDWWW
(3 rows)
```

For Ashford both columns agree. For Carrow and Fenwick they do not, because the gameweek 5 match played on 18 October is the latest result by date but sits in the middle by gameweek. The form string shows what happened most recently, so it must follow the calendar.

## Assembling it into a function

Season, a cut-off date and a home/away filter are the three things a table page usually offers. In PostgreSQL a SQL function with default parameters covers them, and MySQL would use a stored procedure for the same job. The cut-off is a date, not a gameweek, so that a postponed match counts only once it has been played.

```sql
CREATE FUNCTION standings(
    p_season TEXT,
    p_as_of  DATE DEFAULT NULL,    -- NULL: the whole season
    p_venue  TEXT DEFAULT NULL     -- NULL: all matches, or 'home' / 'away'
)
RETURNS TABLE (pos BIGINT, club TEXT, p BIGINT, w BIGINT, d BIGINT, l BIGINT,
               gf BIGINT, ga BIGINT, gd BIGINT, pts BIGINT, form TEXT)
LANGUAGE sql STABLE AS $$
    WITH tm AS (
        SELECT t.*
        FROM team_matches t
        WHERE t.season = p_season
          AND (p_as_of IS NULL OR t.match_date <= p_as_of)
          AND (p_venue IS NULL OR t.venue = p_venue)
    ),
    totals AS (
        SELECT tm.club,
               COUNT(*)                                  AS p,
               COUNT(*) FILTER (WHERE tm.result = 'W')   AS w,
               COUNT(*) FILTER (WHERE tm.result = 'D')   AS d,
               COUNT(*) FILTER (WHERE tm.result = 'L')   AS l,
               SUM(tm.goals_for)                         AS gf,
               SUM(tm.goals_against)                     AS ga,
               SUM(tm.goals_for - tm.goals_against)      AS gd,
               3 * COUNT(*) FILTER (WHERE tm.result = 'W')
                 + COUNT(*) FILTER (WHERE tm.result = 'D') AS pts
        FROM tm
        GROUP BY tm.club
    ),
    ranked AS (
        SELECT tm.club, tm.result, tm.match_date,
               ROW_NUMBER() OVER (PARTITION BY tm.club ORDER BY tm.match_date DESC) AS rn
        FROM tm
    ),
    recent AS (
        SELECT r.club, STRING_AGG(r.result, '' ORDER BY r.match_date) AS form
        FROM ranked r
        WHERE r.rn <= 5
        GROUP BY r.club
    )
    SELECT RANK() OVER (ORDER BY t.pts DESC, t.gd DESC, t.gf DESC),
           t.club, t.p, t.w, t.d, t.l, t.gf, t.ga, t.gd, t.pts, r.form
    FROM totals t
    JOIN recent r ON r.club = t.club
    ORDER BY 1, t.club
$$;
```

The full 2023/24 table:

```sql
SELECT * FROM standings('2023/24');
```

```result
 pos |    club     | p  | w | d | l | gf | ga | gd | pts | form
-----+-------------+----+---+---+---+----+----+----+-----+-------
   1 | Fenwick     | 10 | 8 | 2 | 0 | 21 |  8 | 13 |  26 | DWWWW
   2 | Carrow      | 10 | 4 | 3 | 3 | 12 | 12 |  0 |  15 | LWWLL
   3 | Brightwater | 10 | 4 | 3 | 3 | 11 | 11 |  0 |  15 | WWLWD
   4 | Dunmore     | 10 | 0 | 8 | 2 |  9 | 11 | -2 |   8 | DDDLD
   5 | Eastleigh   | 10 | 2 | 2 | 6 |  9 | 17 | -8 |   8 | LWLLD
   6 | Ashford     | 10 | 1 | 4 | 5 |  7 | 10 | -3 |   7 | LLDLD
(6 rows)
```

The table as it stood after gameweek 10 was played, on 14 October. Fenwick and Carrow have played one match fewer than the others because the postponed match is still to come:

```sql
SELECT pos, club, p, pts, gd, form FROM standings('2023/24', DATE '2023-10-14');
```

```result
 pos |    club     | p  | pts | gd | form
-----+-------------+----+-----+----+-------
   1 | Fenwick     |  9 |  23 | 11 | WDWWW
   2 | Carrow      |  9 |  15 |  2 | DLWWL
   3 | Brightwater | 10 |  15 |  0 | WWLWD
   4 | Dunmore     | 10 |   8 | -2 | DDDLD
   5 | Eastleigh   | 10 |   8 | -8 | LWLLD
   6 | Ashford     | 10 |   7 | -3 | LLDLD
(6 rows)
```

Home and away tables use the venue filter:

```sql
SELECT pos, club, p, w, d, l, pts, form FROM standings('2023/24', NULL, 'home');
```

```result
 pos |    club     | p | w | d | l | pts | form
-----+-------------+---+---+---+---+-----+-------
   1 | Fenwick     | 5 | 5 | 0 | 0 |  15 | WWWWW
   2 | Brightwater | 5 | 3 | 1 | 1 |  10 | WWLWD
   3 | Carrow      | 5 | 2 | 2 | 1 |   8 | DDWWL
   4 | Ashford     | 5 | 1 | 2 | 2 |   5 | DDWLL
   5 | Dunmore     | 5 | 0 | 5 | 0 |   5 | DDDDD
   6 | Eastleigh   | 5 | 1 | 1 | 3 |   4 | LWLLD
(6 rows)
```

```sql
SELECT pos, club, p, w, d, l, pts, form FROM standings('2023/24', NULL, 'away');
```

```result
 pos |    club     | p | w | d | l | pts | form
-----+-------------+---+---+---+---+-----+-------
   1 | Fenwick     | 5 | 3 | 2 | 0 |  11 | DDWWW
   2 | Carrow      | 5 | 2 | 1 | 2 |   7 | DLWWL
   3 | Brightwater | 5 | 1 | 2 | 2 |   5 | WDLDL
   4 | Eastleigh   | 5 | 1 | 1 | 3 |   4 | LLDLW
   5 | Dunmore     | 5 | 0 | 3 | 2 |   3 | DLDLD
   6 | Ashford     | 5 | 0 | 2 | 3 |   2 | LLLDD
(6 rows)
```

The form column of a filtered table covers the last five matches inside the filter, so the home table's form is the last five home matches. That matches what sites show for home and away tables.

## Checking the table

A table built by aggregation can be audited against invariants that must hold. Goals scored equal goals conceded across the league, wins equal losses, and total points equal three per decided match plus two per draw:

```sql
SELECT SUM(gf) = SUM(ga) AS goals_balance,
       SUM(w) = SUM(l)   AS results_balance,
       SUM(pts) = (SELECT 3 * COUNT(*) FILTER (WHERE home_score <> away_score)
                        + 2 * COUNT(*) FILTER (WHERE home_score =  away_score)
                   FROM matches WHERE season = '2023/24') AS points_balance
FROM standings('2023/24');
```

```result
 goals_balance | results_balance | points_balance
---------------+-----------------+----------------
 t             | t               | t
(1 row)
```

These checks catch a lot but not everything: a match loaded twice still balances. The quickest extra check on real data is games played: at the end of a season `SELECT DISTINCT p FROM standings('2023/24')` should return a single row, and a misspelled club name shows up as an extra row in the table.

## One pass over the table

The `UNION ALL` view reads `matches` twice. When that matters, a `LATERAL` join to a `VALUES` list produces both rows of each match in a single read:

```sql
SELECT t.club, SUM(t.goals_for) AS gf, SUM(t.goals_against) AS ga
FROM matches m
CROSS JOIN LATERAL (VALUES (m.home_team, m.home_score, m.away_score),
                           (m.away_team, m.away_score, m.home_score)) AS t(club, goals_for, goals_against)
WHERE m.season = '2023/24'
GROUP BY t.club
ORDER BY t.club;
```

```result
    club     | gf | ga
-------------+----+----
 Ashford     |  7 | 10
 Brightwater | 11 | 11
 Carrow      | 12 | 12
 Dunmore     |  9 | 11
 Eastleigh   |  9 | 17
 Fenwick     | 21 |  8
(6 rows)
```

These are the same goal totals as in the table above. On 30 rows it makes no difference; on a large table it reads `matches` once instead of twice. In MySQL the usual form is the `UNION ALL`.

## In short

- Give each match two rows, one per team, with goals for and against reversed. Everything else is then a plain `GROUP BY club`.
- Count outcomes with `COUNT(*) FILTER (WHERE ...)` or `SUM(CASE ...)`; derive points from the counts.
- Position is a window function over the tie-breaker columns; use `RANK()` so that clubs level on every criterion share a position.
- Form needs "last five per club": number rows per club in descending date order, filter, then `STRING_AGG` in ascending order. Order by date, not gameweek.
- Check the result against invariants such as goals for equal goals against, and equal games played at the end of the season.
