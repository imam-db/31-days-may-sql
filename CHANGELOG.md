# Changelog

Semua perubahan penting pada situs #31DaysMaySQL. Yang terbaru di atas.

| Tanggal | Commit | Perubahan | ADR |
| --- | --- | --- | --- |
| 2026-11-22 | (commit ini) | 31 hari divalidasi, diperbaiki, ditulis ulang dan diperkaya; tiap hari kini halaman web + PDF baru dari satu sumber Markdown | [0002](docs/adr/0002-markdown-sebagai-sumber-web-dan-pdf.md) |
| 2026-11-21 | `8fd7fb4` | Situs pertama: halaman indeks 31 hari yang menautkan PDF asli, `wrangler.jsonc` | [0001](docs/adr/0001-situs-statis-di-cloudflare-workers.md) |

## 2026-11-22 — Isi divalidasi dan ditulis ulang, halaman web per hari

### Ditambah
- `content/day01.md`–`day31.md`: sumber tunggal tiap hari, diekstrak sekali dari PDF asli lalu disunting.
- `public/day/NN/`: halaman web per hari, dengan tombol unduh PDF di akhir dan navigasi antarhari.
- `tools/`: `pdf2md.py` (impor dari PDF asli, judul dua baris digabung), `check_sql.py` (semua SQL dijalankan dalam satu sesi PostgreSQL 18; setiap output yang ditampilkan harus sama dengan output psql asli), `lint_style.py` (pola tulisan yang terasa mesin, istilah alat internal, rujukan ke versi lama), `build.py` (pandoc → HTML, Chrome → PDF), `build_index.py` (daftar di halaman indeks dari front matter).
- `docs/panduan-gaya.md`, `docs/README.md`, `docs/adr/` (0001, 0002), `CLAUDE.md`.

### Diubah
- Ke-31 PDF di `public/days/` dibuat ulang dari Markdown; isinya sama dengan halaman web.
- Prosa semua hari ditulis ulang tanpa pola khas AI (pembuka basa-basi, potongan kalimat dramatis, penekanan kosong, statistik karangan, "Key Takeaways"); semua hari lolos `lint_style`.
- Pengayaan di semua hari: `EXPLAIN` asli, contoh tandingan, kasus tepi, cara mendeteksi masalah di database nyata, perbedaan antar-database yang pasti.
- Halaman indeks: setiap hari menautkan halaman webnya; judul dan ringkasan dari front matter; kalimat pembuka ditulis ulang; label kategori dihapus.
- Judul yang terpotong di impor dipulihkan (Day 2, 4, 9, 13, 14, 19–21, 23, 25, 30, 31); Day 10 menjadi "Build a Football League Table with SQL" karena datanya liga rekaan.

### Diperbaiki (kesalahan isi di PDF asli, contoh utama)
- Day 2: `COUNT(DISTINCT (a, b))` dengan row constructor tetap menghitung pasangan yang mengandung `NULL`; PDF bilang keduanya membuang baris itu.
- Day 4: trik `DENSE_RANK` untuk `NULL` hanya benar bila `NULL` di urutan terakhir; tabel dukungan database yang keliru dihapus.
- Day 5: `LIMIT/OFFSET` bukan pagination berbasis cursor; `ORDER BY col IS NULL` hanya di MySQL; tabel yang tidak didefinisikan dibuat.
- Day 6: contoh nomor telepon yang tidak mungkin cocok, "perbaikan" `::TEXT` yang salah, waktu karangan.
- Day 9: klaim "double NOT EXISTS biasanya lebih cepat" tanpa dasar; statistik "90% developer".
- Day 10: urutan form lima laga dengan gameweek rusak saat laga ditunda; `ROW_NUMBER` diganti `RANK` untuk klub yang benar-benar seri.
- Day 13: interpolasi persentil yang hanya benar untuk median.
- Day 14: BigQuery punya operator bitwise; `bit_count` ada di PostgreSQL 14+; `INT` punya 31 bit flag yang aman, bukan 32.
- Day 15: PostgreSQL menerima `UNION` di CTE rekursif; deteksi siklus kini benar-benar ditunjukkan (klausa `CYCLE`).
- Day 16: pendekatan `LEAD` melewatkan booking panjang; diganti running max dan dibuktikan.
- Day 17–18: typo regex, `split_part` memotong `rc-1`, angka amortisasi karangan, klaim rekursi tak terbatas "akhirnya berhenti".
- Day 19–21: frame `ROWS` tidak deterministik saat seri, "2N" grouping set (seharusnya 2^N), key JSON yang tidak ada di payload, pesan error dan ukuran indeks yang salah.
- Day 22: `NOT IN` tidak pernah diubah jadi anti-join di PostgreSQL; catatan keliru soal kolom yang diuji setelah `LEFT JOIN`.
- Day 23–25: `email = 12345` adalah error di PostgreSQL, bukan cast diam-diam; subquery berkorelasi tidak di-dekorelasi; `ANALYZE` memang membatalkan rencana prepared statement yang di-cache; waktu karangan dihapus.
- Day 26–27: filter di luar `LATERAL` tanpa `LIMIT` menghasilkan rencana yang sama; predikat indeks parsial wajib fungsi IMMUTABLE; "workaround" MySQL yang tidak membuat indeks lebih kecil.
- Day 28–31: visibility map yang tertinggal tidak mengubah Index Only Scan (hanya `Heap Fetches` naik); `VACUUM` bukan "non-locking"; contoh write skew yang sebenarnya lost update; PostgreSQL tidak menulis ulang `OR` menjadi `UNION` dan tidak menghapus inner join lewat foreign key.
