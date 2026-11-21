"""Turn an original #31DaysMaySQL PDF into an editable Markdown draft.

This is the starting point only: content/dayNN.md is then checked, corrected
and edited by hand, and becomes the source for both the web page and the new PDF
(see tools/build.py). Refuses to overwrite an existing content file.

Usage: python tools/pdf2md.py <day-number>
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pdf2html as p  # noqa: E402

ROOT = p.ROOT


def html_inline_to_md(s: str) -> str:
    s = re.sub(r"<code>(.*?)</code>", lambda m: "`" + m.group(1) + "`", s)
    s = re.sub(r"<strong>(.*?)</strong>", r"**\1**", s)
    s = re.sub(r"<em>(.*?)</em>", r"*\1*", s)
    return p.html.unescape(s)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    day = int(sys.argv[1])
    nn = f"{day:02d}"
    out = ROOT / "content" / f"day{nn}.md"
    if out.exists():
        raise SystemExit(f"{out} already exists; it is the edited source, not overwriting")
    lines = p.group_lines(p.load_runs(p.run_pdftohtml(ROOT / "public" / "days" / f"31DaysMaySQL-Day{nn}.pdf")))
    meta, blocks = p.build_blocks(lines)
    md = [f"---\nday: {day}\ntitle: \"{meta.get('title', '')}\"\n---\n"]
    for b in blocks:
        t = b["type"]
        if t == "h2":
            md.append(f"## {b['text']}\n")
        elif t == "h3":
            md.append(f"### {b['text']}\n")
        elif t == "p":
            md.append(html_inline_to_md(b["html"]) + "\n")
        elif t == "ul":
            md.append("\n".join(f"- {html_inline_to_md(x)}" for x in b["items"]) + "\n")
        elif t == "code":
            md.append(f"```sql\n{b['text']}\n```\n")
        elif t == "out":
            md.append(f"```result\n{b['text']}\n```\n")
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(md), encoding="utf-8")
    print(f"wrote content/day{nn}.md ({len(blocks)} blocks)")


if __name__ == "__main__":
    main()
