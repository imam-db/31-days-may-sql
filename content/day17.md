---
day: 17
title: "String Parsing in SQL"
summary: "Pull domains, paths, query parameters, list items, log fields and version numbers out of text with PostgreSQL's string and regex functions, including the edge cases that break the obvious pattern."
teaser: "Domains, query parameters, log fields and versions out of text, and the edge cases."
---

The monitoring system logs every API request, and one column holds the full URL, for example `https://api.shop.com/v2/orders/12345?source=mobile&utm=email`. Product asks how many orders per day come from the mobile app. The answer is in that string: the path says it is an order, the `source` parameter says where from.

You can pull the rows into Python and parse them there, or you can do it where the data already is. SQL has enough string functions to handle most of this, and it keeps the parsing next to the data, so only the result leaves the database. The examples run on PostgreSQL 18; the last section lists the equivalents in other databases.

## The data

```sql
CREATE TABLE request_logs (
    log_id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    logged_at     TIMESTAMP NOT NULL,
    url           TEXT NOT NULL,
    user_agent    TEXT,
    response_time INT,
    status_code   INT
);

INSERT INTO request_logs (logged_at, url, user_agent, response_time, status_code) VALUES
('2024-06-10 09:15:22', 'https://api.shop.com/v2/orders/12345?source=mobile&utm=email',  'Mobile/iOS',     142, 200),
('2024-06-10 09:15:24', 'https://api.shop.com/v2/products/laptop-pro?ref=homepage',      'Chrome/120',      88, 200),
('2024-06-10 09:15:31', 'https://api.shop.com/v2/orders/12346?source=web&utm=organic',   'Chrome/120',     156, 201),
('2024-06-10 09:16:02', 'https://cdn.shop.com/images/products/laptop-pro.jpg',           'Mobile/Android',  23, 200),
('2024-06-10 09:16:15', 'https://api.shop.com/v2/users/me?include=orders,addresses',     'Mobile/iOS',      67, 200),
('2024-06-10 09:16:45', 'https://api.shop.com/v2/orders/12347?source=mobile&utm=push',   'Mobile/Android', 198, 200),
('2024-06-11 08:02:10', 'https://api.shop.com/v2/orders/12348?resource=app&source=mobile', 'Mobile/iOS',    120, 200);
```

The last row has a parameter called `resource`. It looks irrelevant now and will matter in a moment.

## The functions you will use most

| Function | What it does | Example |
| --- | --- | --- |
| `split_part(str, delim, n)` | n-th piece, counting from 1; a negative n counts from the end (PostgreSQL 14+) | `split_part('a/b/c', '/', 2)` is `b` |
| `position(sub IN str)` | where a substring starts, 0 if absent | `position('@' IN 'a@b.com')` is `2` |
| `substring(str FROM pattern)` | first match of a regex, or its first capture group | `substring('id=42' FROM '\d+')` is `42` |
| `regexp_match(str, pattern)` | array of the capture groups of the first match, `NULL` if none | `regexp_match('id=42', 'id=(\d+)')` is `{42}` |
| `regexp_replace(str, pattern, repl, flags)` | replace matches; the `g` flag replaces all of them | `regexp_replace('a1b', '\d', '', 'g')` is `ab` |
| `string_to_array(str, delim)` | split into an array | `string_to_array('a,b', ',')` is `{a,b}` |

`split_part` covers most fixed-format cases. Reach for a regex when the position of a piece depends on what surrounds it.

## Domain and path

The domain sits between `://` and the next `/`, so two `split_part` calls are enough: the inner one drops the protocol, the outer one keeps what is before the first slash.

```sql
SELECT split_part(split_part(url, '://', 2), '/', 1) AS domain,
       count(*) AS requests
FROM request_logs
GROUP BY domain
ORDER BY requests DESC, domain;
```

```result
    domain    | requests
--------------+----------
 api.shop.com |        6
 cdn.shop.com |        1
(2 rows)
```

The path is everything from the first `/` after the domain up to `?`. A regex says that more directly than a chain of splits:

```sql
SELECT url,
       (regexp_match(url, '://[^/]+(/[^?#]*)'))[1] AS path
FROM request_logs
ORDER BY log_id;
```

