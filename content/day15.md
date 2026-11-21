---
day: 15
title: "Recursive CTE: Graph Traversal in SQL"
summary: "Walk a tree or graph of unknown depth with WITH RECURSIVE: org charts, ancestors, dependency graphs, UNION versus UNION ALL, and stopping cycles with PostgreSQL's CYCLE clause."
teaser: "Org charts and dependency graphs of unknown depth, including cycles and the CYCLE clause."
---

An org chart table has an `employee_id` and a `manager_id`, and nothing else about the hierarchy. You are asked for everyone who reports to the VP of Engineering, directly or through other managers. No column says how many levels sit between the VP and a given person, and nobody knows in advance how deep the chain goes.

A recursive common table expression (`WITH RECURSIVE`, standard since SQL:1999) answers that: it starts from some rows, then keeps joining the table to the rows it found in the previous step until a step finds nothing new. The examples run on PostgreSQL 18.

## The data

```sql
CREATE TABLE employees (
    employee_id  INT PRIMARY KEY,
    name         TEXT NOT NULL,
    title        TEXT NOT NULL,
    manager_id   INT REFERENCES employees (employee_id)
);

INSERT INTO employees VALUES
    (1,  'Sarah Chen',     'CEO',                 NULL),
    (2,  'Marcus Johnson', 'VP Engineering',      1),
    (3,  'Lisa Park',      'VP Product',          1),
    (4,  'James Wilson',   'Director Backend',    2),
    (5,  'Anna Schmidt',   'Director Frontend',   2),
    (6,  'David Kim',      'Director Data',       2),
    (7,  'Rachel Green',   'Engineering Manager', 4),
    (8,  'Tom Baker',      'Engineering Manager', 4),
    (9,  'Nina Patel',     'Senior Engineer',     7),
    (10, 'Oscar Rivera',   'Senior Engineer',     7),
    (11, 'Emma Liu',       'Engineer',            8),
    (12, 'Alex Turner',    'Engineer',            5),
    (13, 'Sophie Martin',  'Product Manager',     3),
    (14, 'Ryan O''Brien',  'Data Engineer',       6),
    (15, 'Mia Zhang',      'Data Analyst',        6);
```

Marcus Johnson (2) is the VP of Engineering. The chain below him has four levels: Marcus, three directors, managers and engineers under them, and senior engineers under the managers.

## Why hard-coded levels fail

Without recursion you can fetch one level per query by nesting subqueries:

```sql norun
-- direct reports of 2
SELECT * FROM employees WHERE manager_id = 2;

-- reports of the direct reports
SELECT * FROM employees
WHERE manager_id IN (SELECT employee_id FROM employees WHERE manager_id = 2);

-- one more level: another nested subquery, and so on
```

Each extra level needs another nested subquery, so the query has to be rewritten whenever the hierarchy gets deeper than the depth you wrote down.

## How a recursive CTE works

A recursive CTE has two parts joined by `UNION ALL`:

1. The anchor, a normal query that supplies the starting rows.
2. The recursive step, a query that joins the table to the CTE itself.

PostgreSQL runs the anchor once. It then runs the recursive step over only the rows produced by the previous round, adds the new rows to the result, and repeats until a round returns no rows. Each round therefore finds the next layer of the graph, which is why the order of rows without an `ORDER BY` looks like a breadth-first search. That order is an implementation detail and not something to rely on.

## Everyone under Marcus

The anchor is Marcus himself, with depth 1. The recursive step finds the people whose manager is already in the result and adds one to the depth:

```sql
WITH RECURSIVE org_tree AS (
    SELECT employee_id, name, title, 1 AS depth
    FROM employees
    WHERE employee_id = 2

    UNION ALL

    SELECT e.employee_id, e.name, e.title, t.depth + 1
    FROM employees e
    JOIN org_tree t ON e.manager_id = t.employee_id
)
SELECT employee_id, name, title, depth
FROM org_tree
ORDER BY depth, employee_id;
```

