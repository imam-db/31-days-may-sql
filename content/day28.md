---
day: 28
title: "Why DELETE Does Not Shrink a PostgreSQL Table: MVCC, VACUUM and Bloat"
summary: "DELETE and UPDATE leave dead row versions behind. What VACUUM, VACUUM FULL and autovacuum do with them, what a long transaction does to cleanup, and how to measure bloat."
teaser: "Dead row versions, what VACUUM and VACUUM FULL do with them, and long transactions."
---

A common first encounter with PostgreSQL maintenance goes like this. You delete half the rows of a big table, check its size, and it is exactly the same. You run `VACUUM`, and the size is still the same. You run `VACUUM FULL`, the table finally shrinks, and every query on it waits while that runs.

None of this is a bug. It follows from how PostgreSQL keeps readers and writers from blocking each other, and once you see the mechanism the rest of the maintenance advice (autovacuum settings, long transactions, `fillfactor`, `REINDEX`) stops looking like a list of unrelated tricks.

Everything here is PostgreSQL-specific. InnoDB in MySQL also keeps old row versions for concurrent readers, but it stores them in undo logs instead of leaving them in the table, so the symptoms differ. The examples run on PostgreSQL 18. The demo tables are created with `autovacuum_enabled = off` so the background worker does not clean up in the middle of an experiment; you would not do that in production.

## What DELETE and UPDATE do to a row

A `DELETE` does not erase anything. It stamps the row version with the ID of the deleting transaction, and the version stays in the table until no running transaction could still need to see it. An `UPDATE` is a delete plus an insert: the old version is stamped, and a new version is written, usually at a different position in the table.

Row counts come from the cumulative statistics system, which sessions report to asynchronously. The small procedure below makes a session publish its pending numbers and pause for a second, and the examples call it before every statistics query:

```sql
CREATE PROCEDURE flush_stats() LANGUAGE sql
AS 'SELECT pg_stat_force_next_flush(), pg_sleep(1)';
```

The position of a row version is its `ctid`, a (page, slot) pair. Ten small rows fit on the first page, and updating row 3 moves it:

```sql
CREATE TABLE orders (
    order_id  INT PRIMARY KEY,
    status    TEXT NOT NULL
) WITH (autovacuum_enabled = off);

INSERT INTO orders
SELECT g, 'new' FROM generate_series(1, 10) g;

SELECT order_id, ctid FROM orders WHERE order_id = 3;
```

```result
 order_id | ctid
----------+-------
        3 | (0,3)
(1 row)
```

```sql
UPDATE orders SET status = 'shipped' WHERE order_id = 3;

SELECT order_id, ctid FROM orders WHERE order_id = 3;
```

```result
 order_id |  ctid
----------+--------
        3 | (0,11)
(1 row)
```

The new version sits in slot 11 of page 0. The version in slot 3 is still there, invisible to new queries, and it is what the statistics view counts as a dead tuple:

```sql
CALL flush_stats();

SELECT n_live_tup, n_dead_tup
FROM pg_stat_user_tables
WHERE relname = 'orders';
```

```result
 n_live_tup | n_dead_tup
------------+------------
         10 |          1
(1 row)
```

Update the same row 99 more times and the table holds 10 live rows and 100 dead versions:

```sql
DO $$ BEGIN FOR i IN 1..99 LOOP UPDATE orders SET status = 'update ' || i WHERE order_id = 3; END LOOP; END $$;

CALL flush_stats();

SELECT n_live_tup, n_dead_tup
FROM pg_stat_user_tables
WHERE relname = 'orders';
```

```result
 n_live_tup | n_dead_tup
------------+------------
         10 |        100
(1 row)
```

`n_dead_tup` comes from the statistics system and is an estimate, not a count taken from the pages. For this table it is exact because nothing else touched it. PostgreSQL can also remove dead versions of an updated row on its own when it next visits a nearly full page (called pruning), so on a busy table the number can lag behind the physical state. Treat it as a gauge.

