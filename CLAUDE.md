# #31DaysMaySQL

Situs statis seri SQL 31 hari (Mei 2026), terbit di `31daymaysql.vvizh.com` lewat Cloudflare Workers Builds (`wrangler.jsonc`, aset `./public`). Repo: `imam-db/31-days-may-sql`.

## Sumber dan build

- `content/dayNN.md` adalah sumber tunggal tiap hari (ADR 0002). Halaman `public/day/NN/` dan PDF `public/days/31DaysMaySQL-DayNN.pdf` dihasilkan `tools/build.py`; jangan edit hasilnya langsung.
- Sebelum build: `tools/check_sql.py NN` (semua SQL dijalankan, output dicocokkan dengan PostgreSQL 18) dan `tools/lint_style.py NN`.
- Gaya tulisan: `docs/panduan-gaya.md`. Artikel berbahasa Inggris; dokumen repo berbahasa Indonesia.
- `papers/`, `CONTENT_PLAN.md`, dan landing draf di root adalah arsip lokal, tidak di-commit.

## Aturan dokumentasi

- Setiap perubahan yang di-commit memperbarui `CHANGELOG.md` di commit yang sama.
- Keputusan yang sulit diubah dicatat sebagai ADR baru di `docs/adr/`.
- Peta dokumen: `docs/README.md`.
