---
day: 11
title: "Slowly Changing Dimensions in Raw SQL"
summary: "Answer 'what did this customer look like on that date?' from a history table that only stores start dates: LEAD for the end date, the right boundary operators, range types, and what bitemporal adds."
teaser: "What a customer looked like on a given date, from a history table that only stores start dates."
---

A support ticket arrives: "I was charged the wrong amount during the 11.11 sale. I was on the Pro plan back then, but your system says Free." You open the `customers` database and find one row per customer with the current tier. The customer downgraded last week, and nothing in that table remembers they were ever on Pro.

Questions about the past need a table that stores the past. The usual design is a history table with one row per change, and the query that reads it is called an AS OF query: what did this customer look like on a given date? The examples below run on PostgreSQL 18 and use only SQL.

## The data

Each row says "from this moment on, the customer is on this tier". It does not say when the row stops being true.

```sql
CREATE TABLE customers (
    customer_id  INT PRIMARY KEY,
    name         TEXT NOT NULL
);

CREATE TABLE customer_tier_history (
    history_id      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id     INT NOT NULL REFERENCES customers,
    tier            TEXT NOT NULL,
    price_monthly   NUMERIC(10,2) NOT NULL,
    effective_from  TIMESTAMP NOT NULL
);

INSERT INTO customers VALUES
    (42, 'Rina'), (43, 'Dewi'), (44, 'Budi'), (45, 'Sari');

INSERT INTO customer_tier_history (customer_id, tier, price_monthly, effective_from) VALUES
    (42, 'free',  0.00, '2024-01-15 10:00'),
    (42, 'pro',  19.00, '2024-09-03 14:22'),
    (42, 'free',  0.00, '2025-04-30 08:10'),
    (43, 'free',  0.00, '2024-02-01 09:00'),
    (43, 'pro',  19.00, '2024-11-11 00:00'),
    (44, 'pro',  19.00, '2024-06-10 12:00'),
    (45, 'free',  0.00, '2024-12-01 16:30');
```

Customer 42 is the one from the ticket. The other three are there for edge cases: Dewi (43) upgraded at exactly midnight on 11 November, Budi (44) has a single row, and Sari (45) signed up after the sale.

There is no row for 11 November in Rina's history, and there should not be. She went Pro on 3 September and stayed there until 30 April, so the September row was still the true one during the sale. The query has to work that out.

## The naive join

The first attempt is a join with a date filter:

```sql
SELECT c.customer_id, h.tier, h.price_monthly, h.effective_from
FROM customers c
JOIN customer_tier_history h ON h.customer_id = c.customer_id
WHERE c.customer_id = 42
  AND h.effective_from <= '2024-11-11'
ORDER BY h.effective_from;
```

```result
 customer_id | tier | price_monthly |   effective_from
-------------+------+---------------+---------------------
          42 | free |          0.00 | 2024-01-15 10:00:00
          42 | pro  |         19.00 | 2024-09-03 14:22:00
(2 rows)
```

Both rows started before the sale, so both match. The right one is the latest of them. For a single customer you could add `ORDER BY effective_from DESC LIMIT 1`, but `LIMIT` cannot pick one row per customer when you want the answer for everyone at once.

## Each row is a time range

A history row is valid from its `effective_from` until the next row of the same customer starts. The end date is not stored, but it is the next row's start date, and `LEAD()` reads the next row within a partition:

```sql
SELECT customer_id, tier, effective_from,
       LEAD(effective_from) OVER (
           PARTITION BY customer_id
           ORDER BY effective_from
       ) AS effective_to
FROM customer_tier_history
WHERE customer_id = 42
ORDER BY effective_from;
```

```result
 customer_id | tier |   effective_from    |    effective_to
-------------+------+---------------------+---------------------
          42 | free | 2024-01-15 10:00:00 | 2024-09-03 14:22:00
          42 | pro  | 2024-09-03 14:22:00 | 2025-04-30 08:10:00
          42 | free | 2025-04-30 08:10:00 |
(3 rows)
```

The last row has no successor, so `effective_to` is `NULL`. It means "no end yet", which is what the current row is.

## The AS OF query

With ranges, the question "which row was active on date X" becomes a filter: the range started on or before X and either ends after X or has not ended.

