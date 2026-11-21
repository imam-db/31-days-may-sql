# #31DaysMaySQL

One SQL problem a day for May 2026 (31 days, 5 themes). Examples in PostgreSQL.

- `public/` — what gets deployed: `index.html` (index of all 31 days) and `days/` (the final PDFs)

The writing sources (Markdown drafts, content plan, early landing-page drafts) stay local and are not part of this repo; the PDFs are the final version.

## Deploy

Cloudflare Workers Builds, configured in `wrangler.jsonc` (static assets from `./public`, no build command).
Custom domain: `31daymaysql.vvizh.com`.

Local preview: open `public/index.html`, or `npx wrangler dev`.
