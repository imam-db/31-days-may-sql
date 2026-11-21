# Dokumen #31DaysMaySQL

| Dokumen | Isi |
| --- | --- |
| [`../CHANGELOG.md`](../CHANGELOG.md) | Apa yang berubah, terbaru di atas |
| [`adr/`](adr/README.md) | Kenapa keputusan besar diambil |
| [`panduan-gaya.md`](panduan-gaya.md) | Cara menulis artikel: suara, pola yang dilarang, format Markdown, definisi selesai |

## Alur kerja satu hari

```
public/days/…DayNN.pdf (asli) ──pdf2md──▶ content/dayNN.md ──(tulis, cek)──▶ build ──▶ public/day/NN/ + PDF baru
```

1. `python -I tools/pdf2md.py NN` (sekali saja; menolak menimpa file yang sudah ada)
2. Edit `content/dayNN.md`
3. `python -I tools/check_sql.py NN` → 0 problem (butuh PostgreSQL 18 lokal, lihat docstring)
4. `python -I tools/lint_style.py NN` → clean, lalu baca ulang
5. `python -I tools/build.py NN` → HTML + PDF
6. Bila judul atau `teaser` berubah: `python -I tools/build_index.py` (daftar di `public/index.html`)
