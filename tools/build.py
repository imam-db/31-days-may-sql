"""Build the web page and the PDF for one day from content/dayNN.md.

  content/dayNN.md  ->  public/day/NN/index.html         (pandoc, tools/day-template.html)
                    ->  public/days/31DaysMaySQL-DayNN.pdf (Chrome headless, print styles in day.css)

The Markdown file is the source of truth. Run tools/check_sql.py first.
Usage: python tools/build.py <day-number> [--no-pdf]
       python tools/build.py all [--no-pdf]
"""
import html
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
CRLF = "\r\n"
LF = "\n"


def read_front_matter(md: str):
    md = md.replace(CRLF, LF)
    m = re.match(r"---\n(.*?)\n---\n", md, re.S)
    meta = {}
    if m:
        for line in m.group(1).splitlines():
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip().strip('"')
        md = md[m.end():]
    return meta, md


def md_to_html(md: str) -> str:
    md = re.sub(r"^```sql norun\s*$", "```sql", md, flags=re.M)
    r = subprocess.run(["pandoc", "-f", "markdown-smart", "-t", "html5", "--highlight-style=pygments"],
                       input=md.encode("utf-8"), capture_output=True, check=True)
    out = r.stdout.decode("utf-8").replace(CRLF, LF)  # pandoc on Windows prints CRLF
    # pandoc wraps highlighted code in div.sourceCode; keep the markup simple and themeable
    out = re.sub(r'<div class="sourceCode"[^>]*>(.*?)</div>', r"\1", out, flags=re.S)
    out = re.sub(r'<pre class="sourceCode sql"><code class="sourceCode sql">', '<pre class="code"><code>', out)
    out = re.sub(r'<span id="cb\d+-\d+"><a href="#cb\d+-\d+" aria-hidden="true" tabindex="-1"></a>', "<span>", out)
    out = out.replace('<pre class="result"><code>', '<pre class="out" aria-label="Query result"><code>')
    return out


def day_href(d: int) -> str:
    return f"../{d:02d}/" if (ROOT / "content" / f"day{d:02d}.md").exists() else f"../../days/31DaysMaySQL-Day{d:02d}.pdf"


def build(day: int, pdf: bool):
    nn = f"{day:02d}"
    meta, body_md = read_front_matter((ROOT / "content" / f"day{nn}.md").read_text(encoding="utf-8"))
    title = meta["title"]
    body = md_to_html(body_md)
    prev_link = f'<a href="{day_href(day - 1)}">← Day {day - 1:02d}</a>' if day > 1 else "<span></span>"
    next_link = f'<a href="{day_href(day + 1)}">Day {day + 1:02d} →</a>' if day < 31 else "<span></span>"
    page = (ROOT / "tools" / "day-template.html").read_text(encoding="utf-8").replace(CRLF, LF)
    page = (page.replace("{{TITLE}}", html.escape(title, quote=False))
                .replace("{{DESCRIPTION}}", html.escape(meta.get("summary", title)))
                .replace("{{NN}}", nn)
                .replace("{{BODY}}", body)
                .replace("{{PREV}}", prev_link)
                .replace("{{NEXT}}", next_link))
    out_dir = ROOT / "public" / "day" / nn
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "index.html"
    out.write_text(page, encoding="utf-8", newline=LF)
    print(f"wrote public/day/{nn}/index.html")
    if pdf:
        target = ROOT / "public" / "days" / f"31DaysMaySQL-Day{nn}.pdf"
        subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                        "--run-all-compositor-stages-before-draw", "--virtual-time-budget=8000",
                        f"--print-to-pdf={target}", out.as_uri()],
                       check=True, capture_output=True)
        print(f"wrote public/days/{target.name} ({target.stat().st_size // 1024} KB)")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    pdf = "--no-pdf" not in sys.argv
    days = sorted(int(p.stem[3:]) for p in (ROOT / "content").glob("day*.md")) if sys.argv[1] == "all" else [int(sys.argv[1])]
    for d in days:
        build(d, pdf)


if __name__ == "__main__":
    main()
