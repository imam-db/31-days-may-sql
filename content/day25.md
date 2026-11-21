---
day: 25
title: "Outdated Statistics: Why Queries Suddenly Get Slow"
summary: "The planner chooses plans from table statistics, not from the data. How statistics go stale, how to see it in EXPLAIN ANALYZE, and how ANALYZE, per-column targets and extended statistics fix it."
teaser: "Plans come from statistics, not data: stale estimates, ANALYZE and extended statistics."
---

A query ran in a few milliseconds last week and now takes minutes. The SQL is the same, no index was dropped and the table is not much bigger. What changed is the plan, and the plan changed because the numbers the planner works from no longer describe the data.

PostgreSQL does not look at the table when it plans a query. It reads a summary kept in `pg_class` and `pg_statistic` (readable through the `pg_stats` view): how many rows and pages the table has, how many distinct values each column has, which values are most common and how often. From these it estimates how many rows each step will produce, and it picks join methods and scans from those estimates. This article uses PostgreSQL 18 and shows what happens when the summary is wrong.

Other databases keep similar statistics (SQL Server, Oracle and MySQL all have them) but refresh and expose them differently. The commands below are PostgreSQL's.

## What the planner knows

Two tables, a user table and an event log:

```sql
CREATE TABLE users (
    user_id  INT PRIMARY KEY,
    plan     TEXT NOT NULL
);

INSERT INTO users
SELECT g, CASE WHEN g % 10 = 0 THEN 'pro' ELSE 'free' END
FROM generate_series(1, 5000) g;

CREATE TABLE events (
    event_id    INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id     INT  NOT NULL,
    event_type  TEXT NOT NULL
);

-- Statistics only refresh when this script says so. Autovacuum would otherwise
-- analyze the table in the background and make the demonstration depend on timing.
ALTER TABLE events SET (autovacuum_enabled = false);

CREATE INDEX events_type_idx ON events (event_type);

INSERT INTO events (user_id, event_type)
SELECT 1 + g % 5000,
       CASE WHEN g % 10 = 0 THEN 'signup' ELSE 'page_view' END
FROM generate_series(1, 10000) g;

ANALYZE users;
ANALYZE events;
```

After `ANALYZE`, `pg_stats` has one row per column:

```sql
SELECT attname, n_distinct, null_frac, round(correlation::numeric, 2) AS correlation
FROM pg_stats
WHERE tablename = 'events'
ORDER BY attname;
```

```result
  attname   | n_distinct | null_frac | correlation
------------+------------+-----------+-------------
 event_id   |         -1 |         0 |        1.00
 event_type |          2 |         0 |        0.82
 user_id    |       -0.5 |         0 |        0.50
(3 rows)
```

`n_distinct` is the estimated number of distinct values. A negative number means a fraction of the row count: `-1` is "every row is different" (`event_id`), and `-0.5` would be "half as many distinct values as rows". `user_id` shows `-0.5`: 5,000 distinct values in 10,000 rows. Storing a ratio lets the estimate follow the table as it grows. `correlation` says how closely the column's order follows the physical order of the rows (1 means sorted). It is what makes a range scan on an index cheap or expensive.

For a column with few distinct values, the most common values and their frequencies are the part that matters most:

```sql
SELECT most_common_vals, most_common_freqs
FROM pg_stats
WHERE tablename = 'events' AND attname = 'event_type';
```

```result
  most_common_vals  | most_common_freqs
--------------------+-------------------
 {page_view,signup} | {0.9,0.1}
(1 row)
```

The planner now knows 90% of the rows are `page_view` and 10% are `signup`. At table level, `pg_class` keeps a row count and a page count:

```sql
SELECT reltuples, relpages
FROM pg_class
WHERE relname = 'events';
```

```result
 reltuples | relpages
-----------+----------
     10000 |       63
(1 row)
```

## Statistics that no longer match

Now a campaign starts and 15,000 `click` events arrive. Nothing has analyzed the table since the first 10,000 rows:

```sql
INSERT INTO events (user_id, event_type)
SELECT 1 + g % 5000, 'click'
FROM generate_series(1, 15000) g;
```

The planner has never seen the value `click`, and the stored frequencies cover all of the old rows. Here is what it expects against what it gets:

```sql
EXPLAIN (ANALYZE, TIMING OFF, SUMMARY OFF, BUFFERS OFF)
SELECT * FROM events WHERE event_type = 'click';
```

```result
                                                  QUERY PLAN
--------------------------------------------------------------------------------------------------------------
 Index Scan using events_type_idx on events  (cost=0.29..8.30 rows=1 width=17) (actual rows=15000.00 loops=1)
   Index Cond: (event_type = 'click'::text)
   Index Searches: 1
(3 rows)
```

