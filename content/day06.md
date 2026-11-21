---
day: 6
title: "Implicit Type Coercion That Kills Your Index"
summary: "Comparing a string column to a number makes MySQL convert every row and skip the index, while PostgreSQL refuses to run the query; PostgreSQL has its own index-killing casts, shown here with real plans."
teaser: "A string column compared to a number: MySQL scans everything, PostgreSQL has its own index-killing casts."
---

A `customers` table has an index on `phone_number`, a `VARCHAR` column, and someone writes this lookup without the quotes:

```sql norun
SELECT * FROM customers WHERE phone_number = 08123456789;
```

PostgreSQL rejects it with an error. MySQL runs it and returns rows, but it cannot use the index on `phone_number`, so it reads the whole table. On a few thousand rows nobody notices. On a table of a few million rows the same lookup goes from a millisecond to seconds.

The PostgreSQL examples below were run on PostgreSQL 18. The MySQL examples follow documented MySQL 8.0 behaviour; I did not run them for this article, so treat the plan output as representative and check it on your own server.

## The data

```sql
CREATE TABLE customers (
    id            INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name          TEXT,
    phone_number  VARCHAR(20),
    created_at    TIMESTAMP
);

INSERT INTO customers (name, phone_number, created_at)
SELECT 'Customer ' || i,
       '08' || LPAD(i::TEXT, 10, '0'),
       TIMESTAMP '2025-01-01' + (i % 365) * INTERVAL '1 day'
FROM generate_series(1, 100000) AS i;

-- the same subscriber, typed in three different ways
INSERT INTO customers (name, phone_number)
VALUES ('Extra A', '8123456789'),
       ('Extra B', '08123456789'),
       ('Extra C', '0008123456789');

CREATE INDEX idx_customers_phone ON customers (phone_number);
ANALYZE customers;

-- keep plans deterministic: no parallel workers
SET max_parallel_workers_per_gather = 0;
```

The first generated number is `080000000001` (twelve characters). The three extra rows hold the same number with and without leading zeros, which matters in a moment.

## PostgreSQL: matching types use the index

```sql
EXPLAIN (COSTS OFF)
SELECT * FROM customers WHERE phone_number = '080000000001';
```

```result
                         QUERY PLAN
-------------------------------------------------------------
 Index Scan using idx_customers_phone on customers
   Index Cond: ((phone_number)::text = '080000000001'::text)
(2 rows)
```

The `(phone_number)::text` in the condition is only a relabel: `VARCHAR` and `TEXT` share the same comparison operator, so the index is still used.

## PostgreSQL: a number against a string column is an error

PostgreSQL has no operator for `character varying = integer` and does not invent one:

```sql norun
SELECT * FROM customers WHERE phone_number = 123;
```

```text
ERROR:  operator does not exist: character varying = integer
LINE 1: SELECT * FROM customers WHERE phone_number = 123;
                                                   ^
HINT:  No operator matches the given name and argument types. You might need to add explicit type casts.
```

This is the safe behaviour: the mistake shows up the first time the query runs, not in production after the table has grown.

## MySQL: the same query runs, and the column is converted

In MySQL, comparing a string column with a number is allowed. The manual's rule for this case is that the operands are compared as floating-point numbers, so for every row MySQL converts `phone_number` to a number and compares that with your literal. An index on `phone_number` is sorted by string, not by that converted value, so it cannot be used to find matching rows.

```sql norun
CREATE TABLE customers_mysql (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    phone_number  VARCHAR(20),
    INDEX idx_phone (phone_number)
);

INSERT INTO customers_mysql (phone_number)
WITH RECURSIVE cte AS (
    SELECT 1 AS n UNION ALL SELECT n + 1 FROM cte WHERE n < 1000
)
SELECT CONCAT('08', LPAD(n, 10, '0')) FROM cte;

-- A: string literal, uses the index
EXPLAIN SELECT * FROM customers_mysql WHERE phone_number = '080000000001';

-- B: numeric literal, full table scan
EXPLAIN SELECT * FROM customers_mysql WHERE phone_number = 80000000001;
SHOW WARNINGS;
```

On MySQL 8.0 the plan for B shows `type = ALL` and `key = NULL` even though `idx_phone` is listed under `possible_keys`, and `SHOW WARNINGS` after the `EXPLAIN` reports warning 1739:

```text
Level    Code  Message
Warning  1739  Cannot use ref access on index 'idx_phone' due to type or collation
               conversion on field 'phone_number'
```

`EXPLAIN FORMAT=TREE` (8.0.16 and later) prints a `Table scan` under a `Filter` node for the same query. Warning 1739 is the quickest way to confirm the diagnosis, because it names the column whose conversion blocked the index.

## What the conversion does to the results

The slowness is the smaller problem. Converting to numbers also changes which rows match. PostgreSQL will do the same conversion if you ask for it explicitly, which lets us see the effect on the three extra rows:

```sql
SELECT id, phone_number
FROM customers
WHERE phone_number::NUMERIC = 8123456789
ORDER BY id;
```

```result
   id   | phone_number  
--------+---------------
 100001 | 8123456789
 100002 | 08123456789
 100003 | 0008123456789
(3 rows)
```

