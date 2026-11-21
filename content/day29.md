---
day: 29
title: "Second-Order SQL Injection: When the Payload Comes From Your Own Database"
summary: "A value stored safely through a parameterized INSERT can still inject SQL months later, when another job concatenates it into a new query. A runnable demonstration in PL/pgSQL, and the fixes: parameters, format() with %L and %I, and allowlists."
teaser: "A safely stored value injects SQL months later in dynamic SQL. Demonstration and fixes."
---

A support ticket arrives: the admin tool throws a syntax error whenever someone reviews the user "Pat O'Brien". The signup form has used parameterized queries from day one, the code review checklist has a line about string concatenation, and the linter is green. The error comes from a different place. The admin tool reads the display name back out of the database and pastes it into the SQL text of an audit-log `INSERT`.

An apostrophe in a name is the harmless version of this bug. Someone who wants to do damage picks a different name, and the stored value then runs as SQL the next time something concatenates it. That is second-order (or stored) SQL injection: the payload goes in through a safe door, sits in a table, and fires later in a query written by someone who assumed data from our own database is clean.

The demonstrations below run on PostgreSQL 18 and use PL/pgSQL, so everything can be executed here. The mechanism is the same in application code: a Python f-string does exactly what `||` does in the functions below. The rule that fixes it, "never build SQL text out of values", holds for every database and driver.

## Storing the payload is safe

These tables are used throughout. `activities` has a `is_hidden` flag for rows an admin should not see in the normal view:

```sql
CREATE TABLE users (
    user_id       INT PRIMARY KEY,
    email         TEXT NOT NULL UNIQUE,
    display_name  TEXT NOT NULL
);

CREATE TABLE audit_log (
    log_id  SERIAL PRIMARY KEY,
    event   TEXT NOT NULL
);

CREATE TABLE activities (
    activity_id  SERIAL PRIMARY KEY,
    username     TEXT NOT NULL,
    action       TEXT NOT NULL,
    is_hidden    BOOLEAN NOT NULL
);

INSERT INTO activities (username, action, is_hidden) VALUES
    ('Ann', 'logged in',           false),
    ('Ann', 'exported salary data', true);
```

The signup path uses a prepared statement, so the values travel separately from the SQL text. Four people sign up. Three of the names are hostile:

```sql
PREPARE signup(INT, TEXT, TEXT) AS
    INSERT INTO users (user_id, email, display_name) VALUES ($1, $2, $3);

EXECUTE signup(1, 'ann@example.com', 'Ann');
EXECUTE signup(2, 'pat@example.com', 'Pat O''Brien');
EXECUTE signup(3, 'mal@example.com', 'x''), (''forged: refund approved by admin');
EXECUTE signup(4, 'tom@example.com', 'Tom''); UPDATE users SET display_name = ''pwned'' WHERE user_id = 1; --');
EXECUTE signup(5, 'eve@example.com', 'Ann'' --');

SELECT user_id, display_name FROM users ORDER BY user_id;
```

```result
 user_id |                             display_name
---------+----------------------------------------------------------------------
       1 | Ann
       2 | Pat O'Brien
       3 | x'), ('forged: refund approved by admin
       4 | Tom'); UPDATE users SET display_name = 'pwned' WHERE user_id = 1; --
       5 | Ann' --
(5 rows)
```

Every name is stored exactly as typed, quotes and semicolons included. This is the correct behaviour: the signup code did nothing wrong, and escaping the value before storing it would be a bug of its own (the user would see doubled quotes everywhere). Safety is decided when a query is built, not when a value is saved.

## The vulnerable tool

The admin tool records a moderation event. It looks up the name and builds the `INSERT` as a string:

```sql
CREATE PROCEDURE log_review_unsafe(p_user_id INT)
LANGUAGE plpgsql AS $$
DECLARE
    v_name TEXT;
BEGIN
    SELECT display_name INTO v_name FROM users WHERE user_id = p_user_id;
    EXECUTE 'INSERT INTO audit_log (event) VALUES (''Admin reviewed user: ' || v_name || ''')';
END
$$;
```

The Python equivalent is `cursor.execute(f"INSERT INTO audit_log (event) VALUES ('Admin reviewed user: {name}')")`. It works for Ann:

```sql
CALL log_review_unsafe(1);

SELECT log_id, event FROM audit_log ORDER BY log_id;
```