## A DELETE does not shrink the file

Take a table of 100,000 rows and delete the first half. The `size_log` table records the number of 8 kB pages at each step, so we can compare them without printing sizes that depend on the platform:

```sql
CREATE TABLE events (
    event_id  INT PRIMARY KEY,
    payload   TEXT NOT NULL
) WITH (autovacuum_enabled = off);

INSERT INTO events
SELECT g, repeat('x', 100) FROM generate_series(1, 100000) g;

CREATE TABLE size_log (
    step   SERIAL PRIMARY KEY,
    label  TEXT,
    pages  BIGINT
) WITH (autovacuum_enabled = off);

INSERT INTO size_log (label, pages)
SELECT 'loaded', pg_relation_size('events') / 8192;

DELETE FROM events WHERE event_id <= 50000;

INSERT INTO size_log (label, pages)
SELECT 'after delete', pg_relation_size('events') / 8192;
```

The statistics now show half the table as dead:

```sql
CALL flush_stats();

SELECT n_live_tup,
       n_dead_tup,
       ROUND(100.0 * n_dead_tup / NULLIF(n_live_tup + n_dead_tup, 0), 1) AS dead_pct
FROM pg_stat_user_tables
WHERE relname = 'events';
```

```result
 n_live_tup | n_dead_tup | dead_pct
------------+------------+----------
      50000 |      50000 |     50.0
(1 row)
```

```sql
SELECT label, pages = first_value(pages) OVER (ORDER BY step) AS same_as_loaded
FROM size_log
ORDER BY step;
```

```result
    label     | same_as_loaded
--------------+----------------
 loaded       | t
 after delete | t
(2 rows)
```

The file did not change. A sequential scan of `events` still reads every page, including the ones that now hold nothing but dead rows.

## Finding bloat

The same query without the `WHERE relname = ...` filter, ordered by `n_dead_tup DESC`, gives a per-database overview. Add `last_autovacuum` and `last_vacuum` to see whether cleanup is happening at all:

```sql norun
SELECT relname,
       pg_size_pretty(pg_relation_size(relid)) AS table_size,
       n_live_tup, n_dead_tup,
       ROUND(100.0 * n_dead_tup / NULLIF(n_live_tup + n_dead_tup, 0), 1) AS dead_pct,
       last_autovacuum, last_vacuum
FROM pg_stat_user_tables
ORDER BY n_dead_tup DESC
LIMIT 20;
```

Two caveats. These numbers are estimates, and they only count dead rows, not free space inside pages that earlier cleanup already reclaimed but that was never given back to the operating system. There is also no universal threshold: 30% dead on a small table does not matter, and 5% of a table that is hundreds of gigabytes may be many gigabytes. If you need exact figures for one table, the `pgstattuple` extension reads the pages and reports dead tuple and free space percentages (`CREATE EXTENSION pgstattuple; SELECT * FROM pgstattuple('events');`). It scans the whole table, so run it deliberately.

## What plain VACUUM does

`VACUUM` goes through the table, finds row versions that no transaction can see any more, and marks their space as reusable. It also updates the visibility map and the free space map, and with `VACUUM ANALYZE` it refreshes planner statistics.

```sql
VACUUM events;

INSERT INTO size_log (label, pages)
SELECT 'after vacuum', pg_relation_size('events') / 8192;

CALL flush_stats();

SELECT n_live_tup, n_dead_tup
FROM pg_stat_user_tables
WHERE relname = 'events';
```

```result
 n_live_tup | n_dead_tup
------------+------------
      50000 |          0
(1 row)
```

The dead rows are gone, and the file is still the same size. The space is not lost, though. Insert another 50,000 rows and they go into the freed pages instead of extending the file:

```sql
INSERT INTO events
SELECT g, repeat('x', 100) FROM generate_series(100001, 150000) g;

INSERT INTO size_log (label, pages)
SELECT 'after reinsert', pg_relation_size('events') / 8192;

SELECT label, pages = first_value(pages) OVER (ORDER BY step) AS same_as_loaded
FROM size_log
ORDER BY step;
```

