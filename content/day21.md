---
day: 21
title: "Mastering JSONB in PostgreSQL"
summary: "JSON vs JSONB, the -> and ->> operators and the error that comes from comparing them with text, containment and existence checks, which index serves which query (shown with EXPLAIN), jsonb_agg, and updating documents."
teaser: "Operators, containment, which index serves which query (with EXPLAIN), and updates."
---

Event logs, API responses and audit records often end up in a JSON column. PostgreSQL offers two types for it, `json` and `jsonb`, and a handful of operators whose differences (`->` against `->>`, `@>` against `?`) decide whether a query returns the right rows and whether an index is used. This article goes through them on a small events table and checks the index claims with `EXPLAIN` on 200,000 rows.

Everything runs on PostgreSQL 18. `jsonb` is PostgreSQL-specific; the last section lists what MySQL and SQL Server offer instead.

## The data

```sql
CREATE TABLE events (
    event_id    INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id     INT NOT NULL,
    event_type  VARCHAR(50) NOT NULL,
    occurred_at TIMESTAMP NOT NULL,
    payload     JSONB NOT NULL
);

INSERT INTO events (user_id, event_type, occurred_at, payload) VALUES
(101, 'page_view',   '2024-06-10 09:15:22',
 '{"url": "/products/laptop", "referrer": "google",
   "device": {"type": "mobile", "os": "iOS", "version": "17.2"},
   "tags": ["sale", "tech"]}'),
(101, 'add_to_cart', '2024-06-10 09:16:45',
 '{"product_id": 42, "price": 1299.99, "quantity": 1,
   "device": {"type": "mobile", "os": "iOS", "version": "17.2"}}'),
(102, 'page_view',   '2024-06-10 09:17:01',
 '{"url": "/products/phone", "referrer": "direct",
   "device": {"type": "desktop", "os": "Windows", "version": "11"},
   "tags": ["sale"]}'),
(103, 'purchase',    '2024-06-10 09:20:12',
 '{"order_id": 5001, "total": 1299.99,
   "items": [{"product_id": 42, "qty": 1}, {"product_id": 7, "qty": 2}],
   "device": {"type": "mobile", "os": "Android"}}');
```

The fixed attributes (`user_id`, `event_type`, `occurred_at`) are real columns. The part that differs per event type lives in `payload`.

## json or jsonb

`json` stores the text exactly as it was written and parses it again on every access. `jsonb` parses once on insert and stores a binary form. The visible differences:

```sql
SELECT '{"b": 1, "a":   2, "a": 3}'::json  AS as_json,
       '{"b": 1, "a":   2, "a": 3}'::jsonb AS as_jsonb;
```

```result
          as_json           |     as_jsonb
----------------------------+------------------
 {"b": 1, "a":   2, "a": 3} | {"a": 3, "b": 1}
(1 row)
```

`json` keeps the whitespace, the key order and both `a` keys. `jsonb` drops the extra whitespace, sorts keys (shorter keys first, then by byte order), and keeps only the last of duplicate keys. Numbers keep their written precision in both (`1.50` stays `1.50` in `jsonb`).

The bigger difference is what you can do with the column. A `json` column cannot be given a GIN index:

```sql norun
CREATE INDEX ON t USING gin (j);   -- j is of type json
```

```text
ERROR:  data type json has no default operator class for access method "gin"
```

`jsonb` also has the containment and existence operators used below, and it is cheaper to read because nothing has to be re-parsed per access. Use `jsonb` unless you need the original text, byte for byte, for example to verify a signature computed over it. Writes into `json` are slightly cheaper, since there is no conversion, but that rarely outweighs the rest.

## Path operators

| Operator | Returns | Meaning |
| --- | --- | --- |
| `->` | `jsonb` | field by name, or array element by position |
| `->>` | `text` | same, converted to text |
| `#>` | `jsonb` | value at a path, given as a text array |
| `#>>` | `text` | same, converted to text |

A single arrow keeps the result as JSON, so you can chain more steps. A double arrow ends the chain and gives SQL text.

```sql
SELECT event_id,
       payload -> 'device'                 AS device_jsonb,
       payload -> 'device' ->> 'type'      AS device_type,
       payload #>> '{device,os}'           AS device_os
FROM events
ORDER BY event_id;
```

