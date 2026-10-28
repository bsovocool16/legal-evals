"""Patents a company cites in its SEC filings, checked against USPTO status.

A 10-K that describes a patent as part of the company's protection when the
USPTO shows it lapsed for unpaid maintenance fees overstates what the company
owns. This module extracts US patent numbers with their surrounding text so
each flag can be read in context; deciding whether the filing actually claims
the patent as in force is left to review.
"""

from __future__ import annotations

import re

# "U.S. Patent No. 9,123,456", "U.S. Patent Nos. 9,123,456, 10,234,567 and 11,345,678",
# "United States Patent Number 10234567", "U.S. Pat. No. RE49,947".
KEYWORD_RE = re.compile(
    r"(?P<prefix>U\.\s?S\.|United\s+States|US)?\s*(?:Pat\.|Patent)\s+(?:No(?:s)?\.|Number(?:s)?|#)\s*",
    re.I,
)
NUMBER_RE = re.compile(r"(RE\s?\d{2},?\d{3}|D\s?\d{3},?\d{3}|\d{1,2},\d{3},\d{3}|\d{7,8})(?![\d/,]*\d/)", re.I)
LIST_SEP_RE = re.compile(r"\s*(?:,|;|and|or|&)\s*", re.I)
FOREIGN_RE = re.compile(
    r"(European|EP|Japan(?:ese)?|JP|China|Chinese|CN|Korea(?:n)?|KR|Australia(?:n)?|AU|Canad(?:a|ian)|CA|German|DE|French|FR|British|GB|UK|Indian|IN|Israeli|IL|Mexican|MX|Brazil(?:ian)?|BR|Russian|RU|Taiwan(?:ese)?|TW|PCT|WO|International)\b[^.\n]{0,25}$"
)


def normalize(raw: str) -> str | None:
    s = raw.upper().replace(",", "").replace(" ", "")
    if s.startswith("RE") or s.startswith("D"):
        return s
    if not s.isdigit():
        return None
    n = int(s)
    if not 4_000_000 <= n <= 13_500_000:
        return None  # outside the range of US utility patents in force recently
    return str(n)


def cited_patents(text: str, context: int = 400) -> list[dict]:
    """US patent numbers cited in the text, each with its first context."""
    found: dict[str, dict] = {}
    for m in KEYWORD_RE.finditer(text):
        before = text[max(0, m.start() - 40):m.start()]
        if not m.group("prefix") and FOREIGN_RE.search(before):
            continue
        pos = m.end()
        while True:
            n = NUMBER_RE.match(text, pos)
            if not n:
                break
            num = normalize(n.group(1))
            if num and num not in found:
                lo, hi = max(0, m.start() - context), min(len(text), n.end() + context)
                found[num] = {"number": num, "offset": n.start(), "context": " ".join(text[lo:hi].split())}
            pos = n.end()
            sep = LIST_SEP_RE.match(text, pos)
            if not sep:
                break
            pos = sep.end()
    return list(found.values())


def run(filings_dir: str, out_path: str) -> dict:
    """Check every patent cited in a directory of filings against USPTO status."""
    import json
    from collections import Counter
    from pathlib import Path

    from ..sources.uspto import UsptoClient, status
    from ..text import to_text

    root = Path(filings_dir)
    manifest = {json.loads(l)["path"]: json.loads(l) for l in (root / "manifest.jsonl").read_text().splitlines()}
    client = UsptoClient()
    counts: Counter = Counter()
    with open(out_path, "w") as out:
        for name, meta in sorted(manifest.items()):
            text = to_text((root / name).read_text(errors="replace"))
            cites = cited_patents(text)
            counts["filings"] += 1
            counts["filings_citing"] += bool(cites)
            for c in cites:
                counts["citations"] += 1
                rec = client.patent(c["number"])
                if rec is None:
                    counts["not_found"] += 1
                    st = {"patent_number": c["number"], "status": "NOT FOUND"}
                else:
                    st = status(rec)
                    counts["lapsed"] += bool(st["lapsed_for_nonpayment"])
                out.write(json.dumps({
                    "company": meta.get("company"), "cik": meta.get("cik"), "form": meta.get("form"),
                    "file_date": meta.get("file_date"), "url": meta.get("url"), **c, **st,
                }) + "\n")
    return dict(counts)
