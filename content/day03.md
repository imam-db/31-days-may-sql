---
day: 3
title: "'a' = 'A': Is That TRUE or FALSE?"
summary: "Whether two strings that differ only in case or accents are equal depends on the collation. How PostgreSQL and MySQL defaults differ, where it shows up in logins, UNIQUE constraints and sorting, and how to choose the behaviour explicitly."
teaser: "Case and accent rules come from the collation: logins, UNIQUE constraints and sort order."
---

Is the letter `'a'` equal to `'A'`? In PostgreSQL:

```sql
SELECT 'a' = 'A' AS is_equal;
```

```result
 is_equal
----------
 f
(1 row)
```

The same query on MySQL with default settings returns `1`, meaning true, and on a default SQL Server install it also returns true. Neither side reports an error. The difference comes from the collation: the set of rules a database uses to compare and sort strings.

## Defaults differ

| Database | Default collation | `'a' = 'A'` |
| --- | --- | --- |
| PostgreSQL | taken from the locale the cluster was created with, for example `C` or `en_US.UTF-8` | false |
| MySQL 8.0 | `utf8mb4_0900_ai_ci` | true |
| SQL Server | depends on the install language; `SQL_Latin1_General_CP1_CI_AS` is common | true |
| SQLite | `BINARY` | false |

In MySQL's name, `ci` means case-insensitive and `ai` means accent-insensitive, so `'é' = 'e'` is true there as well. PostgreSQL's default collations are all deterministic: two strings are equal only if they are byte for byte the same, so case and accents always matter. That is the behaviour of the PostgreSQL examples below, and they would give the same results on any PostgreSQL server.

Nobody picks these defaults deliberately. An application written against a local MySQL and later deployed on PostgreSQL, or the other way round, changes behaviour without a single line of SQL changing.

## Where it shows up

### Searching and logging in

```sql
CREATE TABLE users (
    id        INT PRIMARY KEY,
    username  TEXT
);

INSERT INTO users VALUES (1, 'admin');

SELECT * FROM users WHERE username = 'Admin';
```

```result
 id | username
----+----------
(0 rows)
```

A user who types `Admin` finds nothing in PostgreSQL. On MySQL with its default collation the same query returns the `admin` row. If the app was developed against one and deployed on the other, login breaks without an error.

### UNIQUE constraints

A unique constraint uses the same equality, so `SQL` and `sql` are different values in PostgreSQL:

```sql
CREATE TABLE tags (tag_name TEXT UNIQUE);

INSERT INTO tags VALUES ('SQL');
INSERT INTO tags VALUES ('sql');

SELECT tag_name FROM tags ORDER BY tag_name;
```

```result
 tag_name
----------
 SQL
 sql
(2 rows)
```

Both inserts succeed. On MySQL with a `_ci` collation the second insert fails with a duplicate key error. A schema that relied on that to keep tags unique has no protection in PostgreSQL.

### GROUP BY and DISTINCT

Grouping uses equality too, so `Electronics` and `electronics` become two groups:

```sql
CREATE TABLE items (name TEXT, category TEXT);

INSERT INTO items VALUES
    ('Keyboard',  'Electronics'),
    ('Mouse',     'electronics'),
    ('Monitor',   'Electronics');

SELECT category, COUNT(*) AS items
FROM items
GROUP BY category
ORDER BY category;
```

```result
  category   | items
-------------+-------
 Electronics |     2
 electronics |     1
(2 rows)
```

On MySQL with a case-insensitive collation this is one group of three.

### ORDER BY

The collation also decides sort order. The same words sorted with two collations, chosen explicitly so the result does not depend on the server's locale. The `"C"` collation sorts by byte value, so every uppercase letter comes before every lowercase one:

```sql
SELECT name
FROM (VALUES ('banana'), ('Apple'), ('cherry'), ('apple'), ('Banana')) AS t(name)
ORDER BY name COLLATE "C";
```

```result
  name
--------
 Apple
 Banana
 apple
 banana
 cherry
(5 rows)
```

An ICU dictionary collation puts the lowercase form of a letter before the uppercase one and keeps `apple` and `Apple` next to each other:

```sql
SELECT name
FROM (VALUES ('banana'), ('Apple'), ('cherry'), ('apple'), ('Banana')) AS t(name)
ORDER BY name COLLATE "en-US-x-icu";
```

```result
  name
--------
 apple
 Apple
 banana
 Banana
 cherry
(5 rows)
```

Keyset or page-based pagination over a column sorted this way can return different pages on servers whose default collations differ, and a sort that is correct under one collation may violate an assumption in code (for example, a binary search over the sorted list in the application).

Collations from the operating system's libc, such as `en_US.UTF-8`, can also change between OS versions. PostgreSQL's documentation warns that when this happens, existing indexes on text columns can become inconsistent with the new sort order and need to be rebuilt. ICU collations such as `en-US-x-icu` are versioned by PostgreSQL, which records the version and warns when it differs.

## Checking what you have

The database's default is chosen at creation time. In PostgreSQL you can ask which collation a column or expression uses:

```sql
SELECT pg_collation_for(username) AS collation FROM users LIMIT 1;
```

```result
 collation
-----------
 "default"
(1 row)
```

`"default"` means the database's default collation. To see what that is, query `pg_database`:

```sql norun
SELECT datname, datlocprovider, datcollate
FROM pg_database
WHERE datname = current_database();
```

On MySQL the equivalents are `SELECT @@collation_database;` for the database, and `SHOW FULL COLUMNS FROM users;` for the column-level collation.

