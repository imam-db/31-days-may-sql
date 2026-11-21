"""Flag phrasing that reads as machine-written (see docs/panduan-gaya.md).

A clean run does not prove the text is good; it only catches the usual tells.
Usage: python tools/lint_style.py <day-number>|all
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PATTERNS = [
    (r"\b(today,? we will|in this (article|post|day)|let'?s dive|let us|welcome to)\b", "filler opener/transition"),
    (r"\b(very subtle|extremely|highly efficient|fundamentally|crucial|essential|powerful|seamless(ly)?|robust|game[- ]changer|delve|leverage|unlock|in today's)\b", "empty emphasis / stock word"),
    (r"\b\d{2,3}% of (developers|people|engineers|teams)\b", "made-up statistic"),
    (r"\bkey (takeaways?|insights?)\b", "takeaway boilerplate"),
    (r"\bit'?s not (just )?(about )?[^.]{1,40}, it'?s\b", "'not X, it's Y'"),
    (r"(^|\n)(Not [^.\n]{1,30}\.\s*){2,}", "fragment drama"),
    (r"(^|\n)(Same [^.\n]{1,25}\.\s*){2,}", "fragment drama"),
    (r"\b(mind-bending|magic(al)?|secret(ly)?|nightmare|silent killer)\b", "hype word"),
    (r"—", "em dash (use sparingly; prefer comma or full stop)"),
    (r"\b(norun|check_sql|lint_style)\b", "internal tool term in prose"),
    (r"\b(original (version|pdf|article|edition)|earlier version|previous version)\b", "refers to an older version of the article"),
]


def lint(nn):
    text = (ROOT / "content" / f"day{nn}.md").read_text(encoding="utf-8")
    prose = re.sub(r"```.*?```", "", text, flags=re.S)
    hits = []
    for pat, why in PATTERNS:
        for m in re.finditer(pat, prose, flags=re.I):
            line = prose.count("\n", 0, m.start()) + 1
            hits.append(f"day{nn}: {why}: «{m.group(0).strip()[:60]}» (prose line {line})")
    imagine = len(re.findall(r"\bImagine\b", prose))
    if imagine > 1:
        hits.append(f"day{nn}: 'Imagine' used {imagine} times")
    return hits


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    days = [p.stem[3:] for p in sorted((ROOT / "content").glob("day*.md"))] if sys.argv[1] == "all" else [f"{int(sys.argv[1]):02d}"]
    hits = [h for nn in days for h in lint(nn)]
    print("\n".join(hits) if hits else f"clean: {', '.join(days)}")
    sys.exit(1 if hits else 0)


if __name__ == "__main__":
    main()