```result
     label      | same_as_loaded
----------------+----------------
 loaded         | t
 after delete   | t
 after vacuum   | t
 after reinsert | t
(4 rows)
```

So for a table with steady churn (rows deleted and inserted at a similar rate), plain `VACUUM` is all that is needed: the file stops growing at a size that fits the working set.

### When VACUUM does shrink the file

There is one exception to "plain `VACUUM` never returns space". If the pages at the end of the file are completely empty, `VACUUM` truncates them and gives that space back to the operating system. It takes a short exclusive lock to do so and skips the truncation if it cannot get the lock quickly.

The `events` delete above emptied the front of the table, which cannot be truncated. Delete from the end instead:

```sql
CREATE TABLE logs (
    log_id  INT PRIMARY KEY,
    msg     TEXT NOT NULL
) WITH (autovacuum_enabled = off);

INSERT INTO logs
SELECT g, repeat('x', 100) FROM generate_series(1, 100000) g;

INSERT INTO size_log (label, pages)
SELECT 'logs loaded', pg_relation_size('logs') / 8192;

DELETE FROM logs WHERE log_id > 50000;

VACUUM logs;

SELECT pg_relation_size('logs') / 8192 < (SELECT pages FROM size_log WHERE label = 'logs loaded') AS truncated;
```

```result
 truncated
-----------
 t
(1 row)
```

This is why deleting old rows from a table that is filled in time order (the oldest are at the front) does not shrink it, while deleting the newest rows does. If the table is partitioned by time, dropping a partition avoids the whole question: the file is removed and no dead rows exist.

### Locking

Plain `VACUUM` takes a `SHARE UPDATE EXCLUSIVE` lock on the table. That mode does not conflict with `SELECT`, `INSERT`, `UPDATE` or `DELETE`, so the application keeps working. It does conflict with itself and with schema changes such as `ALTER TABLE`, which will wait until the vacuum finishes. I checked the first part by holding that lock in one session and running an `UPDATE` on the same table in another: the update went through.

## VACUUM FULL

`VACUUM FULL` writes a new copy of the table containing only live rows and swaps it in. That is the only built-in way to shrink a table whose free space is spread through the file. `events` is full again after the reinsert, so delete every second row first and then rewrite it:

```sql
DELETE FROM events WHERE event_id % 2 = 0;
```

```sql norun
VACUUM FULL events;

SELECT pg_relation_size('events') / 8192
     < (SELECT pages FROM size_log WHERE label = 'loaded') AS smaller_than_loaded;
```

```text
 smaller_than_loaded
---------------------
 t
(1 row)
```

The result depends on what else is open on the server, because `VACUUM FULL` keeps any row that another open transaction might still see, so treat this block as an observation and not something to rerun on a busy system. On an otherwise idle database the table went from 1725 pages to 863; exact page counts depend on platform and version.

The price is an `ACCESS EXCLUSIVE` lock for the whole rewrite. I confirmed it by running `VACUUM FULL` on a table and reading `pg_locks` from a second session, which showed `AccessExclusiveLock` granted. That mode blocks every other access to the table, reads included, and the rewrite needs enough free disk for a second copy of the table and its indexes. How long it takes depends on table size and disk speed; plan it as downtime, or look at online tools such as the `pg_repack` and `pg_squeeze` extensions, which rewrite the table while it stays available (they have their own requirements, so read their documentation first).

## Long transactions hold back cleanup

`VACUUM` can only remove a dead version if no running transaction could still see it. One transaction left open (an idle connection after a `BEGIN`, a forgotten report, an abandoned replication slot) pins everything that died after it started, in every table of the database. It is one of the most common reasons for a table that stays bloated despite vacuuming.

This needs two sessions, so here is what I ran, as two `psql` processes started a couple of seconds apart. Session A opens a snapshot and sleeps; session B deletes rows and vacuums:

```sql norun
-- session A
BEGIN ISOLATION LEVEL REPEATABLE READ;
SELECT count(*) FROM jobs;      -- 1000 rows; takes the snapshot
SELECT pg_sleep(8);
COMMIT;

-- session B, while A is sleeping
DELETE FROM jobs WHERE job_id <= 500;
VACUUM (VERBOSE) jobs;
```

The relevant line of `VACUUM VERBOSE` in session B:

```text
tuples: 0 removed, 1000 remain, 500 are dead but not yet removable
```

Once session A had committed, the same `VACUUM (VERBOSE) jobs` reported:

```text
tuples: 500 removed, 500 remain, 0 are dead but not yet removable
```

The phrase to look for is "dead but not yet removable". To find the culprit, list sessions that have an open transaction, oldest first. `backend_xmin` is the snapshot horizon a session is holding:

```sql norun
SELECT pid, state, backend_xmin, now() - xact_start AS xact_age,
       left(query, 80) AS query
FROM pg_stat_activity
WHERE xact_start IS NOT NULL
ORDER BY xact_start;
```

Sessions in state `idle in transaction` are the usual suspects. Setting `idle_in_transaction_session_timeout` makes the server end them. Also check `pg_replication_slots` and `pg_prepared_xacts` for old entries, because they hold back the horizon in the same way.

## Autovacuum

The autovacuum launcher starts a worker for a table when the number of dead tuples exceeds `autovacuum_vacuum_threshold + autovacuum_vacuum_scale_factor * reltuples`. These are the defaults:

```sql
SELECT name, setting
FROM pg_settings
WHERE name IN ('autovacuum_vacuum_threshold',
               'autovacuum_vacuum_scale_factor',
               'autovacuum_naptime')
ORDER BY name;
```

```result
              name              | setting
--------------------------------+---------
 autovacuum_naptime             | 60
 autovacuum_vacuum_scale_factor | 0.2
 autovacuum_vacuum_threshold    | 50
(3 rows)
```

That is 50 dead rows plus 20% of the table, checked once a minute (`autovacuum_naptime` is in seconds). Since PostgreSQL 13 there is a similar trigger for insert-only tables (`autovacuum_vacuum_insert_threshold`, default 1000, with its own scale factor) so that the visibility map gets set after bulk loads.

The 20% default is reasonable for a table of ten thousand rows and too lax for one with a hundred million: 20 million dead rows must pile up first. Both the trigger and the speed can be overridden per table:

```sql
CREATE TABLE jobs (
    job_id  INT PRIMARY KEY,
    state   TEXT NOT NULL
);

ALTER TABLE jobs SET (
    autovacuum_vacuum_scale_factor = 0.02,
    autovacuum_vacuum_cost_limit   = 1000
);

SELECT reloptions FROM pg_class WHERE relname = 'jobs';
```

```result
                               reloptions
-------------------------------------------------------------------------
 {autovacuum_vacuum_scale_factor=0.02,autovacuum_vacuum_cost_limit=1000}
(1 row)
```

The first setting starts autovacuum at 2% dead rows instead of 20%. The second raises the work budget per throttling pause: autovacuum is deliberately throttled by a cost budget so it does not saturate the disk, so a table with heavy write traffic may need a larger limit, not just an earlier trigger. After a very large delete you do not have to wait for the next autovacuum round: run `VACUUM` on that table yourself. It will not shrink the file unless the deleted rows were at the end, as shown above.

## The visibility map and index-only scans

The visibility map has one bit per table page, set by `VACUUM` when every row on the page is visible to all transactions. An `Index Only Scan` uses it to skip the table: if the page is all-visible, the answer is in the index entry alone, otherwise the executor must visit the page to check row visibility. EXPLAIN reports those visits as `Heap Fetches`.

A freshly loaded table has an empty map. The planner knows how much of the table is all-visible (from `pg_class.relallvisible`) and takes that into account in its costs, so before the first vacuum it does not choose an index-only scan here:

