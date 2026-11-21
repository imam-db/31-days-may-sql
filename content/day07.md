---
day: 7
title: "GROUP BY 1, 2: The Ordinal Trap"
summary: "GROUP BY and ORDER BY accept column positions instead of names. That saves typing and breaks when someone edits the SELECT list; where it fails loudly, where it fails silently, and what to write instead."
teaser: "Column positions in GROUP BY and ORDER BY break, sometimes silently, when the SELECT list changes."
---

This query is valid in PostgreSQL, MySQL, SQLite and BigQuery:

```sql norun
SELECT department, job_title, COUNT(*) AS headcount
FROM employees
GROUP BY 1, 2;
```

`GROUP BY 1, 2` means "group by the first and second expressions of the `SELECT` list". It is shorter than repeating the names, and it ties the `GROUP BY` to the order of the `SELECT` list. The examples run on PostgreSQL 18.

## The data

```sql
CREATE TABLE employees (
    id          INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name        TEXT,
    department  TEXT,
    job_title   TEXT,
    salary      NUMERIC,
    hired_at    DATE
);

INSERT INTO employees (name, department, job_title, salary, hired_at) VALUES
    ('Alice', 'Engineering', 'Backend Developer',  95000,  '2021-03-15'),
    ('Bob',   'Engineering', 'Backend Developer',  92000,  '2022-01-10'),
    ('Carol', 'Engineering', 'Frontend Developer', 88000,  '2021-07-20'),
    ('Dave',  'Sales',       'Account Executive',  75000,  '2020-11-01'),
    ('Eve',   'Sales',       'Account Executive',  78000,  '2022-06-15'),
    ('Frank', 'Sales',       'Sales Manager',      110000, '2019-04-01'),
    ('Grace', 'Marketing',   'Content Writer',     65000,  '2023-01-10'),
    ('Hank',  'Marketing',   'Content Writer',     63000,  '2023-05-20'),
    ('Ivy',   'Marketing',   'Marketing Manager',  105000, '2020-08-15');
```

The headcount report:

```sql
SELECT department, job_title, COUNT(*) AS headcount
FROM employees
GROUP BY 1, 2
ORDER BY 1, 2;
```

```result
 department  |     job_title      | headcount
-------------+--------------------+-----------
 Engineering | Backend Developer  |         2
 Engineering | Frontend Developer |         1
 Marketing   | Content Writer     |         2
 Marketing   | Marketing Manager  |         1
 Sales       | Account Executive  |         2
 Sales       | Sales Manager      |         1
(6 rows)
```

## Editing the SELECT list breaks it

A manager asks for the employee's name in the report, and you add `name` as the first column:

```sql norun
SELECT name, department, job_title, COUNT(*) AS headcount
FROM employees
GROUP BY 1, 2;
```

`GROUP BY 1, 2` now means `name, department`, and `job_title` is neither grouped nor aggregated. PostgreSQL stops with:

```text
ERROR:  column "employees.job_title" must appear in the GROUP BY clause or be used in an aggregate function
LINE 1: SELECT name, department, job_title, COUNT(*) AS headcount
                                 ^
```

The error points at `job_title`, which is not the column you just added, so it takes a moment to see that the real cause is the `1, 2` at the bottom of the query. In a 50-line monthly report the `GROUP BY` can be far from the `SELECT` you are editing.

Written with names, the same edit gives an error that points at the new column:

```sql norun
SELECT name, department, job_title, COUNT(*) AS headcount
FROM employees
GROUP BY department, job_title;
```

```text
ERROR:  column "employees.name" must appear in the GROUP BY clause or be used in an aggregate function
LINE 1: SELECT name, department, job_title, COUNT(*) AS headcount
               ^
```

That is the error you want: it names the column you added and what to decide about it (group by it, or aggregate it).

## Engines that do not complain

PostgreSQL rejects every non-aggregated column that is not grouped. Not every engine does. MySQL does the same since 5.7.5 because `ONLY_FULL_GROUP_BY` is on by default, but it can be switched off, and then MySQL picks a value for the ungrouped column from an arbitrary row of each group. SQLite always allows it. In such an engine the broken query above runs, groups by `name` and `department`, and prints some `job_title` per group. The report looks plausible and is wrong.

## ORDER BY is silent everywhere

Grouping errors are loud in PostgreSQL because every selected column has to be grouped. Sorting has no such check, so a shifted `ORDER BY` position changes the result and nothing complains.

The original report sorted by salary, the second column:

```sql
SELECT name, salary
FROM employees
ORDER BY 2 DESC;
```

```result
 name  | salary
-------+--------
 Frank | 110000
 Ivy   | 105000
 Alice |  95000
 Bob   |  92000
 Carol |  88000
 Eve   |  78000
 Dave  |  75000
 Grace |  65000
 Hank  |  63000
(9 rows)
```

Someone adds `department` between the two columns. Position 2 is now `department`:

```sql
SELECT name, department, salary
FROM employees
ORDER BY 2 DESC, 1;
```