```result
 log_id |          event
--------+--------------------------
      1 | Admin reviewed user: Ann
(1 row)
```

### The apostrophe

For Pat the generated statement is `VALUES ('Admin reviewed user: Pat O'Brien')`, which is not valid SQL:

```sql norun
CALL log_review_unsafe(2);
```

```text
ERROR:  syntax error at or near "Brien"
LINE 1: ...udit_log (event) VALUES ('Admin reviewed user: Pat O'Brien')
                                                                ^
QUERY:  INSERT INTO audit_log (event) VALUES ('Admin reviewed user: Pat O'Brien')
CONTEXT:  PL/pgSQL function log_review_unsafe(integer) line 6 at EXECUTE
```

This is how most second-order injections get noticed: as a bug report about a user with a quote in their name. If the failure is the only symptom you ever see, you are looking at the harmless case of a hole that is open.

### Forging data

Mallory's name closes the string and the row, then starts another row:

```sql
CALL log_review_unsafe(3);

SELECT log_id, event FROM audit_log ORDER BY log_id;
```

```result
 log_id |              event
--------+----------------------------------
      1 | Admin reviewed user: Ann
      2 | Admin reviewed user: x
      3 | forged: refund approved by admin
(3 rows)
```

Reviewing Mallory wrote two rows, and the second one is text of Mallory's choosing in the audit log. In a table that other systems trust, such as a payments queue or a permissions table, that is real damage.

### Running other statements

Tom's name ends the `INSERT` and appends an `UPDATE`:

```sql
CALL log_review_unsafe(4);

SELECT user_id, display_name FROM users ORDER BY user_id;
```

```result
 user_id |                             display_name
---------+----------------------------------------------------------------------
       1 | pwned
       2 | Pat O'Brien
       3 | x'), ('forged: refund approved by admin
       4 | Tom'); UPDATE users SET display_name = 'pwned' WHERE user_id = 1; --
       5 | Ann' --
(5 rows)
```

Ann's name is now `pwned`. The same shape drops a table, as in the classic `'); DROP TABLE audit_log; --`. Two things limit this variant. A driver that sends the statement with bound parameters through PostgreSQL's extended protocol rejects a text containing several commands, so stacked statements depend on the driver and call path; here `EXECUTE` of a plain string allows them. And the application role's privileges bound the damage, which is a reason to give the admin tool a role that cannot drop or update arbitrary tables. Neither limit helps against the forged-row case above, which uses only the `INSERT` the tool was already allowed to run.

Put Ann's name back before going on:

```sql
UPDATE users SET display_name = 'Ann' WHERE user_id = 1;
```

### Bypassing a filter

A comment marker turns off whatever follows the concatenated value. A second tool lists a user's visible activity, looking the user up by ID and then querying `activities` by name:

```sql
CREATE FUNCTION activity_for_user_unsafe(p_user_id INT)
RETURNS SETOF activities
LANGUAGE plpgsql AS $$
DECLARE
    v_name TEXT;
