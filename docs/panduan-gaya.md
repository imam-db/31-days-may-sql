# Panduan gaya #31DaysMaySQL

Artikel ditulis dalam bahasa Inggris. Panduan ini berlaku untuk setiap `content/dayNN.md`.
Tujuannya: terdengar seperti engineer yang menjelaskan ke rekan kerja, bukan seperti teks hasil mesin.

## Prinsip

1. **Benar dulu.** Setiap klaim teknis bisa dibuktikan: SQL-nya dijalankan (`tools/check_sql.py`), output yang ditampilkan adalah output asli PostgreSQL 18, rencana query diambil dari `EXPLAIN` asli. Klaim untuk MySQL/SQL Server/BigQuery/Oracle hanya bila memang dikenal luas dan terdokumentasi; jika ragu, hapus atau sebut "in PostgreSQL".
2. **Konkret.** Pakai data, angka, dan nama tabel dari contoh. "Bob and Eve disappear" lebih baik dari "results may be incorrect".
3. **Langsung.** Kalimat pendek-menengah. Satu paragraf satu gagasan. Mulai dari masalahnya, bukan dari pengantar.
4. **Isi boleh ditambah** bila membuat pemahaman lebih kuat: contoh tandingan, `EXPLAIN` asli, kasus tepi, perbedaan antar-database yang pasti, cara mendeteksi masalah di data nyata. Jangan menambah demi panjang.
5. **Isi boleh dirombak** bila urutan atau contohnya janggal. Judul hari tetap (boleh dirapikan sedikit), topik tetap.

## Dilarang (pola yang terasa AI)

| Pola | Contoh yang dihapus | Ganti dengan |
| --- | --- | --- |
| Pembuka/penutup basa-basi | "Today, we will explore…", "In this article…", "Let us dive in", "Welcome to…" | Langsung ke masalah |
| Drama potongan kalimat | "Not a syntax error. Not a typo." / "Same query. Different answer." | Satu kalimat biasa yang menyatakan faktanya |
| Penekanan kosong | "very subtle", "extremely useful", "highly efficient", "fundamentally different", "crucial", "essential", "powerful", "seamlessly", "robust" | Sebutkan *apa* yang terjadi atau angkanya |
| Statistik karangan | "90% of developers get it wrong" | Hapus, atau ganti dengan pengamatan yang bisa dicek |
| Kalimat pembuka "Imagine…" berulang | "Imagine your manager walks over…" | Boleh sekali bila skenario memang membantu; jangan jadi template |
| Trio retoris | "fast, reliable, and scalable" | Satu sifat yang relevan |
| "It's not X, it's Y" | "This isn't about Fibonacci, it's about…" | Nyatakan Y saja |
| Bullet berlabel tebal untuk semua hal | "**Key insight:** …" di setiap poin | Paragraf biasa; bullet hanya untuk daftar yang memang daftar |
| Penutup "Key Takeaways" yang mengulang isi | lima bullet yang menyalin bagian sebelumnya | Bagian pendek "In short" berisi aturan praktis, atau hapus |
| Kata "Let us" / "Let's" sebagai transisi | "Let us examine why…" | Judul bagian sudah cukup |
| Em dash berlebihan | — di setiap kalimat | Titik atau koma |

## Format Markdown

- Front matter: `day`, `title`, `summary` (satu-dua kalimat, untuk meta description), `teaser` (satu baris pendek untuk daftar di halaman indeks; `tools/build_index.py`).
- Tidak ada `# H1` di isi; judul dari front matter. Bagian memakai `##`, sub-bagian `###`.
- Kode yang dijalankan: ```` ```sql ````. Potongan yang tidak bisa dijalankan sendiri atau dialek lain: ```` ```sql norun ````. Output yang tidak bisa dicek otomatis (pesan error, transkrip dua sesi) memakai ```` ```text ```` dan harus berasal dari psql sungguhan.
- Istilah alat internal (`norun`, `check_sql`) tidak boleh muncul di prosa, begitu juga rujukan ke versi lama artikel.
- Output: ```` ```result ```` tepat setelah blok `sql` yang menghasilkannya, ditulis persis seperti output `psql` (termasuk `(N rows)`). Bila satu blok berisi beberapa query, setiap query harus menghasilkan output yang sama.
- Blok `sql` dijalankan berurutan dalam satu database baru dan satu sesi psql (jadi `SET`, `setseed()`, dan transaksi terbawa), jadi tabel yang dibuat di awal tersedia untuk blok berikutnya. Buat sendiri tabel yang dibutuhkan; jangan mengandalkan tabel yang tidak didefinisikan.
- Paragraf pembuka singkat menyebut database yang dipakai (PostgreSQL 18) hanya bila relevan.

## Selesai berarti

1. `python tools/check_sql.py NN` → 0 problem.
2. Tidak ada pola dari tabel "Dilarang" (`python tools/lint_style.py NN` → bersih, lalu baca ulang).
3. `python tools/build.py NN` → halaman web dan PDF baru.
