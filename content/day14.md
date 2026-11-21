---
day: 14
title: "Bitmasks: Many Flags in One Integer"
summary: "Store a set of yes/no flags such as user permissions in one integer and read, grant, revoke and toggle them with bitwise operators, including what to watch for in PostgreSQL."
teaser: "Many permission flags in one integer: read, grant, revoke, and the traps in PostgreSQL."
---

A content system gives each user some of six permissions: read, write, delete, publish, manage users, view analytics. The textbook design is a junction table, one row per user per permission. It is correct and flexible, and it has costs: checking one permission is a join or a subquery, and finding users who can both publish and manage users means intersecting rows.

A second design stores all six flags in one integer column, one bit per permission. Unix file modes work this way. A check is a bitwise `AND`, granting is an `OR`, and revoking is an `AND` with the complement. The examples run on PostgreSQL 18, and the later sections cover when this design is the wrong choice.

## Bit positions

Each permission gets a power of two, so each one occupies its own bit. PostgreSQL can print the bits, which is easier than doing it in your head: casting an integer to `bit(6)` keeps the six lowest bits.

```sql
CREATE TABLE permission_flags (
    flag_value  INT PRIMARY KEY,
    flag_name   TEXT NOT NULL
);

INSERT INTO permission_flags VALUES
    (1,  'read'),
    (2,  'write'),
    (4,  'delete'),
    (8,  'publish'),
    (16, 'manage_users'),
    (32, 'view_analytics');

SELECT flag_value, flag_value::bit(6) AS bits, flag_name
FROM permission_flags
ORDER BY flag_value;
```

```result
 flag_value |  bits  |   flag_name
------------+--------+----------------
          1 | 000001 | read
          2 | 000010 | write
          4 | 000100 | delete
          8 | 001000 | publish
         16 | 010000 | manage_users
         32 | 100000 | view_analytics
(6 rows)
```

A user with read, write and publish has 1 + 2 + 8 = 11, binary `001011`. Because every flag is a different power of two, the sum is unambiguous: 11 can only be made from those three flags.

The `users` table has a `CHECK` that rejects any bit outside the six defined flags (`permissions & ~63` must be zero):

```sql
CREATE TABLE users (
    user_id      INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    username     TEXT NOT NULL,
    permissions  INT NOT NULL DEFAULT 0 CHECK (permissions & ~63 = 0)
);

INSERT INTO users (username, permissions) VALUES
    ('alice',   1 + 2 + 4 + 8 + 16 + 32),
    ('bob',     1 + 2),
    ('charlie', 1 + 2 + 8),
    ('diana',   1),
    ('eve',     1 + 2 + 4 + 8),
    ('frank',   0);

SELECT user_id, username, permissions, permissions::bit(6) AS bits
FROM users
ORDER BY user_id;
```

```result
 user_id | username | permissions |  bits
---------+----------+-------------+--------
       1 | alice    |          63 | 111111
       2 | bob      |           3 | 000011
       3 | charlie  |          11 | 001011
       4 | diana    |           1 | 000001
       5 | eve      |          15 | 001111
       6 | frank    |           0 | 000000
(6 rows)
```

Frank is a new account with no permissions, which is worth having in the data because several queries below treat zero in a special way.

## Checking permissions

`permissions & 2` keeps only the write bit. The result is 2 if the user has it and 0 if not:

```sql
SELECT username, permissions
FROM users
WHERE permissions & 2 = 2
ORDER BY username;
```

```result
 username | permissions
----------+-------------
 alice    |          63
 bob      |           3
 charlie  |          11
 eve      |          15
(4 rows)
```

For several permissions at once, build the mask by adding the flags and compare the result with the full mask. Both write (2) and publish (8) means mask 10:

```sql
SELECT username, permissions
FROM users
WHERE permissions & 10 = 10
ORDER BY username;
```

```result
 username | permissions
----------+-------------
 alice    |          63
 charlie  |          11
 eve      |          15
(3 rows)
```

Charlie has both (11 = 8 + 2 + 1), and so do Alice and Eve. For "either write or publish", the test is that the `AND` is not zero:

```sql
SELECT username, permissions
FROM users
WHERE permissions & 10 <> 0
ORDER BY username;
```

```result
 username | permissions
----------+-------------
 alice    |          63
 bob      |           3
 charlie  |          11
 eve      |          15
(4 rows)
```

The difference between `= mask` and `<> 0` is the one thing in this pattern that is easy to get backwards. Bob has write but not publish, so he is in the "either" result and not in the "both" result.

## Granting, revoking, toggling

Granting uses `OR`. Bob gets publish (8):

```sql
UPDATE users SET permissions = permissions | 8 WHERE username = 'bob';

SELECT username, permissions, permissions::bit(6) AS bits FROM users WHERE username = 'bob';
```

```result
 username | permissions |  bits
----------+-------------+--------
 bob      |          11 | 001011
(1 row)
```

Bob goes from 3 (`000011`) to 11 (`001011`). Running it a second time changes nothing, because OR-ing a bit that is already set leaves it set.

Revoking uses `AND` with the complement. `~2` flips every bit of 2, so only the write bit is zero, and the `AND` clears exactly that bit:

```sql
UPDATE users SET permissions = permissions & ~2 WHERE username = 'charlie';

SELECT username, permissions, permissions::bit(6) AS bits FROM users WHERE username = 'charlie';
```

```result
 username | permissions |  bits
----------+-------------+--------
 charlie  |           9 | 001001
(1 row)
```

Charlie goes from 11 to 9: read and publish remain, write is gone. This is also safe to repeat.

Toggling flips a bit whatever its current state. PostgreSQL's bitwise XOR on integers is `#`. Eve loses delete (4):

```sql
UPDATE users SET permissions = permissions # 4 WHERE username = 'eve';

SELECT username, permissions, permissions::bit(6) AS bits FROM users WHERE username = 'eve';
```

```result
 username | permissions |  bits
----------+-------------+--------
 eve      |          11 | 001011
(1 row)
```

Run the same statement again and Eve's 11 becomes 15, with delete back. After that, she stays at 15 for the rest of the article.

```sql
UPDATE users SET permissions = permissions # 4 WHERE username = 'eve';
```

One trap here: in MySQL and SQL Server, `^` is XOR, but in PostgreSQL `^` is exponentiation. The query runs without error and returns something unrelated:

```sql
SELECT 3 # 4 AS xor, 3 ^ 4 AS power;
```

```result
 xor | power
-----+-------
   7 |    81
(1 row)
```

An `UPDATE ... SET permissions = permissions | 8` is also atomic. Two requests granting different permissions to the same user at the same time cannot overwrite each other, because the row is locked while the new value is computed from the current one. Reading the integer into application code, changing it and writing it back loses that.

## Showing permissions as text

For display, a `CASE` per bit with `CONCAT_WS` (which skips `NULL`s) works but repeats the flag list in every query. A join against `permission_flags` keeps the list in one place:

```sql
SELECT u.username,
       STRING_AGG(f.flag_name, ', ' ORDER BY f.flag_value) AS permission_list
FROM users u
JOIN permission_flags f ON u.permissions & f.flag_value = f.flag_value
GROUP BY u.username
ORDER BY u.username;
```

```result
 username |                      permission_list
----------+------------------------------------------------------------
 alice    | read, write, delete, publish, manage_users, view_analytics
 bob      | read, write, publish
 charlie  | read, publish
 diana    | read
 eve      | read, write, delete, publish
(5 rows)
```

Frank is missing. With no flag matching, the join produces no rows for him. To keep users with zero permissions, move the test into a `LEFT JOIN` condition:

```sql
SELECT u.username,
       COALESCE(STRING_AGG(f.flag_name, ', ' ORDER BY f.flag_value), '(none)') AS permission_list
FROM users u
LEFT JOIN permission_flags f ON u.permissions & f.flag_value = f.flag_value
GROUP BY u.username
ORDER BY u.username;
```

```result
 username |                      permission_list
----------+------------------------------------------------------------
 alice    | read, write, delete, publish, manage_users, view_analytics
 bob      | read, write, publish
 charlie  | read, publish
 diana    | read
 eve      | read, write, delete, publish
 frank    | (none)
(6 rows)
```