BEGIN
    SELECT display_name INTO v_name FROM users WHERE user_id = p_user_id;
    RETURN QUERY EXECUTE
        'SELECT * FROM activities WHERE username = ''' || v_name || ''' AND NOT is_hidden ORDER BY activity_id';
END
$$;

SELECT * FROM activity_for_user_unsafe(1);
```

```result
 activity_id | username |  action   | is_hidden
-------------+----------+-----------+-----------
           1 | Ann      | logged in | f
(1 row)
```

User 5 registered as `Ann' --`. The query text becomes `... WHERE username = 'Ann' --' AND NOT is_hidden`, so the `--` removes the visibility filter, and the tool shows the hidden row for a different person, Ann:

```sql
SELECT * FROM activity_for_user_unsafe(5);
```

```result
 activity_id | username |        action        | is_hidden
-------------+----------+----------------------+-----------
           1 | Ann      | logged in            | f
           2 | Ann      | exported salary data | t
(2 rows)
```

## Why it passes review

Each piece is correct on its own. The signup `INSERT` is parameterized and the `SELECT` that reads the name is parameterized. The unsafe line is a third query, often in another file or service, and its input comes from "our own database". A scanner that flags string building near an `execute` call can catch it, but only when the concatenation is in the same function as the call. Tests rarely contain a user named `x'), ('forged...`, and the trigger can come months after the payload was stored, which makes the cause hard to find.

The mental error is treating safety as a property of the data: "user input is dangerous, database content is clean". Safety is a property of how the query is built. Any value that ends up inside SQL text through string building is an injection point, whatever table it came from.

## The fix: do not build SQL from values

In PL/pgSQL, plain SQL statements already use their variables as parameters. The rewrite of the first tool needs no dynamic SQL at all:

```sql
CREATE PROCEDURE log_review_safe(p_user_id INT)
LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO audit_log (event)
    SELECT 'Admin reviewed user: ' || display_name
    FROM users
    WHERE user_id = p_user_id;
END
$$;

TRUNCATE audit_log RESTART IDENTITY;

CALL log_review_safe(2);
CALL log_review_safe(3);
CALL log_review_safe(4);

SELECT log_id, event FROM audit_log ORDER BY log_id;
```

```result
 log_id |                                           event
--------+-------------------------------------------------------------------------------------------
      1 | Admin reviewed user: Pat O'Brien
      2 | Admin reviewed user: x'), ('forged: refund approved by admin
      3 | Admin reviewed user: Tom'); UPDATE users SET display_name = 'pwned' WHERE user_id = 1; --
(3 rows)
```

The concatenation `'Admin reviewed user: ' || display_name` still happens, but it builds a value, not SQL text, so the apostrophes are just characters. Pat's name works, and the two hostile names are stored in the log as the text they are.

When a statement really has to be dynamic, pass values with `USING`. They arrive as `$1`, `$2` and are never parsed as SQL:

```sql
CREATE FUNCTION activity_for_user_safe(p_user_id INT)
RETURNS SETOF activities
LANGUAGE plpgsql AS $$
DECLARE
    v_name TEXT;
BEGIN
    SELECT display_name INTO v_name FROM users WHERE user_id = p_user_id;
    RETURN QUERY EXECUTE
        'SELECT * FROM activities WHERE username = $1 AND NOT is_hidden ORDER BY activity_id'
        USING v_name;
END
$$;

SELECT * FROM activity_for_user_safe(5);
```

```result
 activity_id | username | action | is_hidden
-------------+----------+--------+-----------
(0 rows)
```

Eve's name `Ann' --` is now looked up as a literal and matches nobody. The same call for user 1 returns Ann's visible row. In application code this is `cursor.execute("... WHERE username = %s AND NOT is_hidden", (name,))`, with the value in the second argument and never formatted into the string.

### If you must build the string

Sometimes a value has to go into SQL text, for example in a `DO` block or a script generator. `format()` has two placeholders for that: `%L` quotes a literal and `%I` quotes an identifier. With the hostile name from user 3:

```sql
SELECT format('INSERT INTO audit_log (event) VALUES (%L)', 'Admin reviewed user: ' || display_name) AS statement
FROM users
WHERE user_id = 3;
```

```result
                                                statement
---------------------------------------------------------------------------------------------------------
 INSERT INTO audit_log (event) VALUES ('Admin reviewed user: x''), (''forged: refund approved by admin')
(1 row)
```

The embedded quotes are doubled, so the whole name stays inside one string literal. `%L` also handles two things hand-written escaping gets wrong: a `NULL` becomes the keyword `NULL`, and a backslash makes it emit an `E''` string so the result means the same thing under any `standard_conforming_strings` setting:

```sql
SELECT format('%L', NULL::text) AS null_value, format('%L', 'a\b') AS backslash;
```

```result
 null_value | backslash
------------+-----------
 NULL       | E'a\\b'
(1 row)
```

Prefer `USING` whenever the position accepts a parameter, and use `%L` only for the cases that do not.

## Identifiers cannot be parameters

Parameters stand in for values. Table names, column names and `ASC`/`DESC` cannot be parameters, so a dynamic `ORDER BY` always means building text. A saved sort preference that was stored as `name; DROP TABLE products; --` is the second-order version of this. Quote it with `%I`, which turns the whole string into a single identifier:

```sql
SELECT format('SELECT * FROM products ORDER BY %I', 'name; DROP TABLE products; --') AS statement;
```

```result
                            statement
-----------------------------------------------------------------
 SELECT * FROM products ORDER BY "name; DROP TABLE products; --"
(1 row)
```

That statement is harmless: it fails because no column has that name. Quoting alone still lets a caller sort by any column, including ones that should not be exposed (ordering by a column leaks information about its values even when the column is not shown), so pair `%I` with an allowlist. Check the identifier against the list you meant to support, and map the direction instead of passing it through:

```sql
CREATE TABLE products (
    product_id  INT PRIMARY KEY,
    name        TEXT NOT NULL,
    price       NUMERIC(8,2) NOT NULL,
    cost        NUMERIC(8,2) NOT NULL  -- internal, not for customers
);

INSERT INTO products VALUES
    (1, 'Desk',  250.00, 120.00),
    (2, 'Chair', 120.00,  90.00),
    (3, 'Lamp',   40.00,  10.00);

CREATE FUNCTION list_products(p_sort TEXT, p_desc BOOLEAN DEFAULT false)
RETURNS TABLE (product_id INT, name TEXT, price NUMERIC)
LANGUAGE plpgsql AS $$
BEGIN
    IF p_sort NOT IN ('name', 'price') THEN
        RAISE EXCEPTION 'cannot sort by %', p_sort USING ERRCODE = '22023';
    END IF;
    RETURN QUERY EXECUTE format(
        'SELECT p.product_id, p.name, p.price FROM products p ORDER BY %I %s',
        p_sort,
        CASE WHEN p_desc THEN 'DESC' ELSE 'ASC' END);
END
$$;

SELECT * FROM list_products('price', true);
```

```result
 product_id | name  | price
------------+-------+--------
          1 | Desk  | 250.00
          2 | Chair | 120.00
          3 | Lamp  |  40.00
(3 rows)
```

`list_products('cost')` and the DROP string are both refused with `cannot sort by ...` before any SQL is built. In psycopg the same two pieces are `psycopg.sql.Identifier(col)` (or `psycopg2.sql.Identifier`) for the quoting and a Python set for the allowlist:

```python norun
from psycopg import sql

ALLOWED_SORT = {"name", "price"}
if sort_column not in ALLOWED_SORT:
    raise ValueError(f"cannot sort by {sort_column}")

query = sql.SQL("SELECT * FROM products ORDER BY {col}").format(col=sql.Identifier(sort_column))
cursor.execute(query)
```

Saved filters are a case with no clean fix. If users store a `WHERE` clause as text and the application runs it, the stored text is the payload by design. Store a structured filter (column, operator, value) and build the query from an allowlist of columns and operators, with the value as a parameter.

## Why validating input is the wrong layer

Rejecting quotes at signup fails on its own terms: "O'Brien" is a real name, and a filter that blocks it breaks real users while a determined attacker moves to some other payload. It also does nothing about data that already exists, or that enters through an import, an API or a replicated feed, none of which pass through the signup form. Validation is worth having for business rules (length, allowed characters in a username), as a second layer. Structure is protected by keeping values out of SQL text.

## Finding it in an existing system

Search the code for SQL built from strings (f-strings, `+`, `.format`, `%` formatting, template literals) near `execute`, and read each hit with the question "where does this value come from?" instead of "is this user input?". Dynamic `ORDER BY`, `GROUP BY` and table names deserve a separate pass.

Inside the database, functions that build SQL text are easy to list. This query is crude and will miss `format('%s', ...)`, but it finds the pattern used above:

```sql
SELECT proname
FROM pg_proc
WHERE pronamespace = 'public'::regnamespace
  AND prosrc ILIKE '%execute%'
  AND prosrc LIKE '%||%'
ORDER BY proname;
```

```result
         proname
--------------------------
 activity_for_user_unsafe
 log_review_unsafe
(2 rows)
```

At runtime, a burst of syntax errors from an internal job on a table of user-supplied names is the signal to look for. PostgreSQL logs the failing statement text (`log_min_error_statement` defaults to `error`), which shows exactly what was concatenated. It is the same bug as the "Pat O'Brien" ticket, caught earlier.

## In short

- Storing a value safely does not make it safe to use. Every query that reads it and puts it into SQL text is a new injection point.
- Keep values out of SQL text: static SQL, `EXECUTE ... USING`, or driver parameters. Use `format('%L')` only where a parameter is not possible.
- Identifiers cannot be parameters. Quote them with `%I` (or the driver's identifier type) and check them against an allowlist.
- Do not escape on the way in. Escaping belongs to the query builder, at the moment of use.
- A syntax error caused by an apostrophe in a stored value is a vulnerability report, not just a bug.
