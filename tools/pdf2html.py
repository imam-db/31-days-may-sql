"""Parse an original #31DaysMaySQL PDF into structured blocks (library for tools/pdf2md.py).

Its own page-rendering mode was the first prototype and is no longer used: pages are
built from content/dayNN.md by tools/build.py (ADR 0002).

The PDF is the only source. `pdftohtml -xml` gives every text run with its
position, font and colour; the LaTeX template used for all 31 days is
consistent, so each element can be identified from those attributes:

  NotoSans bold, 22px, navy        -> section heading (h2)
  NotoSerif bold, 16px, navy       -> sub-heading (h3)
  NotoSansMono 15px, white         -> query output (dark box)
  NotoSansMono 15px, other colours -> SQL code (light box), colours kept
  NotoSansMono 16px                -> inline code
  NotoSerif 16px                   -> body text (bold / italic kept)
  "•" at the list indent           -> list item

Usage: python tools/pdf2html.py <day-number> [--json]
Writes public/day/NN/index.html and prints a log of hyphen joins to review.
"""
import html
import json
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE_HEADER_MAX_TOP = 75     # running header "#31DaysMaySQL ... Day NN: ..."
PAGE_FOOTER_MIN_TOP = 1180   # page number
BODY_LEFT = 85
PARA_GAP = 30                # vertical gap that starts a new paragraph (line pitch is ~25)
CODE_BLOCK_GAP = 50          # gap that ends a code box (a blank code line is ~45)
KEEP_HYPHEN_PREFIXES = {
    "non", "null", "self", "multi", "co", "pre", "anti", "semi", "well", "built",
    "real", "high", "low", "one", "two", "three", "first", "second", "third", "row", "set",
    "case", "time", "read", "write", "second-order", "order", "cross", "left", "right",
    "full", "top", "sub", "pseudo", "post", "run", "long", "short", "open", "close",
}

NAVY = "#0e2d50"


def run_pdftohtml(pdf: Path) -> ET.Element:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "doc"
        subprocess.run(["pdftohtml", "-xml", "-i", "-q", "-nodrm", "-enc", "UTF-8", str(pdf), str(out)], check=True)
        raw = (Path(tmp) / "doc.xml").read_text(encoding="utf-8", errors="replace")
    raw = re.sub(r"<!DOCTYPE[^>]*>", "", raw)
    return ET.fromstring(raw)


def classify(font: dict, bold: bool, italic: bool) -> str:
    fam, size, color = font["family"], font["size"], font["color"].lower()
    if "Mono" in fam:
        if size <= 15:
            return "out" if color == "#ffffff" else "code"
        return "icode"
    if "NotoSans" in fam and "Serif" not in fam:
        if size >= 30:
            return "title"
        if size >= 24:
            return "eyebrow"
        if size >= 20:
            return "h2"
        return "sans"  # running header, ignored by position
    # serif
    if size >= 20:
        return "author"
    if size >= 17:
        return "date"
    if bold and color == NAVY:
        return "h3"
    if color == NAVY and not bold:
        return "bulletmark"
    return "text"


def load_runs(root: ET.Element):
    fonts = {}
    runs = []
    for page in root.iter("page"):
        pno = int(page.get("number"))
        for fs in page.iter("fontspec"):
            fonts[fs.get("id")] = {"family": fs.get("family"), "size": int(fs.get("size")), "color": fs.get("color")}
        for t in page.iter("text"):
            top, left = int(t.get("top")), int(t.get("left"))
            if top < PAGE_HEADER_MAX_TOP or top > PAGE_FOOTER_MIN_TOP:
                continue
            text = "".join(t.itertext())
            if text == "":
                continue
            bold = t.find(".//b") is not None
            italic = t.find(".//i") is not None
            f = fonts[t.get("font")]
            runs.append({
                "page": pno, "top": top, "left": left, "width": int(t.get("width")),
                "height": int(t.get("height")), "text": text, "bold": bold, "italic": italic,
                "color": f["color"].lower(), "size": f["size"], "kind": classify(f, bold, italic),
            })
    return runs


