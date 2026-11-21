---
day: 30
title: "Write Skew and Phantom Reads: Isolation Levels You're Getting Wrong"
summary: "Two doctors go off call at the same moment and the hospital is left with nobody. Write skew, lost updates and phantoms reproduced with two real sessions on PostgreSQL 18, and what REPEATABLE READ, SERIALIZABLE and explicit locks do about each."
teaser: "Two doctors go off call at once. Write skew reproduced with two real sessions."
---

A hospital has one rule: at least one doctor must be on call. Alice and Bob are both on call. Each opens the app and sees that the other is on call, so each is allowed to go off call. Both transactions check the rule, both pass, both update their own row, both commit. Nobody is on call.

Neither transaction did anything wrong against the data it saw. This anomaly is called write skew, and it survives `REPEATABLE READ` in PostgreSQL. This article reproduces it with two real sessions, then goes through the other anomalies the isolation levels are defined by, and finishes with the three ways of closing the gap: `SERIALIZABLE`, row locks, and advisory locks.

All sessions below ran against PostgreSQL 18. In the two-session examples, the code lists the statements in the order they ran, with `-- A` and `-- B` marking the session, and the block after it shows the output that was observed.

## The tables

```sql
CREATE TABLE doctors (
    doctor_id  INT PRIMARY KEY,
    name       TEXT NOT NULL,
    on_call    BOOLEAN NOT NULL
);

INSERT INTO doctors VALUES (1, 'Alice', true), (2, 'Bob', true);

CREATE TABLE employees (
    employee_id  INT PRIMARY KEY,
    name         TEXT NOT NULL,
    department   TEXT NOT NULL
);

INSERT INTO employees VALUES (1, 'Ann', 'Eng'), (2, 'Bo', 'Eng'), (3, 'Cy', 'Ops');

CREATE TABLE accounts (
    account_id  INT PRIMARY KEY,
    balance     INT NOT NULL
);

INSERT INTO accounts VALUES (1, 100);
```

## The four levels and what PostgreSQL does with them

The SQL standard defines four isolation levels by which anomalies they allow. PostgreSQL implements three distinct behaviours, and its `REPEATABLE READ` is stricter than the standard asks:

| Level | Dirty read | Non-repeatable read | Phantom read | Write skew |
| --- | --- | --- | --- | --- |
| `READ UNCOMMITTED` (PostgreSQL runs it as `READ COMMITTED`) | not possible | possible | possible | possible |
| `READ COMMITTED` (PostgreSQL default) | not possible | possible | possible | possible |
| `REPEATABLE READ` (PostgreSQL, snapshot isolation) | not possible | not possible | not possible | possible |
| `SERIALIZABLE` (PostgreSQL, SSI) | not possible | not possible | not possible | not possible |

In the standard, `REPEATABLE READ` still allows phantoms, and write skew is not in the standard's list of named anomalies at all; it is what is left when a level that is "good enough" by the standard's definitions still does not behave like running the transactions one after another. The default can be checked:

```sql
SHOW default_transaction_isolation;
```

```result
 default_transaction_isolation
-------------------------------
 read committed
(1 row)
```

Each of the next sections uses two sessions. To make the interleaving exact and repeatable, session A held its transaction open and ran session B as a separate `psql` process from inside it (`\!` in `psql`). The statements therefore ran in the order shown.

## Dirty read

A dirty read means seeing another transaction's uncommitted change. PostgreSQL does not allow it at any level. Even `READ UNCOMMITTED` is accepted as a name and behaves like `READ COMMITTED`:

```sql norun
-- A
BEGIN ISOLATION LEVEL READ UNCOMMITTED;
UPDATE accounts SET balance = 0 WHERE account_id = 1;
-- B (a separate session, while A is still open)
BEGIN ISOLATION LEVEL READ UNCOMMITTED;
SELECT balance FROM accounts WHERE account_id = 1;
-- A
ROLLBACK;
```

```text
 balance
---------
     100
(1 row)
```

B sees 100, the committed value, not A's uncommitted 0. `SHOW transaction_isolation` inside such a transaction reports `read uncommitted`, so the name is kept, but the behaviour is that of `READ COMMITTED`.

## Non-repeatable read and phantom read

Under `READ COMMITTED`, every statement gets a fresh snapshot. A transaction that runs the same query twice can get different answers if someone commits in between. Here A counts the engineers and reads an account balance twice, and B commits an insert and an update between the two reads:

```sql norun
-- A
BEGIN ISOLATION LEVEL READ COMMITTED;
SELECT count(*) FROM employees WHERE department = 'Eng';   -- 2
SELECT balance FROM accounts WHERE account_id = 1;         -- 100
-- B (autocommit, so both statements commit immediately)
INSERT INTO employees VALUES (4, 'Dee', 'Eng');
UPDATE accounts SET balance = 200 WHERE account_id = 1;
-- A
SELECT count(*) FROM employees WHERE department = 'Eng';
SELECT balance FROM accounts WHERE account_id = 1;
COMMIT;
```

```text
 count
-------
     3

 balance
---------
     200
```