```result
 employee_id |      name      |        title        | depth
-------------+----------------+---------------------+-------
           2 | Marcus Johnson | VP Engineering      |     1
           4 | James Wilson   | Director Backend    |     2
           5 | Anna Schmidt   | Director Frontend   |     2
           6 | David Kim      | Director Data       |     2
           7 | Rachel Green   | Engineering Manager |     3
           8 | Tom Baker      | Engineering Manager |     3
          12 | Alex Turner    | Engineer            |     3
          14 | Ryan O'Brien   | Data Engineer       |     3
          15 | Mia Zhang      | Data Analyst        |     3
           9 | Nina Patel     | Senior Engineer     |     4
          10 | Oscar Rivera   | Senior Engineer     |     4
          11 | Emma Liu       | Engineer            |     4
(12 rows)
```

Twelve rows: Marcus at depth 1, his three directors at depth 2, five people at depth 3, and three at depth 4. Lisa Park, Sophie Martin and Sarah Chen are not in the result, since none of them sits below Marcus.

## Showing the tree

`ORDER BY depth` lists layers. To print the hierarchy as a tree, with each person directly under their manager, add `SEARCH DEPTH FIRST` (PostgreSQL 14 and later). It adds a column that sorts the rows in depth-first order, with siblings ordered by the column you name:

```sql
WITH RECURSIVE org_tree AS (
    SELECT employee_id, name, title, 1 AS depth
    FROM employees
    WHERE employee_id = 2

    UNION ALL

    SELECT e.employee_id, e.name, e.title, t.depth + 1
    FROM employees e
    JOIN org_tree t ON e.manager_id = t.employee_id
)
SEARCH DEPTH FIRST BY employee_id SET tree_order
SELECT REPEAT('  ', depth - 1) || name AS org_chart, title
FROM org_tree
ORDER BY tree_order;
```

```result
     org_chart      |        title
--------------------+---------------------
 Marcus Johnson     | VP Engineering
   James Wilson     | Director Backend
     Rachel Green   | Engineering Manager
       Nina Patel   | Senior Engineer
       Oscar Rivera | Senior Engineer
     Tom Baker      | Engineering Manager
       Emma Liu     | Engineer
   Anna Schmidt     | Director Frontend
     Alex Turner    | Engineer
   David Kim        | Director Data
     Ryan O'Brien   | Data Engineer
     Mia Zhang      | Data Analyst
(12 rows)
```

`SEARCH BREADTH FIRST BY ...` gives the layer-by-layer order instead. Without the clause you can build the same effect by carrying an array of ids in the recursion (`path || e.employee_id`) and sorting by it. The array also doubles as a breadcrumb: a text column that appends `' > ' || e.name` at each step gives "Marcus Johnson > James Wilson > Rachel Green > Nina Patel" for each row.

## Limiting depth

To stop two levels below Marcus, add a condition to the recursive step:

```sql
WITH RECURSIVE org_tree AS (
    SELECT employee_id, name, 1 AS depth
    FROM employees
    WHERE employee_id = 2

    UNION ALL

    SELECT e.employee_id, e.name, t.depth + 1
    FROM employees e
    JOIN org_tree t ON e.manager_id = t.employee_id
    WHERE t.depth < 3
)
SELECT employee_id, name, depth
FROM org_tree
ORDER BY depth, employee_id;
```

```result
 employee_id |      name      | depth
-------------+----------------+-------
           2 | Marcus Johnson |     1
           4 | James Wilson   |     2
           5 | Anna Schmidt   |     2
           6 | David Kim      |     2
           7 | Rachel Green   |     3
           8 | Tom Baker      |     3
          12 | Alex Turner    |     3
          14 | Ryan O'Brien   |     3
          15 | Mia Zhang      |     3
(9 rows)
```

The check sits in the recursive step and stops rows from being expanded at depth 3. A filter in the outer `SELECT` would give the same rows, but only after the whole subtree had been built.

## Walking up the tree

For the chain of command above a person, swap the join so that you move from a row to its manager. Here is the path from Nina Patel (9) to the CEO:

```sql
WITH RECURSIVE chain AS (
    SELECT employee_id, name, title, manager_id, 1 AS depth
    FROM employees
    WHERE employee_id = 9

    UNION ALL

    SELECT e.employee_id, e.name, e.title, e.manager_id, c.depth + 1
    FROM employees e
    JOIN chain c ON e.employee_id = c.manager_id
)
SELECT depth, name, title
FROM chain
ORDER BY depth;
```

```result
 depth |      name      |        title
-------+----------------+---------------------
     1 | Nina Patel     | Senior Engineer
     2 | Rachel Green   | Engineering Manager
     3 | James Wilson   | Director Backend
     4 | Marcus Johnson | VP Engineering
     5 | Sarah Chen     | CEO
(5 rows)
```

Going down, the join is `child.manager_id = cte.employee_id`. Going up, it is `parent.employee_id = cte.manager_id`. Walking up can only return one row per round in a tree, so it ends by itself when it reaches the CEO, whose `manager_id` is `NULL` and matches nothing.

## Totals per manager

A recursive CTE can produce every (ancestor, descendant) pair, which is the same thing as a closure table, computed on the fly. Counting the pairs per manager gives the total team size, direct and indirect:

```sql
WITH RECURSIVE reports AS (
    SELECT manager_id AS boss_id, employee_id
    FROM employees
    WHERE manager_id IS NOT NULL

    UNION ALL

    SELECT r.boss_id, e.employee_id
    FROM reports r
    JOIN employees e ON e.manager_id = r.employee_id
)
SELECT b.name, b.title, COUNT(*) AS total_reports
FROM reports r
JOIN employees b ON b.employee_id = r.boss_id
GROUP BY b.employee_id, b.name, b.title
ORDER BY total_reports DESC, b.name;
```

```result
      name      |        title        | total_reports
----------------+---------------------+---------------
 Sarah Chen     | CEO                 |            14
 Marcus Johnson | VP Engineering      |            11
 James Wilson   | Director Backend    |             5
 David Kim      | Director Data       |             2
 Rachel Green   | Engineering Manager |             2
 Anna Schmidt   | Director Frontend   |             1
 Lisa Park      | VP Product          |             1
 Tom Baker      | Engineering Manager |             1
(8 rows)
```

Marcus has 11 people under him: three directors, five people at depth 3 and three at depth 4. Sarah has 14, which is everyone else in the company.

## Categories

The same shape fits any parent and child table. Here is a category tree, printed with indentation:

```sql
CREATE TABLE categories (
    category_id  INT PRIMARY KEY,
    name         TEXT NOT NULL,
    parent_id    INT REFERENCES categories (category_id)
);

INSERT INTO categories VALUES
    (1, 'Electronics',    NULL),
    (2, 'Computers',      1),
    (3, 'Laptops',        2),
    (4, 'Gaming Laptops', 3),
    (5, 'Phones',         1),
    (6, 'Smartphones',    5),
    (7, 'Accessories',    1);

WITH RECURSIVE tree AS (
    SELECT category_id, name, 1 AS depth
    FROM categories
    WHERE parent_id IS NULL

    UNION ALL

    SELECT c.category_id, c.name, t.depth + 1
    FROM categories c
    JOIN tree t ON c.parent_id = t.category_id
)
SEARCH DEPTH FIRST BY category_id SET tree_order
SELECT REPEAT('  ', depth - 1) || name AS category
FROM tree
ORDER BY tree_order;
```

```result
       category
----------------------
 Electronics
   Computers
     Laptops
       Gaming Laptops
   Phones
     Smartphones
   Accessories
(7 rows)
```

Sorting by `category_id` alone would only look right if ids happened to be assigned in tree order. `SEARCH DEPTH FIRST` does not depend on that.

## Graphs that are not trees: UNION versus UNION ALL

A package manager has dependencies, and a package can be reached by several routes (a diamond): the app uses `api` and `auth`, and both use `database`. This is a directed acyclic graph, and the tree assumption that every node has one parent does not hold. Add a direct edge from `app` to `database` as well:

```sql
CREATE TABLE dependencies (
    package     TEXT NOT NULL,
    depends_on  TEXT NOT NULL,
    PRIMARY KEY (package, depends_on)
);

INSERT INTO dependencies VALUES
    ('app',   'auth'),
    ('app',   'api'),
    ('app',   'database'),
    ('api',   'database'),
    ('api',   'cache'),
    ('auth',  'database'),
    ('auth',  'crypto'),
    ('cache', 'redis-client');
```

With `UNION ALL`, every route is a separate row. `database` is reached three times (directly, through `api`, through `auth`), and each copy would be expanded further if it had dependencies of its own:

```sql
WITH RECURSIVE deps AS (
    SELECT depends_on AS package
    FROM dependencies
    WHERE package = 'app'

    UNION ALL

    SELECT d.depends_on
    FROM dependencies d
    JOIN deps ON d.package = deps.package
)
SELECT package, COUNT(*) AS times_reached
FROM deps
GROUP BY package
ORDER BY package;
```

```result
   package    | times_reached
--------------+---------------
 api          |             1
 auth         |             1
 cache        |             1
 crypto       |             1
 database     |             3
 redis-client |             1
(6 rows)
```

`UNION` removes rows that are already in the result, so each package appears once and is expanded once. PostgreSQL does accept `UNION` in a recursive CTE:

```sql
WITH RECURSIVE deps AS (
    SELECT depends_on AS package
    FROM dependencies
    WHERE package = 'app'

    UNION

    SELECT d.depends_on
    FROM dependencies d
    JOIN deps ON d.package = deps.package
)
SELECT package
FROM deps
ORDER BY package;
```

```result
   package
--------------
 api
 auth
 cache
 crypto
 database
 redis-client
(6 rows)
```

`UNION` compares entire rows. If you add a `depth` column to the select list, the same package at different depths is no longer a duplicate, and the deduplication is gone:

```sql
WITH RECURSIVE deps AS (
    SELECT depends_on AS package, 1 AS depth
    FROM dependencies
    WHERE package = 'app'

    UNION

    SELECT d.depends_on, deps.depth + 1
    FROM dependencies d
    JOIN deps ON d.package = deps.package
)
SELECT package, depth
FROM deps
WHERE package = 'database'
ORDER BY depth;
```

```result
 package  | depth
----------+-------
 database |     1
 database |     2
(2 rows)
```

`database` shows up at depth 1 and at depth 2. If you need both deduplication and a distance, deduplicate first and take `MIN(depth)` afterwards, or accept the extra rows.

How much this matters grows with the graph. In this ladder, every node of one layer links to both nodes of the next layer, so the number of routes doubles with each layer:

```sql
CREATE TABLE ladder (src TEXT NOT NULL, dst TEXT NOT NULL);

INSERT INTO ladder
SELECT a.x || i, b.y || (i + 1)
FROM generate_series(1, 14) AS i,
     (VALUES ('a'), ('b')) AS a(x),
     (VALUES ('a'), ('b')) AS b(y);

WITH RECURSIVE walk AS (
    SELECT 'a1'::text AS node
    UNION ALL
    SELECT l.dst FROM ladder l JOIN walk w ON l.src = w.node
)
SELECT COUNT(*) AS rows_with_union_all FROM walk;
```

```result
 rows_with_union_all
---------------------
               32767
(1 row)
```

```sql
WITH RECURSIVE walk AS (
    SELECT 'a1'::text AS node
    UNION
    SELECT l.dst FROM ladder l JOIN walk w ON l.src = w.node
)
SELECT COUNT(*) AS rows_with_union FROM walk;
```

```result
 rows_with_union
-----------------
              29
(1 row)
```

There are 29 distinct nodes and 32,767 routes to them. Fifteen layers is already a visible difference, and each extra layer doubles the first number. On a real dependency graph with hundreds of shared nodes, `UNION ALL` can run for a very long time or exhaust memory.

