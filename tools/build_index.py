"""Refresh the day list in public/index.html from content/dayNN.md front matter.

Each day's <li> (found by its <span class="d">NN</span>) gets the title and
teaser from the Markdown file and links to the web page public/day/NN/.
The week headings and the rest of the page are left alone.

Usage: python tools/build_index.py
"""
import html
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build import read_front_matter  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    index = ROOT / "public" / "index.html"
    page = index.read_text(encoding="utf-8")
    done = 0
    for md in sorted((ROOT / "content").glob("day*.md")):
        nn = md.stem[3:]
        meta, _ = read_front_matter(md.read_text(encoding="utf-8"))
        title = html.escape(meta["title"], quote=False)
        teaser = html.escape(meta.get("teaser", ""), quote=False)
        # inline `code` in teasers is not used; plain text only
        li = (f'<li><a href="day/{nn}/"><span class="d">{nn}</span>'
              f'<span class="t">{title}</span><span class="s">{teaser}</span></a></li>')
        page, n = re.subn(rf'<li><a href="[^"]*"><span class="d">{nn}</span>.*?</li>', lambda _m: li, page, count=1, flags=re.S)
        done += n
    index.write_text(page, encoding="utf-8", newline="\n")
    print(f"updated {done} day entries in public/index.html")


if __name__ == "__main__":
    main()