```result
                               url                               |              path
-----------------------------------------------------------------+---------------------------------
 https://api.shop.com/v2/orders/12345?source=mobile&utm=email    | /v2/orders/12345
 https://api.shop.com/v2/products/laptop-pro?ref=homepage        | /v2/products/laptop-pro
 https://api.shop.com/v2/orders/12346?source=web&utm=organic     | /v2/orders/12346
 https://cdn.shop.com/images/products/laptop-pro.jpg             | /images/products/laptop-pro.jpg
 https://api.shop.com/v2/users/me?include=orders,addresses       | /v2/users/me
 https://api.shop.com/v2/orders/12347?source=mobile&utm=push     | /v2/orders/12347
 https://api.shop.com/v2/orders/12348?resource=app&source=mobile | /v2/orders/12348
(7 rows)
```

`regexp_match` returns an array with one element per capture group, and `[1]` takes the first. `[^?#]*` stops at a query string or a fragment, whichever comes first.

Once you have the path, `split_part` with a negative position gives the last segment, which is the file name for CDN requests:

```sql
SELECT split_part((regexp_match(url, '://[^/]+(/[^?#]*)'))[1], '/', -1) AS last_segment
FROM request_logs
WHERE url LIKE 'https://cdn.%';
```

```result
  last_segment
----------------
 laptop-pro.jpg
(1 row)
```

## Query parameters

To read `source`, capture everything after `source=` up to the next `&`. The obvious pattern is `source=([^&]+)`. Here is what it does on the row with the `resource` parameter:

```sql
SELECT url,
       (regexp_match(url, 'source=([^&]+)'))[1] AS naive,
       (regexp_match(url, '[?&]source=([^&#]*)'))[1] AS anchored
FROM request_logs
WHERE log_id = 7;
```

```result
                               url                               | naive | anchored
-----------------------------------------------------------------+-------+----------
 https://api.shop.com/v2/orders/12348?resource=app&source=mobile | app   | mobile
(1 row)
```

The text `source=` also occurs inside `resource=`, and `regexp_match` returns the first match, so the naive pattern reports `app`. Requiring the parameter name to follow a `?` or `&` fixes it. `[^&#]*` also stops at a `#fragment`.

The business question, orders per day and source, with the anchored pattern:

```sql
SELECT logged_at::date AS day,
       (regexp_match(url, '[?&]source=([^&#]*)'))[1] AS source,
       count(*) AS orders
FROM request_logs
WHERE url ~ '/orders/\d+'
GROUP BY 1, 2
ORDER BY 1, 2;
```

```result
    day     | source | orders
------------+--------+--------
 2024-06-10 | mobile |      2
 2024-06-10 | web    |      1
 2024-06-11 | mobile |      1
(3 rows)
```

`~` is PostgreSQL's regex match operator. The `WHERE` clause keeps only order URLs, so `/users/me?include=orders,addresses` is not counted. Extracting the order id itself is the same idea, with a cast:

```sql
SELECT (regexp_match(url, '/orders/(\d+)'))[1]::INT AS order_id
FROM request_logs
WHERE url ~ '/orders/\d+'
ORDER BY order_id;
```

```result
 order_id
----------
    12345
    12346
    12347
    12348
(4 rows)
```

Two limits of doing this with regexes. Values in a URL can be percent-encoded (`laptop%20pro`), and PostgreSQL has no built-in decoder for that. And a hand-written pattern knows nothing about the full URL grammar. For a handful of known parameters it is fine; for arbitrary URLs, parse them once in the application when you ingest them.

### Parse once, not on every query

A regex in a `WHERE` or `GROUP BY` runs on every row of every query and cannot use an ordinary index. If you filter on `source` often, store it as a generated column and index that:

```sql
ALTER TABLE request_logs
    ADD COLUMN source TEXT
    GENERATED ALWAYS AS ((regexp_match(url, '[?&]source=([^&#]*)'))[1]) STORED;

CREATE INDEX request_logs_source_idx ON request_logs (source);

SELECT log_id, source FROM request_logs WHERE source IS NOT NULL ORDER BY log_id;
```

```result
 log_id | source
--------+--------
      1 | mobile
      3 | web
      6 | mobile
      7 | mobile
(4 rows)
```

PostgreSQL computes the value when the row is written. `regexp_match` is immutable, which is what a generated column requires.

## Splitting a list stored in one column