```sql
WITH tier_ranges AS (
    SELECT customer_id, tier, price_monthly, effective_from,
           LEAD(effective_from) OVER (
               PARTITION BY customer_id
               ORDER BY effective_from
           ) AS effective_to
    FROM customer_tier_history
)
SELECT customer_id, tier, price_monthly
FROM tier_ranges
WHERE customer_id = 42
  AND effective_from <= '2024-11-11'
  AND (effective_to > '2024-11-11' OR effective_to IS NULL);
```

```result
 customer_id | tier | price_monthly
-------------+------+---------------
          42 | pro  |         19.00
(1 row)
```

Rina was on Pro at $19.00, so the ticket is valid. The filter has to sit outside the CTE, after `LEAD()` has seen all the rows. If you put `effective_from <= '2024-11-11'` inside the window query instead, the Free row from 2025 disappears before `LEAD()` runs, and the Pro row would look like it never ends.

The same query without `customer_id = 42` gives everyone's tier on that date:

```sql
WITH tier_ranges AS (
    SELECT customer_id, tier, price_monthly, effective_from,
           LEAD(effective_from) OVER (
               PARTITION BY customer_id
               ORDER BY effective_from
           ) AS effective_to
    FROM customer_tier_history
)
SELECT customer_id, tier, price_monthly
FROM tier_ranges
WHERE effective_from <= '2024-11-11'
  AND (effective_to > '2024-11-11' OR effective_to IS NULL)
ORDER BY customer_id;
```

```result
 customer_id | tier | price_monthly
-------------+------+---------------
          42 | pro  |         19.00
          43 | pro  |         19.00
          44 | pro  |         19.00
(3 rows)
```

Sari (45) is absent because she did not exist yet on that date, which is the right answer. Dewi's Pro row started at exactly `2024-11-11 00:00`, and it counts for that instant.

## Boundaries: one side inclusive, one exclusive

A range includes its start and excludes its end. At the moment a change happens, the new row is in effect and the old one is not. Dewi's tier change sits exactly on the boundary, so she shows what happens when both sides are inclusive:

```sql
WITH tier_ranges AS (
    SELECT customer_id, tier, effective_from,
           LEAD(effective_from) OVER (
               PARTITION BY customer_id
               ORDER BY effective_from
           ) AS effective_to
    FROM customer_tier_history
)
SELECT customer_id, tier
FROM tier_ranges
WHERE customer_id = 43
  AND effective_from <= '2024-11-11'
  AND (effective_to >= '2024-11-11' OR effective_to IS NULL)
ORDER BY effective_from;
```

```result
 customer_id | tier
-------------+------
          43 | free
          43 | pro
(2 rows)
```

With `>=`, the instant of the change belongs to both the Free and the Pro range, and Dewi comes back twice. With `>` she comes back once, as Pro. A duplicate like this only appears for timestamps that match a change exactly, so it tends to slip through testing and show up in a month-end report.

## The same thing without LEAD

If you only ever need the latest row at or before X, `DISTINCT ON` does it without computing ranges:

```sql
SELECT DISTINCT ON (customer_id) customer_id, tier, price_monthly
FROM customer_tier_history
WHERE effective_from <= '2024-11-11'
ORDER BY customer_id, effective_from DESC;
```

```result
 customer_id | tier | price_monthly
-------------+------+---------------
          42 | pro  |         19.00
          43 | pro  |         19.00
          44 | pro  |         19.00
(3 rows)
```

This returns the same three rows. The range version is still worth having, because once every row has a start and an end you can join it to other tables. Here is what each order paid for, according to the tier at the time it was placed:

```sql
CREATE TABLE orders (
    order_id     INT PRIMARY KEY,
    customer_id  INT NOT NULL REFERENCES customers,
    ordered_at   TIMESTAMP NOT NULL
);

INSERT INTO orders VALUES
    (1, 42, '2024-11-11 09:15'),
    (2, 42, '2025-05-02 18:40'),
    (3, 43, '2024-11-10 23:59'),
    (4, 43, '2024-11-11 00:00');

CREATE VIEW customer_tier_ranges AS
SELECT customer_id, tier, price_monthly, effective_from,
       COALESCE(
           LEAD(effective_from) OVER (PARTITION BY customer_id ORDER BY effective_from),
           'infinity'
       ) AS effective_to
FROM customer_tier_history;

SELECT o.order_id, o.customer_id, o.ordered_at, r.tier, r.price_monthly
FROM orders o
JOIN customer_tier_ranges r
  ON r.customer_id = o.customer_id
 AND o.ordered_at >= r.effective_from
 AND o.ordered_at <  r.effective_to
ORDER BY o.order_id;
```