The changed balance is a non-repeatable read, and the extra engineer, Dee, is a phantom: a new row that matches a predicate the transaction already queried. Run the same script with `BEGIN ISOLATION LEVEL REPEATABLE READ` and the second reads return `2` and `100`. A keeps the snapshot it took at its first statement for the whole transaction, so B's commits are invisible to it.

That second result is the PostgreSQL-specific part. The standard allows phantoms at `REPEATABLE READ`; PostgreSQL's snapshot makes them impossible. In MySQL InnoDB, which also defaults to `REPEATABLE READ`, plain `SELECT`s read from a snapshot too, but locking statements (`SELECT ... FOR UPDATE`, `UPDATE`, `DELETE`) see the latest committed rows and use gap locks to keep phantoms out of the range they lock. So the level name is the same and what a transaction can observe is not, which is the usual trap when moving code between the two. Another difference is the default level itself: MySQL starts at `REPEATABLE READ`, PostgreSQL at `READ COMMITTED`.

## Lost update

Before write skew, a simpler problem: two transactions change the same row. An account holds 100. Two withdrawals of 80 each should leave -60, or one of them should be refused. If the application reads the balance, computes the new value and writes it back, the second write overwrites the first:

```sql norun
-- A
BEGIN ISOLATION LEVEL READ COMMITTED;
SELECT balance FROM accounts WHERE account_id = 1;         -- 100
-- B (autocommit)
UPDATE accounts SET balance = balance - 80 WHERE account_id = 1;
-- A: new value computed in the application from the 100 it read
UPDATE accounts SET balance = 100 - 80 WHERE account_id = 1;
COMMIT;
SELECT balance FROM accounts WHERE account_id = 1;
```

```text
 balance
---------
      20
(1 row)
```

Two withdrawals of 80 and the balance fell by 80. B's withdrawal vanished. At `REPEATABLE READ` the same script behaves differently: A's `UPDATE` fails with

```text
ERROR:  could not serialize access due to concurrent update
```

because A's snapshot is older than B's committed change to that very row, and A's transaction is rolled back. Lost updates are therefore caught by PostgreSQL's `REPEATABLE READ` and `SERIALIZABLE`, and the application must retry. The cheaper fix is to let the database do the arithmetic (`SET balance = balance - 80 WHERE balance >= 80`), which is safe at `READ COMMITTED`.

## Write skew

Write skew needs two transactions that read the same data and write different rows. A lost-update check cannot see a conflict, because no row is written twice. This is the on-call scenario, at `REPEATABLE READ`:

```sql norun
-- A (Alice)
BEGIN ISOLATION LEVEL REPEATABLE READ;
SELECT count(*) FROM doctors WHERE on_call;                -- 2
-- B (Bob)
BEGIN ISOLATION LEVEL REPEATABLE READ;
SELECT count(*) FROM doctors WHERE on_call;                -- 2
UPDATE doctors SET on_call = false WHERE doctor_id = 2;
COMMIT;
-- A: still believes there are two
UPDATE doctors SET on_call = false WHERE doctor_id = 1;
COMMIT;
SELECT doctor_id, name, on_call FROM doctors ORDER BY doctor_id;
```

```text
 doctor_id | name  | on_call
-----------+-------+---------
         1 | Alice | f
         2 | Bob   | f
(2 rows)
```

Both commits succeed and the table now violates the rule. Each transaction read a consistent snapshot and wrote a row nobody else wrote, so snapshot isolation has nothing to object to. The anomaly appears whenever a rule spans several rows (a minimum headcount, a total that must stay under a limit, a room that can be booked only once) and the check and the write are separate statements. An inventory counter on a single row is not write skew: two buyers of the last item both update that row, so it is the lost-update case above.

## SERIALIZABLE

At `SERIALIZABLE`, PostgreSQL uses Serializable Snapshot Isolation. It runs transactions on snapshots as before but also tracks which transaction read what another one wrote, and cancels one of them when the pattern could not have happened in any one-after-the-other order. The same interleaving as above, with both sessions at `SERIALIZABLE`:

```sql norun
-- A
BEGIN ISOLATION LEVEL SERIALIZABLE;
SELECT count(*) FROM doctors WHERE on_call;                -- 2
-- B
BEGIN ISOLATION LEVEL SERIALIZABLE;
SELECT count(*) FROM doctors WHERE on_call;                -- 2
UPDATE doctors SET on_call = false WHERE doctor_id = 2;
COMMIT;                                                    -- succeeds
-- A
UPDATE doctors SET on_call = false WHERE doctor_id = 1;
```

```text
ERROR:  40001: could not serialize access due to read/write dependencies among transactions
DETAIL:  Reason code: Canceled on identification as a pivot, during write.
HINT:  The transaction might succeed if retried.
```

After A rolls back, Alice is still on call and Bob is off. Here the error came at A's `UPDATE`; it can also come at `COMMIT`, and which of the two transactions is cancelled depends on the interleaving, so the code must be prepared to handle SQLSTATE `40001` anywhere in the transaction. A retry of A runs the check again, counts one doctor on call, and the application refuses the request.

The cost is real but specific:

- Aborts have to be retried, so every code path that runs at `SERIALIZABLE` needs a retry loop.
- PostgreSQL tracks reads with predicate locks, which it may coarsen from rows to pages to the whole relation when there are many of them. A sequential scan takes a relation-level one. Coarser locks mean more false-positive aborts, so queries on indexed access paths and short transactions abort less.
- It does not need extra blocking: transactions are not made to wait for each other, they are cancelled.

A retry loop looks like this. Retry only on `40001` (and `40P01`, deadlock), cap the attempts, and back off:

```python norun
import time
from psycopg import errors

def run_serializable(conn, work, attempts=5):
    for attempt in range(attempts):
        try:
            with conn.transaction():
                conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
                return work(conn)
        except (errors.SerializationFailure, errors.DeadlockDetected):
            if attempt == attempts - 1:
                raise
            time.sleep(0.01 * 2 ** attempt)
```

The `SET TRANSACTION` has to be the first statement inside the transaction. Putting it before `BEGIN`, as sometimes written, affects only the (nonexistent) current transaction and does nothing for the next one.

## Closing the gap without SERIALIZABLE

If the rest of the application runs at `READ COMMITTED`, one or two critical paths can serialize themselves explicitly.

### Lock the rows you decide from

`SELECT ... FOR UPDATE` takes row locks on the rows it returns. A second transaction doing the same blocks until the first commits, then re-checks its rows. The pattern at `READ COMMITTED`: lock every on-call doctor, then do the update conditionally. Session A starts first and sleeps for three seconds while holding its locks; session B starts one second later and has to wait:

```sql norun
-- both sessions run the same statements, with doctor_id 1 (A) and 2 (B)
BEGIN;
SELECT doctor_id FROM doctors WHERE on_call ORDER BY doctor_id FOR UPDATE;
UPDATE doctors SET on_call = false
WHERE doctor_id = 1                                -- 2 in session B
  AND (SELECT count(*) FROM doctors WHERE on_call) > 1;
COMMIT;
```

```text
-- session A
 doctor_id
-----------
         1
         2
(2 rows)
UPDATE 1

-- session B (blocked on the first statement until A committed)
 doctor_id
-----------
         2
(1 row)
UPDATE 0
```

A locked both rows and went off call. B was blocked at `FOR UPDATE`. Once A committed, PostgreSQL re-evaluated `WHERE on_call` against A's committed row, which no longer qualified, so B locked only its own row, the count was 1, and the conditional `UPDATE` changed nothing. The application sees `UPDATE 0` and reports that the last doctor cannot leave. This works because every code path that changes on-call status takes the same locks first. A path that skips the `FOR UPDATE` is not protected, and a rule that depends on rows that do not exist yet (a booking slot with no row) has nothing to lock.

### Advisory locks

When there is no row to lock, or the rule covers a whole resource, an advisory lock gives every participant the same lock to take:

```sql norun
BEGIN;
SELECT pg_advisory_xact_lock(42);   -- 42 is an application-chosen key for "the on-call roster"
-- check the rule and make the change
COMMIT;                             -- the lock is released here
```

The same two-session test with this lock at `READ COMMITTED` gave `UPDATE 0` for the second session, as above. At `REPEATABLE READ` it did not work: the second session's snapshot is taken when it runs the `pg_advisory_xact_lock` query, before it has waited, so after the wait it still sees two doctors on call and both go off. The pattern "lock, then read" needs a level where the read after the lock gets a fresh snapshot, which means `READ COMMITTED`.

### Version columns

Optimistic locking with a `version` column (`UPDATE ... WHERE id = 1 AND version = 5`) detects two writers of the same row. It is the application-level counterpart of the lost-update error and does not help against write skew unless every transaction also updates one shared row, such as a version on the roster. A version column on each doctor would not have stopped Alice and Bob.

## Choosing

- Single-row read-modify-write: let the `UPDATE` compute the new value, or use `REPEATABLE READ` with retries.
- A rule over several rows, with many code paths that could break it: `SERIALIZABLE` with retries, so no path can forget a lock.
- One or two known paths: `FOR UPDATE` on the rows involved, or an advisory lock, at `READ COMMITTED`.

For other databases, only the headline differences are safe to generalize. MySQL InnoDB defaults to `REPEATABLE READ` and behaves differently for locking statements as described above. SQL Server's default `READ COMMITTED` uses locks unless the database has snapshot-based read committed switched on, and its `SERIALIZABLE` uses range locks. Oracle's `SERIALIZABLE` is snapshot isolation, so write skew is possible there as well. Check the manual of the engine before assuming a level name means what it means in PostgreSQL.

## In short

- `READ COMMITTED` gives every statement a new snapshot, so reads can change within a transaction. `REPEATABLE READ` in PostgreSQL pins one snapshot, which removes non-repeatable reads and phantoms and catches concurrent updates of the same row with an error.
- Write skew is two transactions checking a multi-row rule and each writing a different row. Only `SERIALIZABLE` (or explicit locking) stops it.
- Retry on SQLSTATE `40001` at any level above `READ COMMITTED`.
- Explicit locks only protect code paths that take them.