def group_lines(runs):
    """Group runs that share a baseline into lines (same page, |top| within 3px)."""
    lines = []
    for r in sorted(runs, key=lambda r: (r["page"], r["top"], r["left"])):
        if lines and lines[-1]["page"] == r["page"] and abs(lines[-1]["top"] - r["top"]) <= 3:
            lines[-1]["runs"].append(r)
        else:
            lines.append({"page": r["page"], "top": r["top"], "runs": [r]})
    for ln in lines:
        ln["runs"].sort(key=lambda r: r["left"])
        kinds = {r["kind"] for r in ln["runs"]}
        if kinds <= {"code"}:
            ln["kind"] = "code"
        elif kinds <= {"out"}:
            ln["kind"] = "out"
        elif kinds & {"title", "eyebrow", "author", "date", "h2"}:
            ln["kind"] = next(k for k in ("title", "eyebrow", "author", "date", "h2") if k in kinds)
        elif kinds <= {"h3"}:
            ln["kind"] = "h3"
        elif ln["runs"][0]["text"].strip() == "•":
            ln["kind"] = "li"
        else:
            ln["kind"] = "text"
        ln["left"] = ln["runs"][0]["left"]
    return lines


# ---------- inline rendering ----------

def esc(s):
    return html.escape(s, quote=False)


def inline_html(runs):
    """Render body runs of one line, keeping bold/italic/code and the spaces between runs."""
    out = []
    prev_end = None
    for r in runs:
        if r["kind"] == "bulletmark":
            continue
        if prev_end is not None and r["left"] - prev_end > 1:
            out.append(" ")
        t = esc(r["text"])
        if r["kind"] == "icode":
            t = f"<code>{t}</code>"
        if r["bold"] and r["kind"] != "h3":
            t = f"<strong>{t}</strong>"
        if r["italic"]:
            t = f"<em>{t}</em>"
        out.append(t)
        prev_end = r["left"] + r["width"]
    s = "".join(out)
    # merge adjacent identical wrappers produced by split runs
    for tag in ("strong", "em", "code"):
        s = re.sub(rf"</{tag}>(\s*)<{tag}>", r"\1", s)
    return s


def plain(runs):
    return " ".join(r["text"] for r in runs if r["kind"] != "bulletmark")


HYPHEN_LOG = []


def join_lines(parts):
    """Join wrapped lines; undo LaTeX hyphenation but keep real compound hyphens."""
    s = parts[0]
    for nxt in parts[1:]:
        m = re.search(r"([A-Za-z][A-Za-z-]*)-$", s)
        if m and re.match(r"[a-z]", re.sub(r"^<[^>]+>", "", nxt)):
            left = m.group(1).lower()
            if left in KEEP_HYPHEN_PREFIXES:
                HYPHEN_LOG.append(f"kept   {m.group(1)}-{nxt.split()[0]}")
                s = s + nxt
            else:
                HYPHEN_LOG.append(f"joined {m.group(1)}{nxt.split()[0]}")
                s = s[:-1] + nxt
        else:
            s = s + " " + nxt
    return s


# ---------- code rendering ----------

CODE_CLASS = {"#007f00": "c", "#0000b2": "k", "#c81e1e": "s"}


def code_line_html(ln, x0, charw, with_colors):
    col = 0
    out = []
    for r in ln["runs"]:
        target = round((r["left"] - x0) / charw)
        if target > col:
            out.append(" " * (target - col))
            col = target
        elif out and target < col:
            out.append(" ")  # overlapping runs: keep a separator
            col += 1
        t = esc(r["text"])
        cls = CODE_CLASS.get(r["color"]) if with_colors else None
        out.append(f'<span class="{cls}">{t}</span>' if cls else t)
        col += len(r["text"])
    return "".join(out)


def code_line_plain(ln, x0, charw):
    col, out = 0, []
    for r in ln["runs"]:
        target = round((r["left"] - x0) / charw)
        if target > col:
            out.append(" " * (target - col))
            col = target
        elif out and target < col:
            out.append(" ")
            col += 1
        out.append(r["text"])
        col += len(r["text"])
    return "".join(out)