Sometimes several values are packed into one column. `string_to_array` splits the text into an array and `unnest` turns each array element into a row:

```sql
CREATE TABLE user_interests (
    user_id   INT PRIMARY KEY,
    username  VARCHAR(50),
    interests TEXT
);

INSERT INTO user_interests VALUES
    (1, 'alice',   'tech,music,sports'),
    (2, 'bob',     'cooking,gardening'),
    (3, 'charlie', 'tech,gaming,music'),
    (4, 'diana',   'travel,photography,cooking,music');

SELECT user_id, username, unnest(string_to_array(interests, ',')) AS interest
FROM user_interests
ORDER BY user_id, interest;
```

```result
 user_id | username |  interest
---------+----------+-------------
       1 | alice    | music
       1 | alice    | sports
       1 | alice    | tech
       2 | bob      | cooking
       2 | bob      | gardening
       3 | charlie  | gaming
       3 | charlie  | music
       3 | charlie  | tech
       4 | diana    | cooking
       4 | diana    | music
       4 | diana    | photography
       4 | diana    | travel
(12 rows)
```

With the interests as rows, counting how many users share each one is a plain `GROUP BY`. Since PostgreSQL 14 there is also `string_to_table`, which does the split and the unnest in one call (`FROM user_interests, string_to_table(interests, ',') AS interest`).

Two edge cases to know about. An empty string becomes an empty array, so that user produces no rows at all instead of one row with an empty value. And the split does not trim: `'a, b'` gives `a` and ` b` with a leading space.

```sql
SELECT string_to_array('', ',') AS empty_input,
       string_to_array('a, b,,c', ',') AS spaces_and_gaps,
       string_to_array(NULL, ',') IS NULL AS null_input;
```

```result
 empty_input | spaces_and_gaps | null_input
-------------+-----------------+------------
 {}          | {a," b","",c}   | t
(1 row)
```

Apply `trim` to each element if the data has spaces after the commas. Two adjacent delimiters produce an empty string element (`""` in the array output above).

## Parsing log lines

Lines like `user=alice action=login ip=192.168.1.10` are key-value pairs in free text. Anchor each key with `\m` (the start of a word in PostgreSQL regexes) so that a key such as `ip=` cannot match inside the tail of a longer key:

```sql
CREATE TABLE raw_logs (
    log_id  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    line    TEXT NOT NULL
);

INSERT INTO raw_logs (line) VALUES
('2024-06-10 09:15:22 [INFO]  user=alice action=login ip=192.168.1.10'),
('2024-06-10 09:15:24 [WARN]  user=bob action=failed_login ip=192.168.1.11 reason=bad_password'),
('2024-06-10 09:15:31 [ERROR] user=charlie action=db_query duration=2400'),
('2024-06-10 09:15:45 [INFO]  user=diana action=logout ip=192.168.1.12');

SELECT substring(line FROM 1 FOR 19)                    AS log_time,
       (regexp_match(line, '\[(\w+)\]'))[1]             AS log_level,
       (regexp_match(line, '\muser=(\w+)'))[1]          AS username,
       (regexp_match(line, '\maction=(\w+)'))[1]        AS action,
       (regexp_match(line, '\mip=([\d.]+)'))[1]         AS ip_address,
       (regexp_match(line, '\mduration=(\d+)'))[1]::INT AS duration_ms
FROM raw_logs
ORDER BY log_id;
```

```result
      log_time       | log_level | username |    action    |  ip_address  | duration_ms
---------------------+-----------+----------+--------------+--------------+-------------
 2024-06-10 09:15:22 | INFO      | alice    | login        | 192.168.1.10 |
 2024-06-10 09:15:24 | WARN      | bob      | failed_login | 192.168.1.11 |
 2024-06-10 09:15:31 | ERROR     | charlie  | db_query     |              |        2400
 2024-06-10 09:15:45 | INFO      | diana    | logout       | 192.168.1.12 |
(4 rows)
```

Charlie's line has no `ip=`, so `regexp_match` returns `NULL`, and casting a `NULL` gives `NULL`. A missing optional field becomes a `NULL` instead of an error, and a field that appears on only some lines (`duration`, `reason`) needs no special handling.

`\m` is PostgreSQL's own syntax. Perl-style engines, which most other databases use, write the word boundary as `\b`.

## Version numbers