The literal `08123456789` is the number 8123456789, because an integer has no leading zeros. Every string that parses to that number matches, so the query returns three different phone numbers where the string comparison `phone_number = '08123456789'` would return one. In MySQL you get this result without asking for it.

MySQL has a second effect for strings that are not numbers. A string with no leading digits converts to 0, so this comparison is true for every such row:

```sql norun
SELECT * FROM users WHERE username = 0;
-- 'alice' converts to 0, so this returns alice, bob, and every other
-- username that does not start with a digit 1-9 (with a truncation warning per row)
```

Very long digit strings have a third problem: doubles hold about 15 to 17 significant digits, so two different long IDs stored as strings can compare equal to the same numeric literal.

## PostgreSQL has its own version of this problem

PostgreSQL refuses `varchar = integer`, but it will cast a column when the types differ and a cast is the only way to make the comparison work. Two ways to get there.

**You write the cast on the column.** The phone query above, with the cast written out, can no longer use the index:

```sql
EXPLAIN (COSTS OFF)
SELECT * FROM customers WHERE phone_number::NUMERIC = 8123456789;
```

```result
                         QUERY PLAN
-------------------------------------------------------------
 Seq Scan on customers
   Filter: ((phone_number)::numeric = '8123456789'::numeric)
(2 rows)
```

An index on `phone_number` is ordered by the text value. It says nothing about the order of `phone_number::NUMERIC`, so the planner has to evaluate the cast on every row.

**You compare an integer column with a numeric literal.** There is no `integer = numeric` operator, so PostgreSQL converts the column to `numeric`:

```sql
EXPLAIN (COSTS OFF)
SELECT * FROM customers WHERE id = 42.0;
```

```result
            QUERY PLAN
----------------------------------
 Seq Scan on customers
   Filter: ((id)::numeric = 42.0)
(2 rows)
```

`id` has a primary key index, and it is not used. The same happens with a `NUMERIC` parameter from a driver or ORM, or `id = 42::NUMERIC`. The plain integer version and the quoted version use the index:

```sql
EXPLAIN (COSTS OFF) SELECT * FROM customers WHERE id = 42;
EXPLAIN (COSTS OFF) SELECT * FROM customers WHERE id = '42';
```

```result
                  QUERY PLAN
----------------------------------------------
 Index Scan using customers_pkey on customers
   Index Cond: (id = 42)
(2 rows)
```

The quoted `'42'` is an untyped literal, so PostgreSQL resolves it as `integer` to match the column. The rule behind all of this: an untyped literal adapts to the column, but a column of a fixed type is converted when no operator exists for the pair, and then the cast lands on the column.

## The same thing with a function

Any expression around the column, not just a cast, has the same effect. A common case is a date filter:

```sql
CREATE INDEX idx_customers_created ON customers (created_at);
ANALYZE customers;

EXPLAIN (COSTS OFF)
SELECT * FROM customers WHERE created_at::DATE >= '2025-12-31';
```

```result
                      QUERY PLAN
------------------------------------------------------
 Seq Scan on customers
   Filter: ((created_at)::date >= '2025-12-31'::date)
(2 rows)
```

Keep the column bare and compare it with a timestamp instead:

```sql
EXPLAIN (COSTS OFF)
SELECT * FROM customers WHERE created_at >= '2025-12-31';
```

```result
                                       QUERY PLAN
----------------------------------------------------------------------------------------
 Bitmap Heap Scan on customers
   Recheck Cond: (created_at >= '2025-12-31 00:00:00'::timestamp without time zone)
   ->  Bitmap Index Scan on idx_customers_created
         Index Cond: (created_at >= '2025-12-31 00:00:00'::timestamp without time zone)
(4 rows)
```

## Finding it in real systems

- **PostgreSQL:** `EXPLAIN` on the slow query. The giveaway is a `Filter:` line that contains `::` on the column side, such as `(id)::numeric = 42.0`. With `pg_stat_statements` you can sort by total time and look at the heaviest statements for that pattern.
- **MySQL:** `EXPLAIN` showing `type = ALL` with a non-empty `possible_keys`, and warning 1739 in `SHOW WARNINGS`.
- **Application side:** look at the SQL your driver actually sends. ORMs and drivers decide the parameter type from the language type, so a phone number held in an `int` variable or a JSON number becomes a numeric literal.

## The fix

Make the literal or parameter the same type as the column, and leave the column bare:

```sql norun
WHERE phone_number = '08123456789'     -- string column, string value
WHERE id = 42                          -- integer column, integer value
WHERE created_at >= '2025-03-01' AND created_at < '2025-03-02'
```

If the value arrives with the wrong type, cast the value, not the column: `phone_number = (:phone)::TEXT` in PostgreSQL, `phone_number = CAST(? AS CHAR)` in MySQL. Phone numbers, postal codes and account numbers are identifiers, not quantities: store and compare them as strings, which is also the only way to keep their leading zeros.

## In short

- MySQL compares a string column with a number as floating-point numbers, converts every row, cannot use the index, and can match rows you did not intend to match.
- PostgreSQL rejects `varchar = integer`. It still skips the index when the column ends up inside a cast, for example `integer_col = 42.0` or `text_col::numeric = ...`.
- Look for `::` or a function on the column side of the plan's `Filter:` line, or warning 1739 in MySQL.
- Match the parameter's type to the column, and never wrap the column.