```result
 event_id |                     device_jsonb                      | device_type | device_os
----------+-------------------------------------------------------+-------------+-----------
        1 | {"os": "iOS", "type": "mobile", "version": "17.2"}    | mobile      | iOS
        2 | {"os": "iOS", "type": "mobile", "version": "17.2"}    | mobile      | iOS
        3 | {"os": "Windows", "type": "desktop", "version": "11"} | desktop     | Windows
        4 | {"os": "Android", "type": "mobile"}                   | mobile      | Android
(4 rows)
```

`#>>` takes the whole path in one go, which is shorter for deep documents. Array positions in a path start at 0, and a negative position counts from the end:

```sql
SELECT payload #>> '{items,0,product_id}'     AS first_product,
       payload -> 'items' -> -1 ->> 'qty'     AS last_item_qty
FROM events
WHERE event_type = 'purchase';
```

```result
 first_product | last_item_qty
---------------+---------------
 42            | 2
(1 row)
```

A key that does not exist gives SQL `NULL`, not an error, which is convenient but hides typos. One more subtlety: a JSON `null` is not SQL `NULL` with `->`. `'{"a":null}'::jsonb -> 'a'` returns the JSON value `null`, and `->>` returns SQL `NULL`.

Values come out of `->>` as `text`. To compute with them, cast:

```sql
SELECT event_id, (payload ->> 'price')::numeric * (payload ->> 'quantity')::int AS line_total
FROM events
WHERE payload ? 'price';
```

```result
 event_id | line_total
----------+------------
        2 |    1299.99
(1 row)
```

### The comparison mistake

`->` returns `jsonb`, so comparing it with a plain string literal makes PostgreSQL try to read the literal as JSON:

```sql norun
SELECT * FROM events WHERE payload -> 'referrer' = 'google';
```

```text
ERROR:  invalid input syntax for type json
DETAIL:  Token "google" is invalid.
```

A JSON string includes its double quotes, and `google` without quotes is not valid JSON. There are two correct forms:

```sql
SELECT event_id FROM events WHERE payload ->> 'referrer' = 'google';
SELECT event_id FROM events WHERE payload -> 'referrer' = '"google"'::jsonb;
```

```result
 event_id
----------
        1
(1 row)
```

Both return event 1. Use `->>` when comparing with ordinary SQL values, which is almost always the case. If you compare two `->` results with each other, they are compared as JSON.

## Containment and existence

`@>` asks whether the document on the left contains the fragment on the right. A fragment can name nested objects and array members:

```sql
SELECT event_id FROM events WHERE payload @> '{"device": {"type": "mobile"}}' ORDER BY event_id;
```

```result
 event_id
----------
        1
        2
        4
(3 rows)
```

```sql
SELECT event_id FROM events WHERE payload @> '{"tags": ["sale"]}' ORDER BY event_id;
```

```result
 event_id
----------
        1
        3
(2 rows)
```

The second query finds both page views, because their `tags` arrays contain `sale`. Containment on arrays matches members, not the whole array.

The existence operators check keys:

- `payload ? 'order_id'`: does the top-level key exist
- `payload ?| ARRAY['order_id', 'product_id']`: does any of these keys exist
- `payload ?& ARRAY['device', 'url']`: do all of these keys exist

```sql
SELECT event_id,
       payload ? 'order_id'                           AS has_order_id,
       payload ?| ARRAY['order_id', 'product_id']     AS has_any,
       payload ?& ARRAY['device', 'url']              AS has_all
FROM events
ORDER BY event_id;
```

```result
 event_id | has_order_id | has_any | has_all
----------+--------------+---------+---------
        1 | f            | f       | t
        2 | f            | t       | f
        3 | f            | f       | t
        4 | t            | t       | f
(4 rows)
```

`?` looks only at the top level. `payload ? 'type'` is false for every row, although `device.type` exists. For a nested key, use a JSON path expression with `@?` (PostgreSQL 12 and later):

```sql
SELECT event_id,
       payload ? 'type'              AS top_level_type,
       payload @? '$.device.type'    AS nested_type,
       payload @? '$.items[*] ? (@.qty > 1)' AS has_item_with_qty_over_1
FROM events
ORDER BY event_id;
```