Other databases differ here. SQL Server only allows `UNION ALL` in a recursive CTE. MySQL 8 and PostgreSQL accept both. For plain trees, use `UNION ALL`: it skips the deduplication work, and nothing can be duplicated anyway.

## Cycles

In a tree, following `manager_id` always ends at the root. A data error breaks that. Suppose someone sets the CEO's manager to Emma Liu (11), who sits several levels below her. The table is a copy so the earlier examples stay valid:

```sql
CREATE TABLE employees_loop AS TABLE employees;

UPDATE employees_loop SET manager_id = 11 WHERE employee_id = 1;
```

Walking down from Sarah now goes Sarah, Marcus, James, Tom, Emma, Sarah, Marcus and so on. A recursive CTE with `UNION ALL` and no stop condition never finishes. It keeps adding rows until the server runs out of memory or disk, because PostgreSQL has no iteration limit of its own.

PostgreSQL 14 added the `CYCLE` clause for this. It records the values of the columns you name along each branch and marks a row when its values have already occurred earlier on the same branch. Marked rows are returned but not expanded:

```sql
WITH RECURSIVE walk AS (
    SELECT employee_id, name, 1 AS depth
    FROM employees_loop
    WHERE employee_id = 1

    UNION ALL

    SELECT e.employee_id, e.name, w.depth + 1
    FROM employees_loop e
    JOIN walk w ON e.manager_id = w.employee_id
)
CYCLE employee_id SET is_cycle USING path
SELECT depth, name, is_cycle
FROM walk
ORDER BY depth, employee_id;
```

```result
 depth |      name      | is_cycle
-------+----------------+----------
     1 | Sarah Chen     | f
     2 | Marcus Johnson | f
     2 | Lisa Park      | f
     3 | James Wilson   | f
     3 | Anna Schmidt   | f
     3 | David Kim      | f
     3 | Sophie Martin  | f
     4 | Rachel Green   | f
     4 | Tom Baker      | f
     4 | Alex Turner    | f
     4 | Ryan O'Brien   | f
     4 | Mia Zhang      | f
     5 | Nina Patel     | f
     5 | Oscar Rivera   | f
     5 | Emma Liu       | f
     6 | Sarah Chen     | t
(16 rows)
```

The walk visits the whole company once, and then Sarah appears again at depth 6 with `is_cycle` true, which is where the loop closes. It stops there. This also gives you a way to report the problem instead of just surviving it.

The same protection without the `CYCLE` clause, which is what you write on databases that lack it, is an array of visited ids and a condition that refuses to revisit one:

```sql
WITH RECURSIVE walk AS (
    SELECT employee_id, name, 1 AS depth, ARRAY[employee_id] AS visited
    FROM employees_loop
    WHERE employee_id = 1

    UNION ALL

    SELECT e.employee_id, e.name, w.depth + 1, w.visited || e.employee_id
    FROM employees_loop e
    JOIN walk w ON e.manager_id = w.employee_id
    WHERE NOT e.employee_id = ANY (w.visited)
)
SELECT COUNT(*) AS people_reached, MAX(depth) AS deepest
FROM walk;
```

```result
 people_reached | deepest
----------------+---------
             15 |       5
(1 row)
```

Fifteen people, with the deepest at depth 5. Here the walk stops quietly before returning to Sarah, so nothing in the output shows that a cycle exists.

To find existing cycles in a table, walk up from every employee with `CYCLE` and look at the rows it marks. Only members of a cycle ever get marked:

```sql
WITH RECURSIVE up AS (
    SELECT employee_id, manager_id FROM employees_loop

    UNION ALL

    SELECT e.employee_id, e.manager_id
    FROM employees_loop e
    JOIN up ON e.employee_id = up.manager_id
)
CYCLE employee_id SET is_cycle USING path
SELECT DISTINCT e.employee_id, e.name
FROM up
JOIN employees e ON e.employee_id = up.employee_id
WHERE up.is_cycle
ORDER BY e.employee_id;
```