Version strings sort wrongly as text, because `'2.10.0'` is smaller than `'2.9.7'` character by character. The numeric parts have to be compared as numbers.

```sql
CREATE TABLE deployments (
    deploy_id   INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    service     VARCHAR(50) NOT NULL,
    version     VARCHAR(30) NOT NULL,
    deployed_at TIMESTAMP NOT NULL
);

INSERT INTO deployments (service, version, deployed_at) VALUES
    ('api',      '2.1.4',         '2024-05-01 10:00'),
    ('api',      '2.10.0',        '2024-05-15 14:30'),
    ('api',      '2.9.7',         '2024-05-10 09:15'),
    ('api',      '3.0.0-beta.2',  '2024-06-01 16:00'),
    ('frontend', '1.5.2',         '2024-05-20 11:00'),
    ('frontend', '1.6.0-rc.1',    '2024-05-25 14:00'),
    ('frontend', '1.6.0',         '2024-06-05 09:30');

SELECT version AS by_text FROM deployments WHERE service = 'api' ORDER BY version;
```

```result
   by_text
--------------
 2.1.4
 2.10.0
 2.9.7
 3.0.0-beta.2
(4 rows)
```

Splitting the core part (before any `-`) into an `INT[]` gives a sort key, because PostgreSQL compares arrays element by element. The suffix after the first `-` is the prerelease tag, and a version without one is a stable release, which sorts after its prereleases:

```sql
SELECT service, version,
       string_to_array(split_part(version, '-', 1), '.')::INT[] AS core,
       substring(version FROM '-(.*)$')                         AS prerelease
FROM deployments
ORDER BY service,
         string_to_array(split_part(version, '-', 1), '.')::INT[],
         version NOT LIKE '%-%',        -- false (prerelease) sorts before true (stable)
         version;
```

```result
 service  |   version    |   core   | prerelease
----------+--------------+----------+------------
 api      | 2.1.4        | {2,1,4}  |
 api      | 2.9.7        | {2,9,7}  |
 api      | 2.10.0       | {2,10,0} |
 api      | 3.0.0-beta.2 | {3,0,0}  | beta.2
 frontend | 1.5.2        | {1,5,2}  |
 frontend | 1.6.0-rc.1   | {1,6,0}  | rc.1
 frontend | 1.6.0        | {1,6,0}  |
(7 rows)
```

`2.10.0` now follows `2.9.7`, `3.0.0-beta.2` comes last for the API, and `1.6.0-rc.1` precedes `1.6.0`. Take the prerelease tag with `substring(... FROM '-(.*)$')` rather than `split_part(version, '-', 2)`, because the latter cuts a tag like `rc-1` down to `rc`.

This is a good sort for the common cases, not a complete implementation of the Semantic Versioning rules. Among prereleases the final `version` tie-breaker compares text, so `beta.10` would sort before `beta.2`, and build metadata after `+` is not handled. If you need the full rules, store the parsed parts in separate columns when you ingest the data.

## Cleaning inconsistent text

```sql
CREATE TABLE messy_contacts (
    contact_id  INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name        VARCHAR(100),
    email       VARCHAR(100),
    phone       VARCHAR(50)
);

INSERT INTO messy_contacts (name, email, phone) VALUES
    ('  alice WONG  ',  ' Alice@Shop.COM ',    '(555) 123-4567'),
    ('Bob Smith',       'bob.smith@shop.com',  '+1-555-234-5678'),
    ('charlie BROWN',   'CHARLIE@SHOP.COM',    '555.345.6789'),
    (NULL,              'diana@shop.com',      '15554567890');

SELECT contact_id,
       initcap(trim(name))                                    AS clean_name,
       lower(trim(email))                                     AS clean_email,
       right(regexp_replace(phone, '\D', '', 'g'), 10)        AS clean_phone
FROM messy_contacts
ORDER BY contact_id;
```

```result
 contact_id |  clean_name   |    clean_email     | clean_phone
------------+---------------+--------------------+-------------
          1 | Alice Wong    | alice@shop.com     | 5551234567
          2 | Bob Smith     | bob.smith@shop.com | 5552345678
          3 | Charlie Brown | charlie@shop.com   | 5553456789
          4 |               | diana@shop.com     | 5554567890
(4 rows)
```