```result
 name  | department  | salary
-------+-------------+--------
 Dave  | Sales       |  75000
 Eve   | Sales       |  78000
 Frank | Sales       | 110000
 Grace | Marketing   |  65000
 Hank  | Marketing   |  63000
 Ivy   | Marketing   | 105000
 Alice | Engineering |  95000
 Bob   | Engineering |  92000
 Carol | Engineering |  88000
(9 rows)
```

It is a valid query that returns every row, now sorted by department name descending. If this feeds a "top earners" list, the list is wrong and no one gets an error. Ordinals also fail in a quieter way when the position no longer exists: with only `department` and `COUNT(*)` selected, `ORDER BY 3` gives `ORDER BY position 3 is not in select list`.

## Naming a column also has a trap

The usual advice is to group by name or alias. In PostgreSQL there is a catch: in `GROUP BY`, a bare name is resolved as an input column first and only then as an output alias. In `ORDER BY` it is the other way round. If an alias has the same name as a table column, the two clauses can refer to different things.

```sql
SELECT EXTRACT(year FROM hired_at) AS hired_at, COUNT(*)
FROM employees
GROUP BY hired_at
ORDER BY hired_at;
```

```result
 hired_at | count
----------+-------
     2019 |     1
     2020 |     1
     2020 |     1
     2021 |     1
     2021 |     1
     2022 |     1
     2022 |     1
     2023 |     1
     2023 |     1
(9 rows)
```

The `hired_at` in `GROUP BY` is the date column, so the query groups by each exact date and each year shows up twice instead of being counted once per year. Use an alias that does not collide with a column, and the intent is clear:

```sql
SELECT EXTRACT(year FROM hired_at)::INT AS hired_year, COUNT(*)
FROM employees
GROUP BY hired_year
ORDER BY hired_year;
```

```result
 hired_year | count
------------+-------
       2019 |     1
       2020 |     2
       2021 |     2
       2022 |     2
       2023 |     2
(5 rows)
```

## When ordinals are fine

**Ad hoc queries.** When you are poking at data in psql, `GROUP BY 1` is fast and nobody will maintain it:

```sql
SELECT department, COUNT(*)
FROM employees
GROUP BY 1
ORDER BY 1;
```

```result
 department  | count
-------------+-------
 Engineering |     3
 Marketing   |     3
 Sales       |     3
(3 rows)
```

**Long expressions.** If the grouping expression is a long `CASE` or a `DATE_TRUNC`, an alias avoids repeating it:

```sql
SELECT DATE_TRUNC('month', hired_at)::DATE AS hire_month,
       department,
       COUNT(*) AS hires
FROM employees
GROUP BY hire_month, department
ORDER BY hire_month, department
LIMIT 3;
```

```result
 hire_month | department | hires
------------+------------+-------
 2019-04-01 | Sales      |     1
 2020-08-01 | Marketing  |     1
 2020-11-01 | Sales      |     1
(3 rows)
```

The alias works in PostgreSQL, MySQL, BigQuery and SQLite, but not in SQL Server, which only accepts column expressions in `GROUP BY`. For code that has to run on SQL Server, put the expression in a subquery or CTE and group by its output columns:

```sql
WITH hires AS (
    SELECT DATE_TRUNC('month', hired_at)::DATE AS hire_month, department
    FROM employees
)
SELECT hire_month, department, COUNT(*) AS hires
FROM hires
GROUP BY hire_month, department
ORDER BY hire_month, department
LIMIT 3;
```

```result
 hire_month | department | hires
------------+------------+-------
 2019-04-01 | Sales      |     1
 2020-08-01 | Marketing  |     1
 2020-11-01 | Sales      |     1
(3 rows)
```

**UNION.** `ORDER BY` on a combined query may only refer to output columns by name or position, not to expressions. If the column names differ between the branches, the position is the plain way to say it:

```sql
SELECT name AS label FROM employees WHERE id < 3
UNION ALL
SELECT department FROM employees WHERE id = 9
ORDER BY 1 DESC;
```

```result
   label
-----------
 Marketing
 Bob
 Alice
(3 rows)
```

**Generated SQL.** Some analytics teams write ordinals on purpose in dbt models and BigQuery queries, and some style guides recommend it. That is a team convention with a cost: every edit to the select list needs a matching edit to the `GROUP BY`. If you adopt it, review for it.

## Support across databases

| Database | `GROUP BY` position | `ORDER BY` position | `GROUP BY` alias |
| --- | --- | --- | --- |
| PostgreSQL | yes | yes | yes |
| MySQL | yes | yes | yes |
| SQLite | yes | yes | yes |
| BigQuery | yes | yes | yes |
| SQL Server | no | yes | no |

SQL Server rejects `GROUP BY 1`, so the trap is not there for grouping, but it applies to `ORDER BY 2` just the same.

## In short

- Ordinals in `GROUP BY` and `ORDER BY` point at the position in the `SELECT` list, so editing the list silently changes what they mean.
- In PostgreSQL a shifted `GROUP BY` usually produces an error, though one that points at a different column than the one you edited. A shifted `ORDER BY` produces a wrong result with no error.
- Group and sort by column names, or by an alias that does not collide with a column name; beware that `GROUP BY` prefers the table column over an alias of the same name.
- Positions are fine in ad hoc queries and in `ORDER BY` after a `UNION`.