```result
 event_id | top_level_type | nested_type | has_item_with_qty_over_1
----------+----------------+-------------+--------------------------
        1 | f              | t           | f
        2 | f              | t           | f
        3 | f              | t           | f
        4 | f              | t           | t
(4 rows)
```

The last column uses a filter inside the path: it is true when some element of `items` has `qty` above 1. The same `?` can be asked as containment, `payload @> '{"device": {}}'`, but that only tells you that `device` is an object.

## Which index serves which query

Four rows say nothing about indexes, so here is a table of 200,000 events with the same shape of data:

```sql
CREATE TABLE big_events (
    event_id    INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id     INT NOT NULL,
    event_type  TEXT NOT NULL,
    occurred_at TIMESTAMP NOT NULL,
    payload     JSONB NOT NULL
);

INSERT INTO big_events (user_id, event_type, occurred_at, payload)
SELECT g % 5000,
       CASE WHEN g % 10 = 0 THEN 'purchase' ELSE 'page_view' END,
       TIMESTAMP '2024-06-01' + g * interval '1 second',
       jsonb_strip_nulls(jsonb_build_object(
           'url',      '/products/p' || (g % 1000),
           'device',   jsonb_build_object('type', (ARRAY['mobile','desktop','tablet'])[1 + g % 3],
                                          'os',   (ARRAY['iOS','Android','Windows','macOS'])[1 + g % 4]),
           'tags',     CASE WHEN g % 500 = 0 THEN jsonb_build_array('sale', 'beta')
                            ELSE jsonb_build_array('sale') END,
           'order_id', CASE WHEN g % 500 = 1 THEN g END))
FROM generate_series(1, 200000) g;

ANALYZE big_events;
```

Of the 200,000 rows, 400 have a `beta` tag, 400 have an `order_id`, and 200 have the url `/products/p42`. These are selective filters, which is when an index pays off. The plan lines below show the shape that matters: `Seq Scan` against `Bitmap Index Scan`. The number of parallel workers on a `Gather` node depends on the machine.

Without any index, a filter on a field is a scan of the whole table:

```sql
EXPLAIN (COSTS OFF)
SELECT count(*) FROM big_events WHERE payload ->> 'url' = '/products/p42';
```

```result
                                   QUERY PLAN
---------------------------------------------------------------------------------
 Finalize Aggregate
   ->  Gather
         Workers Planned: 2
         ->  Partial Aggregate
               ->  Parallel Seq Scan on big_events
                     Filter: ((payload ->> 'url'::text) = '/products/p42'::text)
(6 rows)
```

### Expression index: one known field

If queries always filter on the same field, index that expression. The query has to use the same expression:

```sql
CREATE INDEX big_events_url_idx ON big_events ((payload ->> 'url'));
ANALYZE big_events;

EXPLAIN (COSTS OFF)
SELECT count(*) FROM big_events WHERE payload ->> 'url' = '/products/p42';
```

```result
                                  QUERY PLAN
-------------------------------------------------------------------------------
 Aggregate
   ->  Bitmap Heap Scan on big_events
         Recheck Cond: ((payload ->> 'url'::text) = '/products/p42'::text)
         ->  Bitmap Index Scan on big_events_url_idx
               Index Cond: ((payload ->> 'url'::text) = '/products/p42'::text)
(5 rows)
```

This is an ordinary B-tree on text, so it also supports `=`, `IN`, ranges and `ORDER BY` on that field, and `ANALYZE` collects statistics for the expression, which gives the planner a real selectivity estimate.

### GIN index: arbitrary containment and existence

A GIN index on the whole column serves `@>`, `?`, `?|`, `?&` and the path operators `@?` and `@@`:

```sql
CREATE INDEX big_events_gin_idx ON big_events USING gin (payload);
ANALYZE big_events;

EXPLAIN (COSTS OFF)
SELECT count(*) FROM big_events WHERE payload @> '{"tags": ["beta"]}';
```

