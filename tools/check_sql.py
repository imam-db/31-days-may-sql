"""Run every SQL example of a day and check every printed result against PostgreSQL.

Conventions in content/dayNN.md:
  ```sql         runs, in order, in a fresh database
  ```sql norun   shown only (fragments, other dialects); not executed
  ```result      must equal the psql output of each row-returning statement
                 in the sql block just before it (several statements -> each
                 must give this same output)

All statements run in one psql session, so SET, setseed() and transactions carry over.
Needs a PostgreSQL server: set PGHOST/PGPORT/PGUSER (default 127.0.0.1:54331, user sqlcheck).
Usage: python tools/check_sql.py <day-number> [--show]
"""
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PSQL = os.environ.get("PSQL", r"C:\Program Files\PostgreSQL\18\bin\psql.exe")
CONN = ["-h", os.environ.get("PGHOST", "127.0.0.1"), "-p", os.environ.get("PGPORT", "54331"),
        "-U", os.environ.get("PGUSER", "sqlcheck")]
FENCE = re.compile(r"^```([a-z]+)([^\n]*)\n(.*?)^```", re.S | re.M)


def psql(db, sql):
    r = subprocess.run([PSQL, *CONN, "-d", db, "-X", "-q", "-v", "ON_ERROR_STOP=1", "-P", "pager=off"],
                       input=sql.encode("utf-8"), capture_output=True)
    return r.returncode, r.stdout.decode("utf-8"), r.stderr.decode("utf-8")


def split_statements(sql):
    """Split on semicolons that end a line, ignoring those inside $$ ... $$ bodies."""
    parts, cur = [], []
    in_dollar = False
    for line in sql.splitlines():
        cur.append(line)
        if len(re.findall(r"\$[A-Za-z_]*\$", re.sub(r"--.*", "", line))) % 2 == 1:
            in_dollar = not in_dollar
        if not in_dollar and re.search(r";\s*(--.*)?$", line):
            stmt = "\n".join(cur).strip()
            if re.sub(r"--.*", "", stmt).strip():
                parts.append(stmt)
            cur = []
    rest = "\n".join(cur).strip()
    if re.sub(r"--.*", "", rest).strip():
        parts.append(rest)
    return parts


def norm(out):
    out = out.replace("\r\n", "\n").replace("\r", "\n")  # psql on Windows prints CRLF
    return "\n".join(line.rstrip() for line in out.strip("\n").splitlines())


def run_all(db, statements):
    """Run all statements in ONE psql session (so SET, setseed, BEGIN carry over),
    and return (output, error_or_None) per statement, split by echo markers."""
    script = []
    for i, stmt in enumerate(statements):
        stmt = stmt if stmt.rstrip().endswith(";") else stmt + ";"
        script += [f"\\echo @@BEGIN {i}@@", stmt,
                   "\\if :ERROR", "\\echo @@ERROR@@ :LAST_ERROR_MESSAGE", "\\endif",
                   f"\\echo @@END {i}@@"]
    r = subprocess.run([PSQL, *CONN, "-d", db, "-X", "-q", "-v", "ON_ERROR_STOP=0", "-P", "pager=off"],
                       input="\n".join(script).encode("utf-8"), capture_output=True)
    out = r.stdout.decode("utf-8").replace("\r\n", "\n")
    results = []
    for i in range(len(statements)):
        m = re.search(rf"^@@BEGIN {i}@@\n(.*?)^@@END {i}@@$", out, re.S | re.M)
        if not m:
            results.append(("", f"no output captured (psql stderr: {r.stderr.decode('utf-8')[-500:]})"))
            continue
        body = m.group(1)
        em = re.search(r"^@@ERROR@@ (.*)$", body, re.M)
        results.append((body[:em.start()] if em else body, em.group(1) if em else None))
    return results


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    nn = f"{int(sys.argv[1]):02d}"
    show = "--show" in sys.argv
    md = (ROOT / "content" / f"day{nn}.md").read_text(encoding="utf-8")
    db = f"check_day{nn}"
    psql("postgres", f"DROP DATABASE IF EXISTS {db};")
    psql("postgres", f"CREATE DATABASE {db};")
    fences = [(m.group(1), m.group(2).strip(), m.group(3)) for m in FENCE.finditer(md)]
    results = run_all(db, [s for lang, info, body in fences if lang == "sql" and "norun" not in info
                           for s in split_statements(body)])
    failures, ran, checked = 0, 0, 0
    last_outputs = []
    idx = 0
    for lang, info, body in fences:
        if lang == "sql" and "norun" not in info:
            last_outputs = []
            for stmt in split_statements(body):
                out, err = results[idx]
                idx += 1
                ran += 1
                if err is not None:
                    failures += 1
                    print(f"ERROR running:\n{stmt}\n{err}")
                    continue
                if out.strip():
                    last_outputs.append((stmt, out))
        elif lang == "result":
            expected = norm(body)
            if not last_outputs:
                failures += 1
                print(f"result block with no row-returning statement before it:\n{body}")
                continue
            for stmt, out in last_outputs:
                checked += 1
                if norm(out) != expected:
                    failures += 1
                    print(f"MISMATCH for:\n{stmt}\n--- printed in article\n{expected}\n--- PostgreSQL returned\n{norm(out)}\n")
                elif show:
                    print(f"ok: {stmt.splitlines()[0][:70]}")
    psql("postgres", f"DROP DATABASE IF EXISTS {db};")
    print(f"Day {nn}: {ran} statements run, {checked} printed results checked, {failures} problem(s)")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