```result
 order_id | customer_id |     ordered_at      | tier | price_monthly
----------+-------------+---------------------+------+---------------
        1 |          42 | 2024-11-11 09:15:00 | pro  |         19.00
        2 |          42 | 2025-05-02 18:40:00 | free |          0.00
        3 |          43 | 2024-11-10 23:59:00 | free |          0.00
        4 |          43 | 2024-11-11 00:00:00 | pro  |         19.00
(4 rows)
```

The view replaces the `NULL` end date with `'infinity'`, a value PostgreSQL's `timestamp` type supports. That turns the open-ended range into a plain `>= start AND < end` test and removes the `OR ... IS NULL` branch from every query that uses the view. Orders 3 and 4 are one minute apart and land on different tiers, which is the boundary rule at work.

## A reusable function

For the "everyone on date X" case, a function saves copying the query around:

```sql
CREATE FUNCTION customer_tier_as_of(as_of TIMESTAMP)
RETURNS TABLE (customer_id INT, tier TEXT, price_monthly NUMERIC(10,2))
LANGUAGE sql STABLE
AS $$
    SELECT r.customer_id, r.tier, r.price_monthly
    FROM customer_tier_ranges r
    WHERE r.effective_from <= as_of AND as_of < r.effective_to
$$;

SELECT * FROM customer_tier_as_of('2024-11-11') ORDER BY customer_id;
```

```result
 customer_id | tier | price_monthly
-------------+------+---------------
          42 | pro  |         19.00
          43 | pro  |         19.00
          44 | pro  |         19.00
(3 rows)
```

Because it returns a table, you can aggregate over it. Monthly recurring revenue at the end of September 2024:

```sql
SELECT SUM(price_monthly) AS mrr
FROM customer_tier_as_of('2024-09-30');
```

```result
  mrr
-------
 38.00
(1 row)
```

On 30 September, Rina (42) and Budi (44) were on Pro. Dewi was still free and Sari had not signed up.

The window runs over the whole history table even when you want one customer, unless the planner can push your filter below it. For a filter on the partition column it can:

```sql
EXPLAIN (COSTS OFF)
SELECT * FROM customer_tier_ranges WHERE customer_id = 42;
```

```result
                           QUERY PLAN
-----------------------------------------------------------------
 WindowAgg
   Window: w1 AS (ORDER BY customer_tier_history.effective_from)
   ->  Sort
         Sort Key: customer_tier_history.effective_from
         ->  Seq Scan on customer_tier_history
               Filter: (customer_id = 42)
(6 rows)
```

The `Filter: (customer_id = 42)` is on the scan below the `WindowAgg`, so only that customer's rows are windowed. A filter on `effective_from` does not get this treatment, which is why the date conditions in the queries above belong outside the view.

## Letting PostgreSQL enforce the ranges

The history table above relies on your application never inserting two rows with the same start, and never leaving a gap or an overlap by hand-editing. If you store the validity as a range value instead, PostgreSQL can reject overlaps. PostgreSQL 18 adds `WITHOUT OVERLAPS` to primary keys and unique constraints for exactly this. It needs the `btree_gist` extension so that the plain `customer_id` column can take part in the underlying index:

```sql
CREATE EXTENSION btree_gist;

CREATE TABLE tier_period (
    customer_id  INT NOT NULL,
    tier         TEXT NOT NULL,
    valid        TSRANGE NOT NULL,
    PRIMARY KEY (customer_id, valid WITHOUT OVERLAPS)
);

INSERT INTO tier_period VALUES
    (42, 'free', '[2024-01-15 10:00, 2024-09-03 14:22)'),
    (42, 'pro',  '[2024-09-03 14:22, 2025-04-30 08:10)'),
    (42, 'free', '[2025-04-30 08:10, infinity)');
```

An overlapping row is refused:

```sql norun
INSERT INTO tier_period VALUES
    (42, 'pro', '[2024-11-01, 2024-12-01)');
```

```text
ERROR:  conflicting key value violates exclusion constraint "tier_period_pkey"
DETAIL:  Key (customer_id, valid)=(42, ["2024-11-01 00:00:00","2024-12-01 00:00:00")) conflicts with existing key (customer_id, valid)=(42, ["2024-09-03 14:22:00","2025-04-30 08:10:00")).
```

The AS OF query then becomes a containment test, with no `LEAD()` and no boundary operators to get right, because the range type already knows its start is inclusive and its end is exclusive:

```sql
SELECT customer_id, tier
FROM tier_period
WHERE valid @> TIMESTAMP '2024-11-11'
ORDER BY customer_id;
```

```result
 customer_id | tier
-------------+------
          42 | pro
(1 row)
```

The constraint stops overlaps but not gaps. Nothing prevents a customer from having no row between two dates, so closing the old row and opening the new one in the same transaction is still your job. The price is a different write path: you now update `valid` on the old row when a change happens instead of only inserting.

## Built-in versioned tables in other databases

The SQL:2011 standard defines system-versioned tables, where the database keeps old row versions itself and lets you query them with `FOR SYSTEM_TIME AS OF`. SQL Server (temporal tables, since 2016) and MariaDB (since 10.3) implement it with that syntax. BigQuery has `FOR SYSTEM_TIME AS OF` as well, as time travel limited to a recent window (seven days by default). Oracle has its own Flashback Query (`AS OF TIMESTAMP`). PostgreSQL and MySQL have no built-in equivalent, which is why a history table like the one above is the usual approach there.

One difference to keep in mind: system versioning records when the database learned something, not when it became true in the real world. That is the bitemporal problem, covered next.

## Two timelines: bitemporal tables

Suppose Rina actually upgraded to Pro on 15 August 2024, but the integration that writes upgrades was down, and the row was only inserted on 3 September. For a while the database was wrong. An AS OF query for 20 August, run on 25 August, said Free. The same query run on 10 September says Pro. Both answers were correct for what the database knew at the time.

If you need to reproduce reports exactly as they were printed, or audit what was known when, you keep two pairs of dates: when the fact was true (`valid_*`) and when the database held it (`recorded_*`). A correction never overwrites a row. It closes the old row by setting `recorded_to` and inserts the new truth:

```sql
CREATE TABLE tier_bitemporal (
    row_id         INT PRIMARY KEY,
    customer_id    INT NOT NULL,
    tier           TEXT NOT NULL,
    valid_from     TIMESTAMP NOT NULL,
    valid_to       TIMESTAMP NOT NULL,
    recorded_from  TIMESTAMP NOT NULL,
    recorded_to    TIMESTAMP NOT NULL
);

-- 15 Jan: Rina signs up on Free, open-ended
INSERT INTO tier_bitemporal VALUES
    (1, 42, 'free', '2024-01-15', 'infinity', '2024-01-15', 'infinity');

-- 3 Sep: the missed upgrade (effective 15 Aug) is recorded.
-- Row 1 is superseded, not changed in place.
UPDATE tier_bitemporal SET recorded_to = '2024-09-03 14:22' WHERE row_id = 1;

INSERT INTO tier_bitemporal VALUES
    (2, 42, 'free', '2024-01-15', '2024-08-15', '2024-09-03 14:22', 'infinity'),
    (3, 42, 'pro',  '2024-08-15', 'infinity',   '2024-09-03 14:22', 'infinity');
```

Now an AS OF query has two dates: the date you are asking about, and the date as of which you want the database's knowledge. What did we think Rina's tier was on 20 August, as of 25 August?

```sql
SELECT tier
FROM tier_bitemporal
WHERE customer_id = 42
  AND valid_from    <= '2024-08-20' AND '2024-08-20' < valid_to
  AND recorded_from <= '2024-08-25' AND '2024-08-25' < recorded_to;
```

```result
 tier
------
 free
(1 row)
```

And the same question asked as of 10 September:

```sql
SELECT tier
FROM tier_bitemporal
WHERE customer_id = 42
  AND valid_from    <= '2024-08-20' AND '2024-08-20' < valid_to
  AND recorded_from <= '2024-09-10' AND '2024-09-10' < recorded_to;
```

```result
 tier
------
 pro
(1 row)
```

Financial, insurance and audit systems need this. Most applications do not, and a single-timeline history table is enough when late corrections are rare and nobody needs to reproduce an old report.

## In short

- A history table that stores only start dates holds time ranges. `LEAD()` over the customer's partition gives each row its end date, and the last row has none.
- The AS OF filter is `effective_from <= X AND X < effective_to`: start inclusive, end exclusive. Using `>=` on the end gives duplicates exactly at change timestamps.
- Apply the date filter after `LEAD()`, never inside the query that computes it.
- A range column with `WITHOUT OVERLAPS` (PostgreSQL 18) lets the database refuse overlapping rows and reduces AS OF to `valid @> X`.
- When late corrections matter, a second pair of dates records when each fact was known, and corrections close old rows instead of overwriting them.