# ---------- block building ----------

def build_blocks(lines):
    blocks = []
    meta = {}
    i = 0
    n = len(lines)
    while i < n:
        ln = lines[i]
        k = ln["kind"]
        if k in ("title", "eyebrow", "author", "date"):
            # a long title wraps over two lines in the PDF: join them
            meta[k] = (meta[k] + " " + plain(ln["runs"])) if k == "title" and k in meta else plain(ln["runs"])
            i += 1
        elif k == "h2":
            blocks.append({"type": "h2", "html": inline_html(ln["runs"]), "text": plain(ln["runs"])})
            i += 1
        elif k == "h3":
            blocks.append({"type": "h3", "html": inline_html(ln["runs"]), "text": plain(ln["runs"])})
            i += 1
        elif k in ("code", "out"):
            group = [ln]
            j = i + 1
            while j < n and lines[j]["kind"] == k:
                prev, cur = group[-1], lines[j]
                if cur["page"] == prev["page"] and cur["top"] - prev["top"] > CODE_BLOCK_GAP:
                    break
                if cur["page"] != prev["page"]:
                    # continue across a page only if nothing else sits between
                    pass
                group.append(cur)
                j += 1
            blocks.append(make_code_block(group, k))
            i = j
        elif k == "li":
            item = [ln]
            j = i + 1
            while j < n and lines[j]["kind"] == "text" and lines[j]["left"] > BODY_LEFT + 10:
                if lines[j]["page"] == item[-1]["page"] and lines[j]["top"] - item[-1]["top"] > PARA_GAP:
                    break
                item.append(lines[j])
                j += 1
            parts = [inline_html(x["runs"]) for x in item]
            text = " ".join(plain(x["runs"]) for x in item)
            if blocks and blocks[-1]["type"] == "ul":
                blocks[-1]["items"].append(join_lines(parts))
                blocks[-1]["text"] += " " + text
            else:
                blocks.append({"type": "ul", "items": [join_lines(parts)], "text": text})
            i = j
        else:  # text paragraph
            para = [ln]
            j = i + 1
            while j < n and lines[j]["kind"] == "text" and lines[j]["left"] <= BODY_LEFT + 10:
                prev, cur = para[-1], lines[j]
                if cur["page"] == prev["page"] and cur["top"] - prev["top"] > PARA_GAP:
                    break
                if cur["page"] != prev["page"]:
                    last = plain(prev["runs"]).rstrip()
                    if last.endswith((".", ":", "?", "!")):
                        break
                para.append(cur)
                j += 1
            parts = [inline_html(x["runs"]) for x in para]
            blocks.append({"type": "p", "html": join_lines(parts),
                           "text": " ".join(plain(x["runs"]) for x in para)})
            i = j
    # a list item interrupted by nothing but a bullet list closes the list
    return meta, blocks


