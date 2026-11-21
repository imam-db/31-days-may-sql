---
day: 4
title: "COUNT(DISTINCT) OVER: Counting Unique Values per Window"
summary: "PostgreSQL does not allow DISTINCT inside a window function. Four ways to get a distinct count next to every row: a pre-aggregated join, a correlated subquery, the DENSE_RANK trick and a first-occurrence flag."
teaser: "PostgreSQL rejects COUNT(DISTINCT x) OVER; four ways to get the number anyway."
---

You know `COUNT(DISTINCT col)` and you know window functions, so sooner or later you try to combine them. The setup is a table of employees and the projects they work on:

```sql
CREATE TABLE assignments (
    department  TEXT,
    employee    TEXT,
    project     TEXT
);

INSERT INTO assignments VALUES
    ('Engineering', 'Alice', 'Atlas'),
    ('Engineering', 'Alice', 'Beacon'),
    ('Engineering', 'Bob',   'Atlas'),
    ('Engineering', 'Bob',   'Atlas'),   -- duplicate assignment
    ('Engineering', 'Carol', 'Beacon'),
    ('Engineering', 'Carol', 'Cipher'),
    ('Sales',       'Dave',  'Atlas'),
    ('Sales',       'Dave',  'Atlas'),   -- duplicate assignment
    ('Sales',       'Eve',   'Atlas'),
    ('Sales',       'Eve',   'Beacon');
```

For every row, show how many different projects exist in that employee's department. Engineering has three (Atlas, Beacon, Cipher) and Sales has two (Atlas, Beacon). The query that comes to mind:

```sql norun
SELECT department, employee,
       COUNT(DISTINCT project) OVER (PARTITION BY department) AS unique_projects
FROM assignments;
```

```text
ERROR:  DISTINCT is not implemented for window functions
LINE 3:        COUNT(DISTINCT project) OVER (PARTITION BY department) AS...
```

PostgreSQL does not implement `DISTINCT` in an aggregate used as a window function. SQL Server rejects it as well, and MySQL does not accept it. Oracle (with only a `PARTITION BY`, no `ORDER BY` or frame) and DuckDB do allow it, so check your engine's documentation before assuming a workaround is needed. The rest of this day uses PostgreSQL 18.

## Join a pre-aggregated query

The `GROUP BY` that computes the distinct count already exists, so compute it once per department and join it back:

```sql
WITH department_counts AS (
    SELECT department, COUNT(DISTINCT project) AS unique_projects
    FROM assignments
    GROUP BY department
)
SELECT a.department, a.employee, a.project, d.unique_projects
FROM assignments a
JOIN department_counts d USING (department)
ORDER BY a.department, a.employee, a.project;
```

```result
 department  | employee | project | unique_projects
-------------+----------+---------+-----------------
 Engineering | Alice    | Atlas   |               3
 Engineering | Alice    | Beacon  |               3
 Engineering | Bob      | Atlas   |               3
 Engineering | Bob      | Atlas   |               3
 Engineering | Carol    | Beacon  |               3
 Engineering | Carol    | Cipher  |               3
 Sales       | Dave     | Atlas   |               2
 Sales       | Dave     | Atlas   |               2
 Sales       | Eve      | Atlas   |               2
 Sales       | Eve      | Beacon  |               2
(10 rows)
```

This is the version I would write first. It is easy to read, the distinct count is computed once per group, and `COUNT(DISTINCT)` already skips `NULL`s the way you expect. It gets awkward only when the "window" is not a plain partition, or when the query already has several window functions over the same partition.

## Correlated subquery

A scalar subquery in the select list gives the same result:

```sql
SELECT a.department, a.employee, a.project,
       (SELECT COUNT(DISTINCT b.project)
        FROM assignments b
        WHERE b.department = a.department) AS unique_projects
FROM assignments a
ORDER BY a.department, a.employee, a.project;
```

```result
 department  | employee | project | unique_projects
-------------+----------+---------+-----------------
 Engineering | Alice    | Atlas   |               3
 Engineering | Alice    | Beacon  |               3
 Engineering | Bob      | Atlas   |               3
 Engineering | Bob      | Atlas   |               3
 Engineering | Carol    | Beacon  |               3
 Engineering | Carol    | Cipher  |               3
 Sales       | Dave     | Atlas   |               2
 Sales       | Dave     | Atlas   |               2
 Sales       | Eve      | Atlas   |               2
 Sales       | Eve      | Beacon  |               2
(10 rows)
```

The plan shows the cost:

```sql
EXPLAIN (COSTS OFF)
SELECT a.department, a.employee, a.project,
       (SELECT COUNT(DISTINCT b.project)
        FROM assignments b
        WHERE b.department = a.department) AS unique_projects
FROM assignments a;
```