```result
                             QUERY PLAN
--------------------------------------------------------------------
 Aggregate
   ->  Bitmap Heap Scan on big_events
         Recheck Cond: (payload @> '{"tags": ["beta"]}'::jsonb)
         ->  Bitmap Index Scan on big_events_gin_idx
               Index Cond: (payload @> '{"tags": ["beta"]}'::jsonb)
(5 rows)
```

```sql
EXPLAIN (COSTS OFF)
SELECT count(*) FROM big_events WHERE payload ? 'order_id';
```

```result
                       QUERY PLAN
--------------------------------------------------------
 Aggregate
   ->  Bitmap Heap Scan on big_events
         Recheck Cond: (payload ? 'order_id'::text)
         ->  Bitmap Index Scan on big_events_gin_idx
               Index Cond: (payload ? 'order_id'::text)
(5 rows)
```

Both plans use the GIN index. The index does not help `->>` comparisons. For `payload ->> 'url' = ...` the planner only considers the B-tree expression index; without it, the GIN index would be ignored and the table scanned. Two indexes can also be combined in one plan when a query has both kinds of condition:

```sql
EXPLAIN (COSTS OFF)
SELECT count(*) FROM big_events
WHERE payload ->> 'url' = '/products/p42' AND payload @> '{"tags": ["beta"]}';
```

```result
                                                        QUERY PLAN
--------------------------------------------------------------------------------------------------------------------------
 Aggregate
   ->  Bitmap Heap Scan on big_events
         Recheck Cond: (((payload ->> 'url'::text) = '/products/p42'::text) AND (payload @> '{"tags": ["beta"]}'::jsonb))
         ->  BitmapAnd
               ->  Bitmap Index Scan on big_events_url_idx
                     Index Cond: ((payload ->> 'url'::text) = '/products/p42'::text)
               ->  Bitmap Index Scan on big_events_gin_idx
                     Index Cond: (payload @> '{"tags": ["beta"]}'::jsonb)
(8 rows)
```

`BitmapAnd` intersects the two bitmaps before visiting the table.

### jsonb_path_ops: smaller, containment only

`jsonb_path_ops` indexes hashes of paths and values. It supports `@>`, `@?` and `@@`, and not the key-existence operators:

```sql
CREATE INDEX big_events_pathops_idx ON big_events USING gin (payload jsonb_path_ops);

SELECT pg_size_pretty(pg_relation_size('big_events_gin_idx'))     AS default_gin,
       pg_size_pretty(pg_relation_size('big_events_pathops_idx')) AS path_ops;
```

```result
 default_gin | path_ops
-------------+----------
 3048 kB     | 1904 kB
(1 row)
```

On this data the `jsonb_path_ops` index is about 60% of the default one. How much smaller it is depends on the data; do not count on a fixed ratio. To see the limitation, drop the default index and ask for key existence:

```sql
DROP INDEX big_events_gin_idx;

EXPLAIN (COSTS OFF)
SELECT count(*) FROM big_events WHERE payload ? 'order_id';
```

```result
                        QUERY PLAN
----------------------------------------------------------
 Finalize Aggregate
   ->  Gather
         Workers Planned: 2
         ->  Partial Aggregate
               ->  Parallel Seq Scan on big_events
                     Filter: (payload ? 'order_id'::text)
(6 rows)
```

With only the `jsonb_path_ops` index, `?` is a sequential scan. Containment still uses it:

```sql
EXPLAIN (COSTS OFF)
SELECT count(*) FROM big_events WHERE payload @> '{"tags": ["beta"]}';
```

```result
                             QUERY PLAN
--------------------------------------------------------------------
 Aggregate
   ->  Bitmap Heap Scan on big_events
         Recheck Cond: (payload @> '{"tags": ["beta"]}'::jsonb)
         ->  Bitmap Index Scan on big_events_pathops_idx
               Index Cond: (payload @> '{"tags": ["beta"]}'::jsonb)
(5 rows)
```

So: use the default operator class if you need `?`, `?|` or `?&`, and `jsonb_path_ops` if you only ever use containment and want a smaller, faster index.

### Generated column: promote a field

If you read or filter a field all the time, expose it as a stored generated column. It then behaves like any column, with its own statistics, index and type:

```sql
ALTER TABLE big_events
    ADD COLUMN url TEXT GENERATED ALWAYS AS (payload ->> 'url') STORED;

CREATE INDEX big_events_url_col_idx ON big_events (url);
ANALYZE big_events;

SELECT count(*) FROM big_events WHERE url = '/products/p42';
```

```result
 count
-------
   200
(1 row)
```

The column costs storage for a second copy of the value and is recomputed when the row is written. Queries then write `url = ...` instead of repeating the expression.

## Turning arrays into rows

`jsonb_array_elements` expands a JSON array into one row per element, which is the way to join or aggregate over array contents:

```sql
SELECT e.event_id,
       (item ->> 'product_id')::int AS product_id,
       (item ->> 'qty')::int        AS qty
FROM events e,
     jsonb_array_elements(e.payload -> 'items') AS item
WHERE e.event_type = 'purchase'
ORDER BY product_id;
```

```result
 event_id | product_id | qty
----------+------------+-----
        4 |          7 |   2
        4 |         42 |   1
(2 rows)
```

Events without an `items` array produce no rows here, since `jsonb_array_elements(NULL)` is empty.

## jsonb_agg

`jsonb_agg` collects the values of a group into one JSON array, which is handy for building an API response in the query:

```sql
SELECT user_id,
       jsonb_agg(jsonb_build_object('type', event_type, 'at', occurred_at)
                 ORDER BY occurred_at DESC) AS events
FROM events
GROUP BY user_id
ORDER BY user_id;
```

```result
 user_id |                                                   events
---------+------------------------------------------------------------------------------------------------------------
     101 | [{"at": "2024-06-10T09:16:45", "type": "add_to_cart"}, {"at": "2024-06-10T09:15:22", "type": "page_view"}]
     102 | [{"at": "2024-06-10T09:17:01", "type": "page_view"}]
     103 | [{"at": "2024-06-10T09:20:12", "type": "purchase"}]
(3 rows)
```

The aggregate builds each group's array fully before it returns it, and the result is a single value per group. For users with a handful of events that is fine. For a user with a million events it is one very large value, and aggregation gives no way to stop early: `LIMIT` applies to the output rows, not to the rows going into `jsonb_agg`. If you only need the latest few events per user, restrict the input before aggregating. With a window function:

```sql
SELECT user_id,
       jsonb_agg(jsonb_build_object('type', event_type, 'at', occurred_at)
                 ORDER BY occurred_at DESC) AS latest_event
FROM (SELECT user_id, event_type, occurred_at,
             row_number() OVER (PARTITION BY user_id ORDER BY occurred_at DESC) AS rn
      FROM events) t
WHERE rn <= 1
GROUP BY user_id
ORDER BY user_id;
```

```result
 user_id |                      latest_event
---------+--------------------------------------------------------
     101 | [{"at": "2024-06-10T09:16:45", "type": "add_to_cart"}]
     102 | [{"at": "2024-06-10T09:17:01", "type": "page_view"}]
     103 | [{"at": "2024-06-10T09:20:12", "type": "purchase"}]
(3 rows)
```

The window version still numbers every event of every user. When there is an index on `(user_id, occurred_at)` and a list of users, a correlated subquery with `LIMIT` reads only the rows it keeps:

```sql
CREATE INDEX events_user_time_idx ON events (user_id, occurred_at);

SELECT u.user_id,
       (SELECT jsonb_agg(jsonb_build_object('type', e.event_type, 'at', e.occurred_at)
                         ORDER BY e.occurred_at DESC)
        FROM (SELECT event_type, occurred_at
              FROM events
              WHERE user_id = u.user_id
              ORDER BY occurred_at DESC
              LIMIT 1) e) AS latest_event
FROM (SELECT DISTINCT user_id FROM events) u
ORDER BY u.user_id;
```

```result
 user_id |                      latest_event
---------+--------------------------------------------------------
     101 | [{"at": "2024-06-10T09:16:45", "type": "add_to_cart"}]
     102 | [{"at": "2024-06-10T09:17:01", "type": "page_view"}]
     103 | [{"at": "2024-06-10T09:20:12", "type": "purchase"}]
(3 rows)
```

If the client is going to loop over the events anyway, return plain rows and group them there, so no large intermediate JSON value has to be built and parsed.

## Updating documents

