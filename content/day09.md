---
day: 9
title: "Relational Division: Find Who Bought Everything"
summary: "Finding the customers who bought every product in a set: why JOIN gets it wrong, three correct forms (counting, double NOT EXISTS, EXCEPT), and the duplicate-row and empty-set traps in each."
teaser: "Customers who bought every product in a set: counting, double NOT EXISTS, EXCEPT."
---

The request: "find all customers who bought every product in the Staples category." A `JOIN` finds customers who bought some Staples product, and `GROUP BY` can count what they bought, but "every product in the set" is a different kind of question. It is called relational division, and it appears wherever a requirement says "all of": users holding every permission an action needs, candidates covering every skill in a job posting, articles carrying every tag in a filter. The queries run on PostgreSQL 18 and are standard SQL apart from the `FILTER` clause (not in MySQL) and the array variant at the end.

## The data

```sql
CREATE TABLE customers (
    customer_id  INT PRIMARY KEY,
    name         TEXT
);

CREATE TABLE products (
    product_id  INT PRIMARY KEY,
    name        TEXT,
    category    TEXT
);

CREATE TABLE purchases (
    customer_id  INT,
    product_id   INT
);

INSERT INTO customers VALUES
    (1, 'Alice'), (2, 'Bob'), (3, 'Carol'), (4, 'Dave');

INSERT INTO products VALUES
    (1, 'Bread',     'Staples'),
    (2, 'Milk',      'Staples'),
    (3, 'Eggs',      'Staples'),
    (4, 'Chocolate', 'Snacks'),
    (5, 'Chips',     'Snacks');

INSERT INTO purchases VALUES
    (1, 1), (1, 2), (1, 3), (1, 4),
    (2, 1), (2, 2),
    (3, 1), (3, 2), (3, 3),
    (4, 4), (4, 5);
```

Staples are Bread, Milk and Eggs. Alice bought all three (and Chocolate), Carol bought all three, Bob bought two, Dave bought none. The right answer is Alice and Carol.

## A join answers "at least one"

```sql
SELECT DISTINCT c.name
FROM purchases p
JOIN products  pr ON pr.product_id  = p.product_id
JOIN customers c  ON c.customer_id = p.customer_id
WHERE pr.category = 'Staples'
ORDER BY c.name;
```

```result
 name
-------
 Alice
 Bob
 Carol
(3 rows)
```

Bob is in the result with two of three. A join keeps every row that matches, so it can only say "bought at least one Staple".

## Counting

Count the distinct Staples each customer bought, and compare with the number of Staples that exist:

```sql
SELECT c.name
FROM purchases p
JOIN products  pr ON pr.product_id  = p.product_id
JOIN customers c  ON c.customer_id = p.customer_id
WHERE pr.category = 'Staples'
GROUP BY c.customer_id, c.name
HAVING COUNT(DISTINCT p.product_id) = (
    SELECT COUNT(*) FROM products WHERE category = 'Staples'
)
ORDER BY c.name;
```

```result
 name
-------
 Alice
 Carol
(2 rows)
```

Taking the total from `products` instead of writing `3` means the query stays right when someone adds a fourth Staple.

The `DISTINCT` matters when `purchases` can contain the same pair twice, which is likely with a table that has no primary key. Pretend Bob's first purchase was recorded twice, and count rows instead of distinct products:

```sql
SELECT c.name
FROM (SELECT * FROM purchases UNION ALL SELECT 2, 1) AS p
JOIN products  pr ON pr.product_id  = p.product_id
JOIN customers c  ON c.customer_id = p.customer_id
WHERE pr.category = 'Staples'
GROUP BY c.customer_id, c.name
HAVING COUNT(*) = (SELECT COUNT(*) FROM products WHERE category = 'Staples')
ORDER BY c.name;
```

```result
 name
-------
 Alice
 Bob
 Carol
(3 rows)
```

Bob's rows are Bread, Bread, Milk: three rows, so he passes. `COUNT(DISTINCT p.product_id)` does not have this problem.

## Double NOT EXISTS

Turn the question around. A customer bought every Staple if there is no Staple that the customer has not bought:

```sql
SELECT c.name
FROM customers c
WHERE NOT EXISTS (
    SELECT 1
    FROM products pr
    WHERE pr.category = 'Staples'
      AND NOT EXISTS (
          SELECT 1
          FROM purchases p
          WHERE p.customer_id = c.customer_id
            AND p.product_id  = pr.product_id
      )
)
ORDER BY c.name;
```

```result
 name
-------
 Alice
 Carol
(2 rows)
```

Read it from the outside in: for this customer, there is no product in the required set for which no purchase exists. Duplicates in `purchases` do not matter because the inner `EXISTS` only asks whether at least one row is there. SQL has no "for all" quantifier, and this is how "for all x, P(x)" is written as "not exists x such that not P(x)".

The same logic with a set operator reads closer to the English. `EXCEPT` removes from the required set everything the customer bought, and what is left is what the customer is missing:

```sql
SELECT c.name
FROM customers c
WHERE NOT EXISTS (
    SELECT product_id FROM products WHERE category = 'Staples'
    EXCEPT
    SELECT product_id FROM purchases p WHERE p.customer_id = c.customer_id
)
ORDER BY c.name;
```

```result
 name
-------
 Alice
 Carol
(2 rows)
```

Nothing left over means the customer bought every Staple.

## What if the required set is empty?

The two forms disagree when no product has the category. With `NOT EXISTS`, "there is no missing product" is trivially true for everyone:

```sql
SELECT c.name
FROM customers c
WHERE NOT EXISTS (
    SELECT 1
    FROM products pr
    WHERE pr.category = 'Gadgets'
      AND NOT EXISTS (
          SELECT 1 FROM purchases p
          WHERE p.customer_id = c.customer_id AND p.product_id = pr.product_id
      )
)
ORDER BY c.name;
```

```result
 name
-------
 Alice
 Bob
 Carol
 Dave
(4 rows)
```

All four customers, Dave included, "bought every Gadget" because there are none. The counting version goes through an inner join to the required products, finds no rows, and returns nobody. Logicians call the first behaviour vacuous truth. Which answer is wanted depends on the question; for permissions, "everyone passes an empty requirement" is usually right. If it is not, add a guard such as `AND EXISTS (SELECT 1 FROM products WHERE category = 'Gadgets')`.

## With extras or exactly the set?

All of the queries so far allow extras: Alice bought Chocolate and is still included. If you need customers whose purchases are exactly the Staples and nothing else, add a second condition: there is no purchase outside the set.

```sql
SELECT c.name
FROM customers c
WHERE NOT EXISTS (
    SELECT 1
    FROM products pr
    WHERE pr.category = 'Staples'
      AND NOT EXISTS (
          SELECT 1 FROM purchases p
          WHERE p.customer_id = c.customer_id AND p.product_id = pr.product_id
      )
)
AND NOT EXISTS (
    SELECT 1
    FROM purchases p
    JOIN products pr ON pr.product_id = p.product_id
    WHERE p.customer_id = c.customer_id
      AND pr.category <> 'Staples'
)
ORDER BY c.name;
```

```result
 name
-------
 Carol
(1 row)
```

Only Carol: Alice also bought Chocolate. The counting version of the same rule compares two counts per customer, using `FILTER` to split them:

```sql
SELECT c.name
FROM customers c
JOIN purchases p  ON p.customer_id = c.customer_id
JOIN products  pr ON pr.product_id = p.product_id
GROUP BY c.customer_id, c.name
HAVING COUNT(DISTINCT pr.product_id) FILTER (WHERE pr.category = 'Staples')
         = (SELECT COUNT(*) FROM products WHERE category = 'Staples')
   AND COUNT(DISTINCT pr.product_id) FILTER (WHERE pr.category <> 'Staples') = 0
ORDER BY c.name;
```

```result
 name
-------
 Carol
(1 row)
```

Ask which one is wanted. "Customers who bought all the Staples" almost always means extras are fine.

## How the forms compare

Counting is the form most people read fastest, and it needs `COUNT(DISTINCT ...)` and a count taken from the data rather than typed. Double `NOT EXISTS` ignores duplicates and handles the empty set the way logic does, but it takes practice to read, so it deserves a comment. Neither is reliably faster; PostgreSQL plans them very differently, so measure on your own data when the tables are large. For the small tables here, the planner turns the outer `NOT EXISTS` into an anti-join and runs the inner one as a hashed subplan over `purchases`:

```sql
ANALYZE customers;
ANALYZE products;
ANALYZE purchases;

EXPLAIN (COSTS OFF)
SELECT c.name
FROM customers c
WHERE NOT EXISTS (
    SELECT 1
    FROM products pr
    WHERE pr.category = 'Staples'
      AND NOT EXISTS (
          SELECT 1 FROM purchases p
          WHERE p.customer_id = c.customer_id AND p.product_id = pr.product_id
      )
);
```

```result
                                                      QUERY PLAN
----------------------------------------------------------------------------------------------------------------------
 Nested Loop Anti Join
   Join Filter: (NOT (ANY ((c.customer_id = (hashed SubPlan 2).col1) AND (pr.product_id = (hashed SubPlan 2).col2))))
   ->  Seq Scan on customers c
   ->  Materialize
         ->  Seq Scan on products pr
               Filter: (category = 'Staples'::text)
   SubPlan 2
     ->  Seq Scan on purchases p
(8 rows)
```

On larger tables an index on `purchases (customer_id, product_id)` gives the planner the option of probing it once per customer and product instead of hashing the whole table.

## A generic form with a requirements table

When the required set is not a category but data, such as permissions an action needs, put it in a table and divide by it:

```sql
CREATE TABLE required_products (product_id INT PRIMARY KEY);
INSERT INTO required_products VALUES (1), (2), (3);

SELECT c.name
FROM customers c
WHERE NOT EXISTS (
    SELECT 1
    FROM required_products r
    WHERE NOT EXISTS (
        SELECT 1 FROM purchases p
        WHERE p.customer_id = c.customer_id AND p.product_id = r.product_id
    )
)
ORDER BY c.name;
```

```result
 name
-------
 Alice
 Carol
(2 rows)
```

PostgreSQL also offers a compact variant with arrays: `array_agg` collects each customer's products, and `@>` tests whether that array contains all the required ones.

```sql
SELECT c.name
FROM customers c
JOIN purchases p ON p.customer_id = c.customer_id
GROUP BY c.customer_id, c.name
HAVING array_agg(p.product_id) @> (SELECT array_agg(product_id) FROM required_products)
ORDER BY c.name;
```

```result
 name
-------
 Alice
 Carol
(2 rows)
```

It is short, ignores duplicates, and is specific to PostgreSQL. For an empty required set `array_agg` returns `NULL`, so nobody matches, the same as the counting form; customers with no purchases at all are dropped by the join.

## In short

- A join finds "at least one". "Every" needs either a count compared with the size of the required set, or a double negative.
- Counting: use `COUNT(DISTINCT ...)` and take the target number from the data.
- Double `NOT EXISTS` (or `NOT EXISTS (... EXCEPT ...)`): no required item is missing. Immune to duplicates, true for an empty requirement.
- Decide whether extras are allowed and say so in the query.