## Choosing case-insensitive comparison in PostgreSQL

### lower() and a matching index

The simplest option is to compare lowercased values, and to put a unique index on the same expression if the column must be unique ignoring case:

```sql
SELECT * FROM users WHERE lower(username) = lower('Admin');
```

```result
 id | username
----+----------
  1 | admin
(1 row)
```

The index must be created before duplicates exist. On a fresh table it rejects the second spelling:

```sql
CREATE TABLE labels (label TEXT);
CREATE UNIQUE INDEX labels_lower_key ON labels (lower(label));

INSERT INTO labels VALUES ('Python');
```

```sql norun
INSERT INTO labels VALUES ('python');
```

```text
ERROR:  duplicate key value violates unique constraint "labels_lower_key"
DETAIL:  Key (lower(label))=(python) already exists.
```

Building the same index on `tags` would fail, because `SQL` and `sql` are already both there.

The queries must use `lower(username)` exactly as written in the index. A plain index on `username` cannot serve `WHERE lower(username) = ...`.

### ILIKE

`ILIKE` is PostgreSQL's case-insensitive `LIKE`:

```sql
SELECT * FROM users WHERE username ILIKE 'admin';
```

```result
 id | username
----+----------
  1 | admin
(1 row)
```

It is fine for searching, but it is a pattern match: `%` and `_` in the input are wildcards, so user input would need escaping, and it does not give you a uniqueness guarantee.

### citext

The `citext` extension adds a text type that compares case-insensitively, including in `UNIQUE` constraints and `GROUP BY`:

```sql
CREATE EXTENSION IF NOT EXISTS citext;

CREATE TABLE accounts (username CITEXT UNIQUE);

INSERT INTO accounts VALUES ('Admin');

SELECT username FROM accounts WHERE username = 'ADMIN';
```

```result
 username
----------
 Admin
(1 row)
```

A second insert of `admin` fails with a duplicate key error. The stored value keeps its original case. `citext` folds case the way `lower()` does for the database's locale, and does nothing about accents.

### A nondeterministic ICU collation

Since PostgreSQL 12 you can define a collation that does not require strings to be byte-identical. Here is one that ignores case and one that also ignores accents:

```sql
CREATE COLLATION case_insensitive (provider = icu, locale = 'und-u-ks-level2', deterministic = false);
CREATE COLLATION ignore_accents   (provider = icu, locale = 'und-u-ks-level1', deterministic = false);

SELECT 'a' = 'A'    COLLATE case_insensitive AS case_ci,
       'café' = 'cafe' COLLATE case_insensitive AS accent_ci,
       'café' = 'cafe' COLLATE ignore_accents   AS accents_ignored;
```

```result
 case_ci | accent_ci | accents_ignored
---------+-----------+-----------------
 t       | f         | t
(1 row)
```

Used on a column, this fixes search, `UNIQUE` and `GROUP BY` without changing any query:

```sql
CREATE TABLE members (username TEXT COLLATE case_insensitive UNIQUE);

INSERT INTO members VALUES ('admin');

SELECT username FROM members WHERE username = 'ADMIN';
```

```result
 username
----------
 admin
(1 row)
```

Grouping treats different spellings as one value too. This is the `items` example again, with the column declared with the new collation:

```sql
CREATE TABLE items_ci (name TEXT, category TEXT COLLATE case_insensitive);

INSERT INTO items_ci VALUES
    ('Keyboard', 'Electronics'),
    ('Mouse',    'electronics'),
    ('Monitor',  'Electronics');

SELECT COUNT(*) AS groups
FROM (SELECT category FROM items_ci GROUP BY category) AS g;
```

```result
 groups
--------
      1
(1 row)
```

Inserting `ADMIN` into `members` would now fail with a duplicate key error. In PostgreSQL 18, `LIKE` works against such a column:

```sql
SELECT username FROM members WHERE username LIKE 'AD%';
```

```result
 username
----------
 admin
(1 row)
```

Nondeterministic collations have limits. `ILIKE` raises `ERROR: nondeterministic collations are not supported for ILIKE`, and before PostgreSQL 18 `LIKE` raised a similar error. Comparisons can be slower than with a deterministic collation, because the database cannot just compare bytes.

## Choosing case-sensitive comparison in MySQL

The other direction, for a MySQL column that should be case-sensitive:

```sql norun
-- compare as bytes for one query
SELECT * FROM users WHERE BINARY username = 'Admin';

-- or use a case-sensitive collation on the column
CREATE TABLE users_cs (
    username VARCHAR(255) COLLATE utf8mb4_bin
);
```

## Accents

`'café' = 'cafe'` is false in PostgreSQL with any default collation, and true in MySQL under `utf8mb4_0900_ai_ci`. A search for `cafe` finds `café` on one and not the other:

```sql
SELECT 'café' = 'cafe' AS postgres_default;
```

```result
 postgres_default
------------------
 f
(1 row)
```

For accent-insensitive search in PostgreSQL, use the `ignore_accents` collation above, or the `unaccent` extension, which strips accents from both sides of a comparison.

## In short

- Equality, `UNIQUE`, `GROUP BY`, `DISTINCT` and `ORDER BY` all follow the collation. PostgreSQL's defaults are case- and accent-sensitive. MySQL 8.0's and SQL Server's usual defaults are case-insensitive.
- Decide per column. In PostgreSQL use `lower()` with a unique index, `citext`, or a nondeterministic ICU collation. In MySQL pick a `_cs` or `_bin` collation where case matters.
- Moving an application between databases, or between machines with different locales, can change what its queries return without a single change in SQL.