```result
 employee_id |      name
-------------+----------------
           1 | Sarah Chen
           2 | Marcus Johnson
           4 | James Wilson
           8 | Tom Baker
          11 | Emma Liu
(5 rows)
```

Sarah, Marcus, James, Tom and Emma form the loop. Everyone else leads into it from outside but is not part of it.

## What to check on real tables

An index on the column the recursive step joins on (`manager_id`) decides whether each round reads a few rows or the whole table. On a generated table with 100,000 employees in a wide tree and only the primary key, every round is a hash join against a full scan of `big_employees`:

```sql
CREATE TABLE big_employees AS
SELECT g AS employee_id,
       CASE WHEN g = 1 THEN NULL ELSE (g - 2) / 5 + 1 END AS manager_id
FROM generate_series(1, 100000) AS g;

ALTER TABLE big_employees ADD PRIMARY KEY (employee_id);
ANALYZE big_employees;

EXPLAIN (COSTS OFF)
WITH RECURSIVE t AS (
    SELECT employee_id, 1 AS depth FROM big_employees WHERE employee_id = 2
    UNION ALL
    SELECT e.employee_id, t.depth + 1
    FROM big_employees e JOIN t ON e.manager_id = t.employee_id
)
SELECT COUNT(*) FROM t;
```

```result
                               QUERY PLAN
-------------------------------------------------------------------------
 Aggregate
   CTE t
     ->  Recursive Union
           ->  Index Only Scan using big_employees_pkey on big_employees
                 Index Cond: (employee_id = 2)
           ->  Hash Join
                 Hash Cond: (e.manager_id = t_1.employee_id)
                 ->  Seq Scan on big_employees e
                 ->  Hash
                       ->  WorkTable Scan on t t_1
   ->  CTE Scan on t
(11 rows)
```

After indexing `manager_id`, each round looks up only the children of the rows from the previous round:

```sql
CREATE INDEX ON big_employees (manager_id);
ANALYZE big_employees;

EXPLAIN (COSTS OFF)
WITH RECURSIVE t AS (
    SELECT employee_id, 1 AS depth FROM big_employees WHERE employee_id = 2
    UNION ALL
    SELECT e.employee_id, t.depth + 1
    FROM big_employees e JOIN t ON e.manager_id = t.employee_id
)
SELECT COUNT(*) FROM t;
```

```result
                                      QUERY PLAN
--------------------------------------------------------------------------------------
 Aggregate
   CTE t
     ->  Recursive Union
           ->  Index Only Scan using big_employees_pkey on big_employees
                 Index Cond: (employee_id = 2)
           ->  Nested Loop
                 ->  WorkTable Scan on t t_1
                 ->  Index Scan using big_employees_manager_id_idx on big_employees e
                       Index Cond: (manager_id = t_1.employee_id)
   ->  CTE Scan on t
(10 rows)
```

Other databases put a limit on recursion that PostgreSQL does not. MySQL stops after `cte_max_recursion_depth` iterations (1000 by default) and SQL Server after `MAXRECURSION` (100 by default), both with an error. In PostgreSQL, a depth condition or `CYCLE` is the only protection. SQL Server and Oracle also omit the word `RECURSIVE`; the rest of the syntax is the same.

If the hierarchy is read far more than it changes and you need subtree queries to be a single index lookup, store it differently: a closure table of (ancestor, descendant) pairs, a nested-set model, or PostgreSQL's `ltree` extension for materialised paths.

## In short

- A recursive CTE is an anchor plus a recursive step joined by `UNION ALL`, repeated until a round adds nothing.
- Down the tree: `child.parent = cte.id`. Up the tree: `parent.id = cte.parent`.
- Use `SEARCH DEPTH FIRST` to order rows as a tree, and a `depth` column to limit or label levels.
- Trees do not need `UNION`. Graphs with shared nodes do, or the number of rows grows with the number of routes. A `depth` column in the select list defeats the deduplication.
- Bad data can create cycles. Use `CYCLE` (PostgreSQL 14 and later) or an array of visited ids.
- Index the join column of the recursive step.