The estimate is `rows=1` and the scan returned 15,000 rows. That gap between `rows=` and `actual rows=` is what to look for when a plan seems wrong. It is also why this query flips to a different join when a second table comes in:

```sql
EXPLAIN (COSTS OFF)
SELECT count(*)
FROM events e
JOIN users u USING (user_id)
WHERE e.event_type = 'click';
```

```result
                        QUERY PLAN
----------------------------------------------------------
 Aggregate
   ->  Nested Loop
         ->  Index Scan using events_type_idx on events e
               Index Cond: (event_type = 'click'::text)
         ->  Index Only Scan using users_pkey on users u
               Index Cond: (user_id = e.user_id)
(6 rows)
```

Believing there is about one `click` row, the planner picks a nested loop: find the click rows through the index, then do one primary-key lookup in `users` per row. With 15,000 rows that is 15,000 lookups where one pass over each table would do. On a table with a hundred times as many rows, this is the plan that takes minutes.

The table size estimate held up better, and the reason is worth knowing. The planner does not take `reltuples` at face value; it measures the table's current number of pages and scales the old rows-per-page density to it:

```sql
EXPLAIN SELECT * FROM events;
```

```result
                         QUERY PLAN
-------------------------------------------------------------
 Seq Scan on events  (cost=0.00..372.57 rows=22857 width=17)
(1 row)
```

`reltuples` still says 10,000, the table really has 25,000 rows, and the estimate is 22,857, close enough to be useful. A bulk insert of more of the same kind of rows is therefore not what hurts. What hurts is a change in the distribution of values, which the page count cannot reveal.

Run `ANALYZE` and the same two queries change:

```sql
ANALYZE events;

EXPLAIN (ANALYZE, TIMING OFF, SUMMARY OFF, BUFFERS OFF)
SELECT * FROM events WHERE event_type = 'click';
```

```result
                                         QUERY PLAN
--------------------------------------------------------------------------------------------
 Seq Scan on events  (cost=0.00..456.50 rows=15000 width=15) (actual rows=15000.00 loops=1)
   Filter: (event_type = 'click'::text)
   Rows Removed by Filter: 10000
(3 rows)
```

```sql
EXPLAIN (COSTS OFF)
SELECT count(*)
FROM events e
JOIN users u USING (user_id)
WHERE e.event_type = 'click';
```

```result
                     QUERY PLAN
----------------------------------------------------
 Aggregate
   ->  Hash Join
         Hash Cond: (e.user_id = u.user_id)
         ->  Seq Scan on events e
               Filter: (event_type = 'click'::text)
         ->  Hash
               ->  Seq Scan on users u
(7 rows)
```

The estimate is exact, a sequential scan is chosen over the index, and the join becomes a hash join that reads each table once. The frequencies have caught up with the data:

```sql
SELECT most_common_vals, most_common_freqs
FROM pg_stats
WHERE tablename = 'events' AND attname = 'event_type';
```

```result
     most_common_vals     | most_common_freqs
--------------------------+-------------------
 {click,page_view,signup} | {0.6,0.36,0.04}
(1 row)
```

A note on how small this example is: 25,000 rows is below the size `ANALYZE` samples (more on that below), so every row was read and these numbers come out the same on every run. On bigger tables the sample is random and the estimates vary slightly between runs.

## A mass delete

Deleting rows leaves the table with the same number of pages, because `DELETE` only marks rows dead. The row estimate stays where it was until `VACUUM` or `ANALYZE` updates it:

```sql
CREATE TABLE logs (
    log_id   INT PRIMARY KEY,
    level    TEXT NOT NULL,
    payload  TEXT
);
ALTER TABLE logs SET (autovacuum_enabled = false);

INSERT INTO logs
SELECT g, 'info', repeat('x', 50) FROM generate_series(1, 20000) g;
ANALYZE logs;

DELETE FROM logs WHERE log_id <= 16000;
```

```sql
SELECT count(*) AS live_rows FROM logs;
```

```result
 live_rows
-----------
      4000
(1 row)
```

```sql
EXPLAIN SELECT * FROM logs;
```

```result
                        QUERY PLAN
-----------------------------------------------------------
 Seq Scan on logs  (cost=0.00..428.00 rows=20000 width=60)
(1 row)
```

The table has 4,000 live rows and the planner expects 20,000. Plans for queries on `logs` assume five times too many rows until the table is vacuumed or analyzed. A scan that returns an estimated 20,000 rows may well choose a different join order than one that returns 4,000.

## Why statistics fall behind

Autovacuum analyzes a table when the number of rows changed since the last analyze exceeds a threshold:

```sql
SELECT name, setting
FROM pg_settings
WHERE name IN ('autovacuum_analyze_threshold',
               'autovacuum_analyze_scale_factor',
               'autovacuum_naptime')
ORDER BY name;
```

```result
              name               | setting
---------------------------------+---------
 autovacuum_analyze_scale_factor | 0.1
 autovacuum_analyze_threshold    | 50
 autovacuum_naptime              | 60
(3 rows)
```

The threshold is `autovacuum_analyze_threshold + autovacuum_analyze_scale_factor * reltuples`, so 50 rows plus 10% of the table by default, and autovacuum looks every `autovacuum_naptime` seconds (60). For a table of 100 million rows that means ten million changes before an automatic analyze. Several situations get past it:

- **A bulk load followed by an immediate query.** The load can finish well inside the one-minute naptime. Nothing has analyzed the new rows when the first report runs.
- **A change in distribution that stays under the threshold.** If 2% of a large table suddenly becomes the value everyone is querying (a new tenant, a campaign, one very busy customer), that is far below 10% of the rows and nothing triggers. The `click` example above is the small version of this.
- **Tables autovacuum never analyzes.** Temporary tables are never analyzed automatically, so analyze them yourself after filling them. The parent of a partitioned table is not analyzed by autovacuum either, which matters for queries that join or aggregate across partitions.
- **Autovacuum that is behind.** A long-running transaction, a busy server or a disabled worker can keep it from reaching the table.

To see how far a table has drifted, look at the modification counter:

```sql norun
SELECT relname,
       n_mod_since_analyze,
       last_analyze,
       last_autoanalyze
FROM pg_stat_user_tables
ORDER BY n_mod_since_analyze DESC
LIMIT 10;
```

A table with a large `n_mod_since_analyze` compared with its size, and an old `last_autoanalyze`, is a candidate. The counter is a hint only. The proof is `rows=` against `actual rows=` in `EXPLAIN (ANALYZE)` on the query that got slow.

## Fixing it

### Analyze, and analyze after loads

`ANALYZE table_name;` rebuilds the statistics with a sample of the table and is cheap compared to the queries it protects. For a nightly import or any other bulk job, run it as the last step of the job. It does not need to wait for autovacuum, and it can run inside the same transaction as the load:

```sql norun
BEGIN;
INSERT INTO orders SELECT * FROM staging_orders;
ANALYZE orders;
COMMIT;
```

Running a plain `ANALYZE;` with no table name analyzes every table in the database, which is something to schedule rather than to run casually on a large system.

### Make autovacuum analyze sooner on big tables

For a large table the 10% default is a lot of rows. A per-table setting lowers it:

```sql norun
ALTER TABLE orders SET (autovacuum_analyze_scale_factor = 0.02);
```

That makes autovacuum analyze `orders` after 2% of its rows have changed instead of 10%. `autovacuum_vacuum_scale_factor` is the separate setting for dead-row cleanup.

### Bigger sample and more detail for one column

`ANALYZE` reads `300 * default_statistics_target` rows, and the target is 100 by default, so 30,000 rows. It also keeps up to 100 most common values and 100 histogram buckets per column. I checked the sample on a table of a million rows with `ANALYZE VERBOSE`, which reports it:

```sql norun
ANALYZE VERBOSE big;
-- INFO:  "big": scanned 4480 of 4480 pages, containing 1000000 live rows and 0 dead rows;
--        30000 rows in sample, 1000000 estimated total rows

ALTER TABLE big ALTER COLUMN grp SET STATISTICS 1000;
ANALYZE VERBOSE big;
-- INFO:  "big": scanned 4480 of 4480 pages, containing 1000000 live rows and 0 dead rows;
--        300000 rows in sample, 1000000 estimated total rows
```

The sample size follows the largest target among the table's columns. A column with many distinct values and a skewed distribution (a few customers with most of the rows) can lose its rare-but-important values with only 100 slots. `ALTER TABLE ... ALTER COLUMN ... SET STATISTICS 1000` raises the target for that column alone (the maximum is 10,000). The cost is a slower `ANALYZE` and a larger `pg_statistic`, so do it for columns where `EXPLAIN (ANALYZE)` shows repeated bad estimates, not everywhere.

## Columns that are not independent

For `WHERE a = 1 AND b = 2` the planner multiplies the selectivity of each condition, as if the columns were unrelated. When one column determines the other, as a city does its country, that produces a serious underestimate:

```sql
CREATE TABLE places (
    id       INT PRIMARY KEY,
    city     TEXT NOT NULL,
    country  TEXT NOT NULL
);
ALTER TABLE places SET (autovacuum_enabled = false);

INSERT INTO places
SELECT g, 'city' || (g % 100), 'country' || ((g % 100) / 10)
FROM generate_series(1, 20000) g;

ANALYZE places;

EXPLAIN (ANALYZE, TIMING OFF, SUMMARY OFF, BUFFERS OFF)
SELECT * FROM places WHERE city = 'city7' AND country = 'country0';
```

