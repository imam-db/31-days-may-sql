# #31DaysMaySQL

One SQL problem a day for May 2026, 31 days in five themes. Every example runs on PostgreSQL 18 and every result shown is real psql output. Live at <https://31daymaysql.vvizh.com/>.

- `content/dayNN.md` — the source of each day (Markdown)
- `public/` — what gets deployed: `index.html`, `day/NN/` (one page per day), `days/` (one PDF per day), `day.css`
- `tools/` — `pdf2md.py` (one-off import from the original PDFs), `check_sql.py`, `lint_style.py`, `build.py`, `build_index.py`
- `docs/` — style guide, ADRs, workflow (in Indonesian)

## Build one day

```
python -I tools/check_sql.py 22   # needs a local PostgreSQL 18, see the docstring
python -I tools/lint_style.py 22
python -I tools/build.py 22       # web page + PDF (pandoc, Chrome)
python -I tools/build_index.py    # refresh the list on the home page
```

## Deploy

Cloudflare Workers Builds, configured in `wrangler.jsonc` (static assets from `./public`, no build step on Cloudflare, so built files are committed). Custom domain: `31daymaysql.vvizh.com`.