## Roles as masks

Roles are masks too. A role is the OR of its permissions, and a user's permissions can be built from roles:

```sql
CREATE TABLE roles (
    role_name    TEXT PRIMARY KEY,
    permissions  INT NOT NULL CHECK (permissions & ~63 = 0)
);

INSERT INTO roles VALUES
    ('viewer',    1),
    ('editor',    1 + 2 + 8),
    ('moderator', 1 + 2 + 4 + 8),
    ('admin',     63);
```

Giving Diana the editor role adds the role's bits to hers with `OR`. It does not remove anything she already has, which is different from setting her permissions equal to the role:

```sql
UPDATE users
SET permissions = permissions | (SELECT permissions FROM roles WHERE role_name = 'editor')
WHERE username = 'diana';

SELECT username, permissions FROM users WHERE username = 'diana';
```

```result
 username | permissions
----------+-------------
 diana    |          11
(1 row)
```

Who holds at least editor-level access? The same `& mask = mask` test, with the role's mask coming from the table:

```sql
SELECT u.username
FROM users u
JOIN roles r ON r.role_name = 'editor'
            AND u.permissions & r.permissions = r.permissions
ORDER BY u.username;
```

```result
 username
----------
 alice
 bob
 diana
 eve
(4 rows)
```

Charlie is missing because he lost write earlier, even though he can still publish.

## Counting set bits

PostgreSQL has `bit_count()` for bit strings (since version 14), so cast the integer to `bit(32)` first. The portable alternative adds up one shifted bit per flag, and both give the same numbers:

```sql
SELECT username, permissions,
       bit_count(permissions::bit(32)) AS flags_builtin,
       (permissions >> 0 & 1) + (permissions >> 1 & 1) + (permissions >> 2 & 1)
     + (permissions >> 3 & 1) + (permissions >> 4 & 1) + (permissions >> 5 & 1) AS flags_manual
FROM users
ORDER BY flags_builtin DESC, username;
```

```result
 username | permissions | flags_builtin | flags_manual
----------+-------------+---------------+--------------
 alice    |          63 |             6 |            6
 eve      |          15 |             4 |            4
 bob      |          11 |             3 |            3
 diana    |          11 |             3 |            3
 charlie  |           9 |             2 |            2
 frank    |           0 |             0 |            0
(6 rows)
```

MySQL has `BIT_COUNT()` for integers directly.

## Everyday queries

Who cannot publish (a zero result of the `AND`):

```sql
SELECT username
FROM users
WHERE permissions & 8 = 0
ORDER BY username;
```

```result
 username
----------
 frank
(1 row)
```

How many users share each exact combination:

```sql
SELECT permissions, permissions::bit(6) AS bits, COUNT(*) AS user_count
FROM users
GROUP BY permissions
ORDER BY user_count DESC, permissions;
```

```result
 permissions |  bits  | user_count
-------------+--------+------------
          11 | 001011 |          2
           0 | 000000 |          1
           9 | 001001 |          1
          15 | 001111 |          1
          63 | 111111 |          1
(5 rows)
```

Comparing two users, which flags Alice has that Eve lacks, the reverse, and the shared ones:

```sql
SELECT (a.permissions & ~e.permissions)::bit(6) AS alice_only,
       (e.permissions & ~a.permissions)::bit(6) AS eve_only,
       (a.permissions &  e.permissions)::bit(6) AS shared
FROM users a, users e
WHERE a.username = 'alice' AND e.username = 'eve';
```

```result
 alice_only | eve_only | shared
------------+----------+--------
 110000     | 000000   | 001111
(1 row)
```

## What the CHECK constraint buys

The `CHECK (permissions & ~63 = 0)` stops typos from creating bits that mean nothing. For example, a stray 64 would later make `permissions = 63` comparisons fail for an "admin":

```sql norun
INSERT INTO users (username, permissions) VALUES ('mallory', 64);
```

```text
ERROR:  new row for relation "users" violates check constraint "users_permissions_check"
DETAIL:  Failing row contains (7, mallory, 64).
```