```result
                        QUERY PLAN
-----------------------------------------------------------
 Seq Scan on assignments a
   SubPlan 1
     ->  Aggregate
           ->  Sort
                 Sort Key: b.project
                 ->  Seq Scan on assignments b
                       Filter: (department = a.department)
(7 rows)
```

`SubPlan 1` runs once for each row of `a`, and each run scans `assignments` again and sorts the matching rows. With ten rows that does not matter. With millions of rows the work grows with rows times department size, unless an index on `department` makes each run cheap.

## The DENSE_RANK trick

If you want to stay in a single pass over the table, rank the distinct values inside each partition and take the largest rank. `DENSE_RANK()` gives equal values the same rank and has no gaps, so the highest rank equals the number of distinct values:

```sql
WITH ranked AS (
    SELECT department, employee, project,
           DENSE_RANK() OVER (PARTITION BY department ORDER BY project) AS dr
    FROM assignments
)
SELECT department, employee, project,
       MAX(dr) OVER (PARTITION BY department) AS unique_projects
FROM ranked
ORDER BY department, employee, project;
```

```result
 department  | employee | project | unique_projects
-------------+----------+---------+-----------------
 Engineering | Alice    | Atlas   |               3
 Engineering | Alice    | Beacon  |               3
 Engineering | Bob      | Atlas   |               3
 Engineering | Bob      | Atlas   |               3
 Engineering | Carol    | Beacon  |               3
 Engineering | Carol    | Cipher  |               3
 Sales       | Dave     | Atlas   |               2
 Sales       | Dave     | Atlas   |               2
 Sales       | Eve      | Atlas   |               2
 Sales       | Eve      | Beacon  |               2
(10 rows)
```

In Engineering the ranks are Atlas 1, Beacon 2, Cipher 3, and both of Bob's Atlas rows get rank 1. `MAX(dr)` over the department is 3. The plan has one sort and two `WindowAgg` nodes, and no repeated scans:

```sql
EXPLAIN (COSTS OFF)
WITH ranked AS (
    SELECT department, employee, project,
           DENSE_RANK() OVER (PARTITION BY department ORDER BY project) AS dr
    FROM assignments
)
SELECT department, employee, project,
       MAX(dr) OVER (PARTITION BY department) AS unique_projects
FROM ranked;
```

```result
                                                    QUERY PLAN
-------------------------------------------------------------------------------------------------------------------
 WindowAgg
   Window: w1 AS (PARTITION BY assignments.department)
   ->  WindowAgg
         Window: w1 AS (PARTITION BY assignments.department ORDER BY assignments.project ROWS UNBOUNDED PRECEDING)
         ->  Sort
               Sort Key: assignments.department, assignments.project
               ->  Seq Scan on assignments
(7 rows)
```

### Why DENSE_RANK and not RANK or ROW_NUMBER

Take three rows, two of them equal:

```sql
SELECT project,
       ROW_NUMBER() OVER w AS row_number,
       RANK()       OVER w AS rank,
       DENSE_RANK() OVER w AS dense_rank
FROM (VALUES ('Atlas'), ('Atlas'), ('Beacon')) AS t(project)
WINDOW w AS (ORDER BY project)
ORDER BY project;
```

```result
 project | row_number | rank | dense_rank
---------+------------+------+------------
 Atlas   |          1 |    1 |          1
 Atlas   |          2 |    1 |          1
 Beacon  |          3 |    3 |          2
(3 rows)
```

There are two distinct projects. The largest `row_number` is 3 because duplicates get different numbers, and the largest `rank` is 3 because it skips 2 after the tie. Only `dense_rank` ends at 2.

## NULLs

`COUNT(DISTINCT project)` ignores `NULL`s, but `DENSE_RANK` ranks a `NULL` like any other value. Add a Sales employee with no project yet:

```sql
INSERT INTO assignments VALUES ('Sales', 'Frank', NULL);

WITH ranked AS (
    SELECT department, employee, project,
           DENSE_RANK() OVER (PARTITION BY department ORDER BY project) AS dr
    FROM assignments
)
SELECT department, employee, project,
       MAX(dr) OVER (PARTITION BY department) AS unique_projects
FROM ranked
WHERE department = 'Sales'
ORDER BY employee, project;
```

```result
 department | employee | project | unique_projects
------------+----------+---------+-----------------
 Sales      | Dave     | Atlas   |               3
 Sales      | Dave     | Atlas   |               3
 Sales      | Eve      | Atlas   |               3
 Sales      | Eve      | Beacon  |               3
 Sales      | Frank    |         |               3
(5 rows)
```

Sales has two projects, and the query says three because `NULL` got a rank. Give `NULL` rows a `NULL` rank, which `MAX` ignores:

```sql
WITH ranked AS (
    SELECT department, employee, project,
           CASE WHEN project IS NOT NULL
                THEN DENSE_RANK() OVER (PARTITION BY department ORDER BY project)
           END AS dr
    FROM assignments
)
SELECT department, employee, project,
       MAX(dr) OVER (PARTITION BY department) AS unique_projects
FROM ranked
WHERE department = 'Sales'
ORDER BY employee, project;
```

```result
 department | employee | project | unique_projects
------------+----------+---------+-----------------
 Sales      | Dave     | Atlas   |               2
 Sales      | Dave     | Atlas   |               2
 Sales      | Eve      | Atlas   |               2
 Sales      | Eve      | Beacon  |               2
 Sales      | Frank    |         |               2
(5 rows)
```

This fix quietly depends on where `NULL`s sort. In PostgreSQL an ascending sort puts them last, so the real values are ranked 1, 2, ... without a gap. In a database that sorts `NULL` first (MySQL, SQL Server) or if you write `ORDER BY project NULLS FIRST` in PostgreSQL, `NULL` takes rank 1, the real values start at 2, and `MAX` is off by one even with the `CASE`. Day 5 covers where `NULL`s sort. Here the safest option is to state it:

```sql
WITH ranked AS (
    SELECT department, employee, project,
           CASE WHEN project IS NOT NULL
                THEN DENSE_RANK() OVER (PARTITION BY department ORDER BY project NULLS LAST)
           END AS dr
    FROM assignments
)
SELECT DISTINCT department, MAX(dr) OVER (PARTITION BY department) AS unique_projects
FROM ranked
ORDER BY department;
```

```result
 department  | unique_projects
-------------+-----------------
 Engineering |               3
 Sales       |               2
(2 rows)
```

## A first-occurrence flag

A different formulation avoids the dependence on `NULL` ordering. Number the rows within each (department, project) pair. The first row of a pair has `row_number = 1`, so summing a 1 for each first row counts the distinct projects:

```sql
WITH flagged AS (
    SELECT department, employee, project,
           ROW_NUMBER() OVER (PARTITION BY department, project) AS rn
    FROM assignments
)
SELECT department, employee, project,
       COUNT(*) FILTER (WHERE rn = 1 AND project IS NOT NULL)
           OVER (PARTITION BY department) AS unique_projects
FROM flagged
ORDER BY department, employee, project;
```

```result
 department  | employee | project | unique_projects
-------------+----------+---------+-----------------
 Engineering | Alice    | Atlas   |               3
 Engineering | Alice    | Beacon  |               3
 Engineering | Bob      | Atlas   |               3
 Engineering | Bob      | Atlas   |               3
 Engineering | Carol    | Beacon  |               3
 Engineering | Carol    | Cipher  |               3
 Sales       | Dave     | Atlas   |               2
 Sales       | Dave     | Atlas   |               2
 Sales       | Eve      | Atlas   |               2
 Sales       | Eve      | Beacon  |               2
 Sales       | Frank    |         |               2
(11 rows)
```

The same flag also gives something the `DENSE_RANK` trick cannot: a running distinct count. Number each visitor's first visit, then sum the flags in date order:

```sql
CREATE TABLE visits (visit_date DATE, visitor TEXT);

INSERT INTO visits VALUES
    ('2025-01-01', 'ann'),
    ('2025-01-01', 'bob'),
    ('2025-01-02', 'ann'),
    ('2025-01-03', 'cat'),
    ('2025-01-03', 'bob'),
    ('2025-01-04', 'ann');

WITH flagged AS (
    SELECT visit_date, visitor,
           ROW_NUMBER() OVER (PARTITION BY visitor ORDER BY visit_date) = 1 AS first_visit
    FROM visits
)
SELECT DISTINCT visit_date,
       COUNT(*) FILTER (WHERE first_visit) OVER (ORDER BY visit_date) AS distinct_visitors_so_far
FROM flagged
ORDER BY visit_date;
```

```result
 visit_date | distinct_visitors_so_far
------------+--------------------------
 2025-01-01 |                        2
 2025-01-02 |                        2
 2025-01-03 |                        3
 2025-01-04 |                        3
(4 rows)
```

## In short

- `COUNT(DISTINCT x) OVER (...)` is an error in PostgreSQL.
- A join to a `GROUP BY` subquery is the simplest correct answer. A correlated subquery works but runs once per row.
- `MAX(DENSE_RANK() ...)` gives the distinct count in one pass. Handle `NULL`s with a `CASE`, and make sure they sort last.
- A first-occurrence flag (`ROW_NUMBER() = 1`) is not affected by `NULL` ordering and also works for running distinct counts.