def make_code_block(group, kind):
    x0 = min(r["left"] for ln in group for r in ln["runs"])
    widths = [r["width"] / len(r["text"]) for ln in group for r in ln["runs"] if len(r["text"]) >= 3]
    charw = sorted(widths)[len(widths) // 2] if widths else 9.0
    pitch = 22.4
    html_lines, plain_lines = [], []
    for idx, ln in enumerate(group):
        if idx:
            prev = group[idx - 1]
            if ln["page"] == prev["page"]:
                blanks = round((ln["top"] - prev["top"]) / pitch) - 1
                for _ in range(max(0, blanks)):
                    html_lines.append("")
                    plain_lines.append("")
        html_lines.append(code_line_html(ln, x0, charw, kind == "code"))
        plain_lines.append(code_line_plain(ln, x0, charw))
    return {"type": kind, "html": "\n".join(html_lines), "text": "\n".join(plain_lines)}


# ---------- page ----------

def load_corrections(nn: str):
    p = ROOT / "tools" / "corrections" / f"day{nn}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def apply_corrections(blocks, corrections):
    """Each correction replaces one exact HTML fragment; it must match exactly once."""
    for c in corrections:
        hits = [b for b in blocks if c["find"] in b.get("html", "") or any(c["find"] in x for x in b.get("items", []))]
        count = sum(b.get("html", "").count(c["find"]) + sum(x.count(c["find"]) for x in b.get("items", [])) for b in blocks)
        if count != 1:
            raise SystemExit(f"correction matched {count} times, expected 1: {c['find']!r}")
        b = hits[0]
        if "items" in b:
            b["items"] = [x.replace(c["find"], c["replace"]) for x in b["items"]]
        else:
            b["html"] = b["html"].replace(c["find"], c["replace"])
    return blocks


def corrections_html(corrections):
    if not corrections:
        return ""
    items = "".join(f"<li>{c['note']}</li>" for c in corrections)
    return ('<aside class="corrections" aria-labelledby="corr-title">'
            '<h2 id="corr-title">Corrections since the PDF</h2>'
            f"<ul>{items}</ul></aside>")


def render_page(day: int, meta: dict, blocks: list, corrections: list) -> str:
    title = meta.get("title", f"Day {day}")
    body = []
    for b in blocks:
        t = b["type"]
        if t == "h2":
            body.append(f'<h2>{b["html"]}</h2>')
        elif t == "h3":
            body.append(f'<h3>{b["html"]}</h3>')
        elif t == "p":
            body.append(f'<p>{b["html"]}</p>')
        elif t == "ul":
            body.append("<ul>" + "".join(f"<li>{x}</li>" for x in b["items"]) + "</ul>")
        elif t == "code":
            body.append(f'<pre class="code"><code>{b["html"]}</code></pre>')
        elif t == "out":
            body.append(f'<pre class="out" aria-label="Query result"><code>{b["html"]}</code></pre>')
    template = (ROOT / "tools" / "day-template.html").read_text(encoding="utf-8")
    nn = f"{day:02d}"
    def day_href(d):
        # link the web page when it exists, otherwise the PDF
        if (ROOT / "public" / "day" / f"{d:02d}" / "index.html").exists():
            return f"../{d:02d}/"
        return f"../../days/31DaysMaySQL-Day{d:02d}.pdf"
    prev_link = f'<a href="{day_href(day - 1)}">← Day {day - 1:02d}</a>' if day > 1 else "<span></span>"
    next_link = f'<a href="{day_href(day + 1)}">Day {day + 1:02d} →</a>' if day < 31 else "<span></span>"
    return (template
            .replace("{{TITLE}}", esc(title))
            .replace("{{NN}}", nn)
            .replace("{{EYEBROW}}", esc(meta.get("eyebrow", f"#31DaysMaySQL - Day {nn}")))
            .replace("{{DATE}}", esc(meta.get("date", "May 2026")))
            .replace("{{BODY}}", "\n".join(body))
            .replace("{{CORRECTIONS}}", corrections_html(corrections))
            .replace("{{PDF_NOTE}}", ", as first published (without the corrections listed above)" if corrections else "")
            .replace("{{PREV}}", prev_link)
            .replace("{{NEXT}}", next_link))


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    day = int(sys.argv[1])
    nn = f"{day:02d}"
    pdf = ROOT / "public" / "days" / f"31DaysMaySQL-Day{nn}.pdf"
    root = run_pdftohtml(pdf)
    lines = group_lines(load_runs(root))
    meta, blocks = build_blocks(lines)
    out_dir = ROOT / "build" / "extracted"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"day{nn}.json").write_text(json.dumps({"meta": meta, "blocks": blocks}, indent=1, ensure_ascii=False), encoding="utf-8")
    if "--json" in sys.argv:
        print(f"wrote build/extracted/day{nn}.json ({len(blocks)} blocks)")
    else:
        corrections = load_corrections(nn)
        blocks = apply_corrections(blocks, corrections)
        page_dir = ROOT / "public" / "day" / nn
        page_dir.mkdir(parents=True, exist_ok=True)
        (page_dir / "index.html").write_text(render_page(day, meta, blocks, corrections), encoding="utf-8")
        print(f"wrote public/day/{nn}/index.html ({len(blocks)} blocks)")
    for h in HYPHEN_LOG:
        print("hyphen:", h)


if __name__ == "__main__":
    main()