A junction table gets the same protection from a foreign key to a permissions table. A bitmask has no foreign key, so the constraint above is the substitute. Adding a seventh flag means updating the constraint as well.

## Size limits

`INT` is a signed 32-bit integer, so only 31 bits are usable as positive flags. The 32nd bit is the sign:

```sql
SELECT 1 << 30 AS bit_30, 1 << 31 AS bit_31_as_int, 1::bigint << 31 AS bit_31_as_bigint;
```

```result
   bit_30   | bit_31_as_int | bit_31_as_bigint
------------+---------------+------------------
 1073741824 |   -2147483648 |       2147483648
(1 row)
```

With more than 31 flags use `BIGINT` (63 usable bits), and past that use PostgreSQL's `bit varying` type or a different design.

## Indexes

A B-tree index on the permissions column cannot help `permissions & 32 = 32`, because the index is ordered by the whole number and the condition looks inside it. With 200,000 users, about 1 in 200 of whom has view_analytics, the plan is a full scan despite an index on `permissions`:

```sql
CREATE TABLE big_users AS
SELECT g AS user_id,
       1 + (g % 2) * 2 + CASE WHEN g % 200 = 0 THEN 32 ELSE 0 END AS permissions
FROM generate_series(1, 200000) AS g;

ALTER TABLE big_users ADD PRIMARY KEY (user_id);
CREATE INDEX ON big_users (permissions);
ANALYZE big_users;

EXPLAIN (COSTS OFF)
SELECT user_id FROM big_users WHERE permissions & 32 = 32;
```

```result
             QUERY PLAN
-------------------------------------
 Seq Scan on big_users
   Filter: ((permissions & 32) = 32)
(2 rows)
```

If one specific flag is queried often, a partial index on exactly that condition does work, because the planner can match the query's condition to the index's predicate:

```sql
CREATE INDEX big_users_analytics ON big_users (user_id) WHERE permissions & 32 = 32;
ANALYZE big_users;

EXPLAIN (COSTS OFF)
SELECT user_id FROM big_users WHERE permissions & 32 = 32;
```

```result
                       QUERY PLAN
--------------------------------------------------------
 Index Only Scan using big_users_analytics on big_users
(1 row)
```

This is one index per flag you need to search on, so the junction table's single index on `(permission, user_id)` scales better when "who has permission X" is the main question.

## Operators across databases

| Operation | PostgreSQL | MySQL | SQL Server |
| --- | --- | --- | --- |
| AND | `&` | `&` | `&` |
| NOT | `~` | `~` | `~` |
| XOR | `#` | `^` | `^` |
| Shift | `<<` `>>` | `<<` `>>` | `LEFT_SHIFT`, `RIGHT_SHIFT` (2022 and later) |
| Bit count | `bit_count` on bit strings | `BIT_COUNT` | `BIT_COUNT` (2022 and later) |

OR is `|` in all three. BigQuery also has `&`, `|`, `^`, `~`, `<<` and `>>` as operators on integers. Its `BIT_AND` and `BIT_OR` are aggregate functions over a column, which are something else. Parenthesising `(a & b) = c` costs nothing and makes the intent clear to readers.

## When not to use a bitmask

A bitmask fits when the set of flags is small, fixed and checked together, and you are happy to change code to add a flag. It is a poor fit when:

- The flags change often or are defined by users, since each change touches code and constraints.
- You need to know who granted what and when. A junction table can carry `granted_by` and `granted_at` columns; one integer cannot.
- You need referential integrity or human-readable queries in reports, where the junction table is plainly easier.
- You need more than 63 flags.

## In short

- One bit per flag: check with `permissions & mask = mask`, grant with `|`, revoke with `& ~mask`, toggle with `#` in PostgreSQL.
- `= mask` means all of the flags, `<> 0` means any of them.
- `^` is XOR in MySQL and SQL Server but exponentiation in PostgreSQL.
- Add a `CHECK` that rejects undefined bits, use `BIGINT` past 31 flags, and expect a full scan unless you add a partial index for a specific flag.