```result
                                      QUERY PLAN
---------------------------------------------------------------------------------------
 Seq Scan on places  (cost=0.00..428.00 rows=20 width=19) (actual rows=200.00 loops=1)
   Filter: ((city = 'city7'::text) AND (country = 'country0'::text))
   Rows Removed by Filter: 19800
(3 rows)
```

There are 100 cities in 10 countries, each city in exactly one country. Filtering on the city alone selects 1% of the rows and the country adds nothing, so 200 rows match. The planner multiplies 1% by 10% and expects 20, a tenth of the real count. With a join added, an error like that can move the plan the same way as the stale `click` statistics did.

`CREATE STATISTICS` tells the planner about the relationship:

```sql
CREATE STATISTICS places_city_country (dependencies) ON city, country FROM places;
ANALYZE places;

EXPLAIN (ANALYZE, TIMING OFF, SUMMARY OFF, BUFFERS OFF)
SELECT * FROM places WHERE city = 'city7' AND country = 'country0';
```

```result
                                       QUERY PLAN
----------------------------------------------------------------------------------------
 Seq Scan on places  (cost=0.00..428.00 rows=200 width=19) (actual rows=200.00 loops=1)
   Filter: ((city = 'city7'::text) AND (country = 'country0'::text))
   Rows Removed by Filter: 19800
(3 rows)
```

The estimate is now 200. `dependencies` covers equality conditions on the columns together (column order does not matter). Other kinds are `ndistinct`, for `GROUP BY` on several columns, and `mcv`, which stores the most common value combinations and also handles other kinds of conditions. Extended statistics are only computed by `ANALYZE`, so they go stale in the same way. Where a column is an expression such as `lower(email)`, an expression index also makes PostgreSQL collect statistics on the expression, which is another reason the planner estimates those queries well.

## Prepared statements and old plans

A common belief is that a prepared statement keeps its old plan after you run `ANALYZE` and needs `DISCARD PLANS`. That is not the case in PostgreSQL 18. `ANALYZE` invalidates cached plans that depend on the analyzed table, and the next `EXECUTE` plans again. I checked this in one psql session with `plan_cache_mode = force_generic_plan`, on a 20,000-row table of `'view'` events, adding 20,000 `'click'` rows afterwards:

```sql norun
PREPARE q(text) AS SELECT * FROM ev WHERE typ = $1;
EXPLAIN (COSTS OFF) EXECUTE q('click');   -- Seq Scan on ev   (generic plan)
-- ... INSERT of the click rows, no ANALYZE yet ...
EXPLAIN (COSTS OFF) EXECUTE q('click');   -- Seq Scan on ev   (same cached plan)
ANALYZE ev;
EXPLAIN (COSTS OFF) EXECUTE q('click');   -- Bitmap Heap Scan on ev   (replanned)
```

So the cached plan stays only while nothing invalidates it, and the stale statistics are the problem, not the cache. The prepared-statement issue that remains is a different one: a generic plan is built without knowing the parameter value, so it uses average selectivity and can be wrong for a skewed column. `plan_cache_mode = force_custom_plan` makes PostgreSQL plan with the actual value each time.

## When a plan changed and you do not know why

1. Run `EXPLAIN (ANALYZE)` on the slow query and compare `rows=` with `actual rows=` at each node. The first node where they diverge by a large factor is where the plan went wrong.
2. Check `pg_stat_user_tables` for the tables involved: `n_mod_since_analyze`, `last_analyze`, `last_autoanalyze`.
3. Run `ANALYZE table_name;` and run `EXPLAIN (ANALYZE)` again. If the estimates and the plan improve, you have found it.
4. Find out why autovacuum did not do it: scale factor too high for the table size, a temporary or partitioned table, or autovacuum blocked.
5. If the estimates are still wrong after `ANALYZE`, the cause is a skewed column (raise its statistics target) or related columns (extended statistics).

## In short

- The planner estimates rows from statistics, and a plan is only as good as the estimate. A big gap between `rows=` and `actual rows=` in `EXPLAIN (ANALYZE)` is the sign to look for.
- Statistics fall behind after bulk loads and after sudden shifts in the data. Autoanalyze waits for 50 rows plus 10% of the table by default, and skips temporary tables and partitioned parents.
- Run `ANALYZE` at the end of load jobs. Lower the analyze scale factor per table for large tables.
- `ANALYZE` samples 300 times the statistics target (30,000 rows by default). Raise the target for a skewed column, and use `CREATE STATISTICS` for columns that depend on each other.
