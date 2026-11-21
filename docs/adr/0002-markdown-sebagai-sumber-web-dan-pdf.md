# 0002. Markdown sebagai sumber tunggal untuk web dan PDF

- Status: Diterima
- Tanggal: 2026-11-21

## Konteks

31 PDF asli dibuat dengan LaTeX, tapi sumber LaTeX final hanya tersisa untuk 3 hari; draf Markdown di `papers/` sudah berbeda jauh dari PDF final. Saat dicek, beberapa PDF memuat klaim teknis yang salah (misalnya Day 22: klaim bahwa planner mengubah `NOT IN` jadi anti-join, dan catatan keliru soal `LEFT JOIN ... IS NULL`). Pemilik ingin isi diperbaiki, ditulis ulang supaya tidak terasa hasil AI, boleh ditambah, lalu tersedia sebagai halaman web dan PDF baru yang isinya sama, tanpa catatan perbandingan dengan PDF lama.

## Keputusan

- Titik awal tiap hari adalah PDF asli, diekstrak sekali ke `content/dayNN.md` dengan `tools/pdf2md.py` (posisi, font, dan warna teks dari `pdftohtml -xml`).
- Setelah itu `content/dayNN.md` adalah **sumber tunggal**. Halaman `public/day/NN/` dan PDF baru `public/days/31DaysMaySQL-DayNN.pdf` dibuat darinya dengan `tools/build.py` (pandoc untuk HTML, Chrome headless untuk PDF dengan gaya cetak di `public/day.css`). PDF lama digantikan.
- Setiap contoh SQL dijalankan dan setiap output yang ditampilkan dibandingkan dengan output asli PostgreSQL 18 (`tools/check_sql.py`). Gaya tulisan mengikuti `docs/panduan-gaya.md` dan diperiksa `tools/lint_style.py`.

## Pilihan lain yang ditimbang

- **Halaman web sebagai salinan persis PDF, koreksi dicatat di halaman.** Sempat dibuat untuk Day 22, lalu ditolak pemilik: koreksi cukup diterapkan, PDF ikut diperbarui.
- **Memperbaiki sumber LaTeX.** Hanya ada untuk 3 hari dan menambah satu toolchain lagi.
- **HTML ditulis tangan per hari.** Web dan PDF akan cepat berbeda.

## Akibat

- Mengubah isi berarti mengubah `content/dayNN.md`, menjalankan `check_sql`, `lint_style`, lalu `build`. PDF tidak lagi diedit langsung.
- Output di artikel dijamin dari PostgreSQL 18; contoh dialek lain ditandai `sql norun` dan tidak dijalankan.
- Build butuh pandoc, Chrome, dan server PostgreSQL lokal untuk pengecekan.