`jsonb` has no in-place update. Changing one key writes a new version of the whole row, including the whole document, the way any `UPDATE` does. The functions below build the new document:

```sql
SELECT 'add key'       AS operation, payload -> 'device' || '{"rooted": false}'::jsonb AS result
FROM events WHERE event_id = 4
UNION ALL
SELECT 'remove key',    (payload -> 'device') - 'os' FROM events WHERE event_id = 4
UNION ALL
SELECT 'set nested',    jsonb_set(payload, '{device,type}', '"tablet"') -> 'device' FROM events WHERE event_id = 4
UNION ALL
SELECT 'remove nested', (payload #- '{device,type}') -> 'device' FROM events WHERE event_id = 4;
```

```result
   operation   |                        result
---------------+------------------------------------------------------
 add key       | {"os": "Android", "type": "mobile", "rooted": false}
 remove key    | {"type": "mobile"}
 set nested    | {"os": "Android", "type": "tablet"}
 remove nested | {"os": "Android"}
(4 rows)
```

Each expression can be used on the right of `UPDATE ... SET payload = ...`. A few points to know:

`||` merges objects at the top level only. If both sides have a `device` key, the right-hand one replaces the whole `device` object, not just the keys it mentions:

```sql
SELECT '{"device": {"os": "iOS", "type": "mobile"}}'::jsonb || '{"device": {"os": "Android"}}'::jsonb AS merged;
```

```result
            merged
-------------------------------
 {"device": {"os": "Android"}}
(1 row)
```

`type` is gone. To change one nested value, use `jsonb_set`, or subscript assignment in PostgreSQL 14 and later:

```sql
UPDATE events
SET payload['device']['os'] = '"iPadOS"'
WHERE event_id = 1
RETURNING event_id, payload -> 'device' AS device;
```

```result
 event_id |                        device
----------+-------------------------------------------------------
        1 | {"os": "iPadOS", "type": "mobile", "version": "17.2"}
(1 row)
```

`jsonb_set` creates the last key of a path if it is missing, but not the objects on the way: with `{x,type}` on a document that has no `x`, it returns the document unchanged.

Because every change rewrites the row, a document that is tens of kilobytes large and has one counter updated constantly is a poor fit for `jsonb`. Keep such a field in its own column.

## When a column is better

`jsonb` is a good fit for payloads whose shape varies by record, for stored API responses and for audit data that is written often and rarely queried by field. It is a poor fit for:

- fields that are in every query. Put them in columns, or promote them with a generated column as above.
- values that must reference other tables. A foreign key cannot point inside a document.
- constraints. `NOT NULL`, `CHECK` and typed columns do not reach inside the document unless you write a `CHECK` on an expression yourself.
- statistics. For a `jsonb` column the planner has no per-key statistics unless you index the expression, so its row estimates for conditions like `payload @> ...` are rough guesses.
- fields updated frequently inside a large document, for the reason above.

A common compromise is real columns for what you filter and join on, and `jsonb` for the rest.

## Other databases

- MySQL has a `JSON` type stored in a binary form, with `->` and `->>` operators that take a path (`payload->>'$.device.type'`), `JSON_CONTAINS`, `JSON_SET` and `JSON_ARRAYAGG`. It indexes JSON values through generated columns or multi-valued indexes, not GIN.
- SQL Server stores JSON as text in an `NVARCHAR` column and reads it with `JSON_VALUE` and `JSON_QUERY`, and changes it with `JSON_MODIFY`. `ISJSON` in a `CHECK` constraint validates it. Indexing goes through computed columns.

## In short

- Use `jsonb`; use `json` only when the exact original text matters.
- `->` returns `jsonb`, `->>` returns text. Compare with strings using `->>`, and cast to `numeric` or `int` before doing arithmetic.
- `@>` finds containment, including inside arrays and nested objects. `?` checks only top-level keys; use `@?` with a path for nested ones.
- A GIN index serves `@>` and the existence operators, `jsonb_path_ops` serves only containment and is smaller, and neither helps `->>`. For one known field, index the expression or promote it to a generated column.
- Aggregate after limiting the input, not before. Update with `jsonb_set` or subscripts, remember `||` is shallow, and expect every update to rewrite the row.