```sql
CREATE TABLE accounts (
    id       INT PRIMARY KEY,
    balance  INT NOT NULL
) WITH (autovacuum_enabled = off);

INSERT INTO accounts SELECT g, 0 FROM generate_series(1, 100000) g;

EXPLAIN (ANALYZE, COSTS OFF, TIMING OFF, SUMMARY OFF, BUFFERS OFF)
SELECT id FROM accounts WHERE id BETWEEN 1 AND 1000;
```

```result
                               QUERY PLAN
------------------------------------------------------------------------
 Bitmap Heap Scan on accounts (actual rows=1000.00 loops=1)
   Recheck Cond: ((id >= 1) AND (id <= 1000))
   Heap Blocks: exact=5
   ->  Bitmap Index Scan on accounts_pkey (actual rows=1000.00 loops=1)
         Index Cond: ((id >= 1) AND (id <= 1000))
         Index Searches: 1
(6 rows)
```

After `VACUUM` the same query is an index-only scan that never touches the table:

```sql
VACUUM accounts;

EXPLAIN (ANALYZE, COSTS OFF, TIMING OFF, SUMMARY OFF, BUFFERS OFF)
SELECT id FROM accounts WHERE id BETWEEN 1 AND 1000;
```

```result
                                  QUERY PLAN
-------------------------------------------------------------------------------
 Index Only Scan using accounts_pkey on accounts (actual rows=1000.00 loops=1)
   Index Cond: ((id >= 1) AND (id <= 1000))
   Heap Fetches: 0
   Index Searches: 1
(4 rows)
```

Now update those 1,000 rows. The pages that hold their new versions are no longer all-visible, and the old versions still have index entries:

```sql
UPDATE accounts SET balance = 1 WHERE id <= 1000;

EXPLAIN (ANALYZE, COSTS OFF, TIMING OFF, SUMMARY OFF, BUFFERS OFF)
SELECT id FROM accounts WHERE id BETWEEN 1 AND 1000;
```

```result
                                  QUERY PLAN
-------------------------------------------------------------------------------
 Index Only Scan using accounts_pkey on accounts (actual rows=1000.00 loops=1)
   Index Cond: ((id >= 1) AND (id <= 1000))
   Heap Fetches: 2000
   Index Searches: 1
(4 rows)
```

The plan is still an index-only scan, but each of the 2,000 index entries (1,000 old, 1,000 new) needs a visit to the table. It is sometimes said that the planner "falls back to an index scan" when vacuum is behind. What actually happens is that the plan keeps its shape and the work per row quietly goes back to what an ordinary index scan would do. When an index-only query gets slower over time, `Heap Fetches` in `EXPLAIN (ANALYZE)` tells you whether the visibility map is the reason.

## Index bloat

Every row version needs its own entry in every index of the table, unless the update is HOT (next section). So an update of 10,000 rows adds 10,000 entries to each index, and the entries for the old versions are only removed by `VACUUM`. Even after that, the index file does not shrink, for the same reason the table does not: the space is reusable but stays in the file. To see it, update an indexed column and compare sizes before and after:

```sql
CREATE TABLE users (
    user_id     INT PRIMARY KEY,
    email       TEXT NOT NULL,
    last_login  INT NOT NULL DEFAULT 0
) WITH (autovacuum_enabled = off);

CREATE INDEX users_email_idx ON users (email);

INSERT INTO users (user_id, email)
SELECT g, 'u' || g || '@example.com' FROM generate_series(1, 10000) g;

CREATE TABLE index_size_log (label TEXT, bytes BIGINT) WITH (autovacuum_enabled = off);

INSERT INTO index_size_log
SELECT 'loaded', pg_relation_size('users_email_idx');

UPDATE users SET email = 'x' || email;

VACUUM users;

INSERT INTO index_size_log
SELECT 'updated and vacuumed', pg_relation_size('users_email_idx');

SELECT (SELECT bytes FROM index_size_log WHERE label = 'updated and vacuumed')
     > (SELECT bytes FROM index_size_log WHERE label = 'loaded') AS index_grew;
```

