# 0001. Situs statis di Cloudflare Workers

- Status: Diterima
- Tanggal: 2026-11-21

## Konteks

Seri #31DaysMaySQL selesai Mei 2026 sebagai 31 PDF. Portfolio vvizh.com hanya boleh menautkan seri yang sudah terbit dengan link spesifik, jadi seri ini butuh alamat publik sendiri.

## Keputusan

Situs statis di subdomain `31daymaysql.vvizh.com`, di-deploy dengan Cloudflare Workers Builds dari repo GitHub `imam-db/31-days-may-sql`. Konfigurasi di `wrangler.jsonc`: aset statis dari `./public`, tanpa langkah build di Cloudflare.

## Pilihan lain yang ditimbang

- **Deploy langsung dengan `wrangler deploy` dari laptop.** Lebih cepat sekali jalan, tapi tidak ada riwayat dan preview per perubahan.
- **Post LinkedIn per hari sebagai link seri.** Tidak ada satu tempat yang memuat semua hari.

## Akibat

- Setiap push ke `main` langsung terbit. Hasil build (HTML, PDF) ikut di-commit karena Cloudflare tidak menjalankan `tools/build.py`.
- Sama dengan pola situs lain di vvizh (ADR 0006 di repo vvizh).