`regexp_replace(phone, '\D', '', 'g')` deletes every non-digit, and `right(..., 10)` then drops a leading country code. That second step assumes all numbers are 10-digit North American numbers, so check it against your own data first. `initcap` capitalizes the first letter after every non-letter and lowercases the rest, so it turns `McDonald` into `Mcdonald`. Treat it as a first pass for names, not a final answer. A `NULL` name stays `NULL` through every function.

## Building strings

The reverse direction is also common: assembling text from rows. `string_agg` joins the values of a group, and with `ORDER BY` inside the call the order is under your control:

```sql
CREATE TABLE user_events (
    user_id     INT,
    action      VARCHAR(50),
    occurred_at TIMESTAMP
);

INSERT INTO user_events VALUES
    (1, 'landing',        '2024-06-10 10:00'),
    (1, 'signup',         '2024-06-10 10:05'),
    (1, 'email_verify',   '2024-06-10 10:10'),
    (1, 'first_purchase', '2024-06-10 10:30'),
    (2, 'landing',        '2024-06-10 11:00'),
    (2, 'product_view',   '2024-06-10 11:02'),
    (2, 'cart',           '2024-06-10 11:05'),
    (2, 'abandon',        '2024-06-10 11:15'),
    (3, 'landing',        '2024-06-10 12:00'),
    (3, 'search',         '2024-06-10 12:05'),
    (3, 'product_view',   '2024-06-10 12:10'),
    (3, 'cart',           '2024-06-10 12:15'),
    (3, 'checkout',       '2024-06-10 12:20');

SELECT user_id,
       string_agg(action, ' -> ' ORDER BY occurred_at) AS user_journey
FROM user_events
GROUP BY user_id
ORDER BY user_id;
```

```result
 user_id |                     user_journey
---------+-------------------------------------------------------
       1 | landing -> signup -> email_verify -> first_purchase
       2 | landing -> product_view -> cart -> abandon
       3 | landing -> search -> product_view -> cart -> checkout
(3 rows)
```

For a URL built from columns, `concat_ws` joins with a separator and skips `NULL` arguments, where `||` turns the whole result `NULL` as soon as one part is `NULL`:

```sql
SELECT 'https://api.shop.com/v2/users/' || 101 || '/orders?status=' || 'active'           AS with_pipes,
       'https://api.shop.com/v2/users/' || 101 || '/orders?status=' || NULL::TEXT         AS pipes_with_null,
       concat_ws('&', 'status=active', NULL, 'from=' || to_char(DATE '2024-06-01', 'YYYY-MM-DD')) AS query_string;
```

```result
                       with_pipes                       | pipes_with_null |         query_string
--------------------------------------------------------+-----------------+-------------------------------
 https://api.shop.com/v2/users/101/orders?status=active |                 | status=active&from=2024-06-01
(1 row)
```

## Equivalents in other databases

Names differ. These are the usual ones for the functions above:

| Task | PostgreSQL | MySQL 8 | SQL Server |
| --- | --- | --- | --- |
| Concatenate with a separator | `concat_ws()` | `CONCAT_WS()` | `CONCAT_WS()` (2017+) |
| n-th piece of a delimited string | `split_part()` | `SUBSTRING_INDEX()`, nested twice | no direct function; `STRING_SPLIT()` returns rows |
| Position of a substring | `position()` | `LOCATE()` | `CHARINDEX()` |
| Regex replace | `regexp_replace()` | `REGEXP_REPLACE()` | check your version's documentation |
| String to rows | `unnest(string_to_array())`, `string_to_table()` | recursive CTE or `JSON_TABLE` | `STRING_SPLIT()` |

In MySQL the n-th piece is `SUBSTRING_INDEX(SUBSTRING_INDEX(str, delim, n), delim, -1)`. Also in MySQL, `||` is a logical OR unless the `PIPES_AS_CONCAT` mode is on, so use `CONCAT()` there.

## In short

- Use `split_part` for fixed-format pieces and `regexp_match` when context decides where a piece starts.
- Anchor a regex on the delimiter before the name (`[?&]source=`), or `source=` will match inside `resource=`.
- Parse once into a generated column if you filter on the value often.
- `string_to_array` plus `unnest` turns a packed list into rows; empty strings produce no rows and nothing is trimmed.
- Percent-encoded URLs and the full version-numbering rules are better handled in the application at ingestion time.