```result
 index_grew
------------
 t
(1 row)
```

`REINDEX ... CONCURRENTLY` builds a fresh copy of the index next to the old one and swaps them, without blocking reads or writes:

```sql
REINDEX INDEX CONCURRENTLY users_email_idx;

SELECT pg_relation_size('users_email_idx')
     < (SELECT bytes FROM index_size_log WHERE label = 'updated and vacuumed') AS index_shrank;
```

```result
 index_shrank
--------------
 t
(1 row)
```

It takes longer than a plain `REINDEX`, needs disk for both copies, cannot run inside a transaction block, and if it fails it leaves an invalid index behind that you have to drop. `REINDEX TABLE CONCURRENTLY users` does the same for every index on the table.

## HOT updates

A Heap-Only Tuple (HOT) update avoids the index growth above. It is possible when two conditions hold: the update changes no indexed column, and the new version fits on the same page as the old one. The index entry keeps pointing to the old version, which links to the new one inside the page.

Whether the page has room is controlled by `fillfactor` (default 100, meaning pages are filled completely on insert). Compare two fresh copies of `users`, one with the default and one with `fillfactor = 80`, and update the non-indexed column `last_login` on every tenth row:

```sql
CREATE TABLE users100 (LIKE users INCLUDING ALL)
WITH (fillfactor = 100, autovacuum_enabled = off);

CREATE TABLE users80 (LIKE users INCLUDING ALL)
WITH (fillfactor = 80, autovacuum_enabled = off);

INSERT INTO users100 SELECT * FROM users;
INSERT INTO users80 SELECT * FROM users;

UPDATE users100 SET last_login = 1 WHERE user_id % 10 = 0;
UPDATE users80 SET last_login = 1 WHERE user_id % 10 = 0;

CALL flush_stats();

SELECT relname, n_tup_upd, n_tup_hot_upd
FROM pg_stat_user_tables
WHERE relname IN ('users100', 'users80')
ORDER BY relname;
```

```result
 relname  | n_tup_upd | n_tup_hot_upd
----------+-----------+---------------
 users100 |      1000 |             0
 users80  |      1000 |          1000
(2 rows)
```

All 1,000 updates on `users80` were HOT and none on `users100`, whose pages were full. Updating an indexed column is never HOT, whatever the fillfactor:

```sql
UPDATE users80 SET email = 'y' || email WHERE user_id % 10 = 1;

CALL flush_stats();

SELECT n_tup_upd, n_tup_hot_upd
FROM pg_stat_user_tables
WHERE relname = 'users80';
```

```result
 n_tup_upd | n_tup_hot_upd
-----------+---------------
      2000 |          1000
(1 row)
```

The cost of a lower `fillfactor` is a larger table (about 25% more pages at 80), so use it on tables that are updated often and where the updated columns are not indexed, not everywhere. Dead versions created by HOT updates can also be cleaned up by pruning, without waiting for `VACUUM`, which is the other reason such tables stay compact.

## In short

- `DELETE` and `UPDATE` leave old row versions in the table. Plain `VACUUM` makes their space reusable but does not shrink the file, except for empty pages at the very end.
- A table with steady churn reaches a stable size under plain `VACUUM` or autovacuum. `VACUUM FULL` is for a one-off shrink and takes an `ACCESS EXCLUSIVE` lock.
- When vacuum seems to do nothing, look for old transactions in `pg_stat_activity` and for "dead but not yet removable" in `VACUUM VERBOSE`.
- For large or hot tables, lower `autovacuum_vacuum_scale_factor` for that table.
- Slow index-only scans: read `Heap Fetches` in `EXPLAIN (ANALYZE)`.
- Updates that change indexed columns grow every index. Updates that do not, with free space on the page, are HOT.
