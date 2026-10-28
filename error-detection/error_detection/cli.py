"""Command line entry point.

  python -m error_detection edgar-fetch --q ... --out DIR       download filings matching an EDGAR full-text query
  python -m error_detection patents-join --dir DIR --out F      check patents cited in those filings against USPTO status
  python -m error_detection short-swing --dir DIR --out F ...   Section 16(b) screen over SEC Form 3/4/5 data sets
  python -m error_detection blm-check FILE... --out F ...       check mining-claim serials cited in filings against BLM

Network access is needed for every command except the screens' own logic. SEC requests need SEC_USER_AGENT
("Name email"); USPTO requests need USPTO_API_KEY.
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path


def cmd_edgar_fetch(args) -> int:
    from .sources.edgar import EdgarClient, download

    client = EdgarClient()
    forms = args.forms.split(",") if args.forms else None
    hits = client.iter_hits(args.q, forms=forms, start=args.start, end=args.end,
                            file_type_prefix=args.exhibit, limit=args.limit)
    saved = download(client, hits, Path(args.out))
    print(f"saved {len(saved)} documents to {args.out}")
    return 0


def cmd_patents_join(args) -> int:
    from .joins import patents

    counts = patents.run(args.dir, args.out)
    print(counts)
    return 0


def cmd_short_swing(args) -> int:
    from datetime import date

    from .screens import short_swing
    from .sources.edgar import EdgarClient

    zips = sorted(Path(args.dir).glob("*.zip"))
    rows = short_swing.screen(zips, as_of=date.fromisoformat(args.as_of), min_profit=args.min_profit)
    client = EdgarClient()
    forms: dict[str, str | None] = {}
    for r in rows:
        if r["issuer_cik"] not in forms:
            forms[r["issuer_cik"]] = short_swing.annual_report_form(client, r["issuer_cik"])
        r["annual_report_form"] = forms[r["issuer_cik"]]
    # Foreign private issuers are exempt from Section 16 (Rule 3a12-3(b)).
    rows = [r for r in rows if r["annual_report_form"] not in ("20-F", "40-F")]
    with open(args.out, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    print(f"candidates: {len(rows)}", dict(collections.Counter(r["class"] for r in rows)))
    return 0


def cmd_blm_check(args) -> int:
    from datetime import date

    from .sources import blm
    from .text import to_text

    client = blm.MlrsClient()
    due = blm.deadline(args.assessment_year)
    as_of = date.fromisoformat(args.as_of)
    counts: collections.Counter = collections.Counter()
    with open(args.out, "w") as out:
        for name in args.files:
            text = to_text(Path(name).read_text(errors="replace"))
            for row in blm.check(text, client, due, as_of, limit=args.limit):
                counts[row["status"]] += 1
                out.write(json.dumps({"file": name, **row}) + "\n")
    print(dict(counts))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="error_detection")
    sub = p.add_subparsers(dest="cmd", required=True)

    fetch = sub.add_parser("edgar-fetch", help="download filings matching an EDGAR full-text search")
    fetch.add_argument("--q", required=True, help='full-text query, e.g. \'"U.S. Patent No."\'')
    fetch.add_argument("--forms", help="comma-separated filing forms, e.g. 10-K,10-Q")
    fetch.add_argument("--start", help="YYYY-MM-DD")
    fetch.add_argument("--end", help="YYYY-MM-DD")
    fetch.add_argument("--exhibit", help="file type prefix to keep, e.g. 10-K or EX-96")
    fetch.add_argument("--limit", type=int, default=200)
    fetch.add_argument("--out", required=True)
    fetch.set_defaults(fn=cmd_edgar_fetch)

    pj = sub.add_parser("patents-join", help="check patents cited in filings against USPTO status")
    pj.add_argument("--dir", required=True, help="directory of filings with manifest.jsonl (from edgar-fetch)")
    pj.add_argument("--out", required=True)
    pj.set_defaults(fn=cmd_patents_join)

    ss = sub.add_parser("short-swing", help="Section 16(b) screen over SEC Form 3/4/5 data-set zips")
    ss.add_argument("--dir", required=True, help="directory of quarterly form345 zips")
    ss.add_argument("--out", required=True)
    ss.add_argument("--as-of", required=True, help="YYYY-MM-DD; pairs realized more than two years earlier are dropped")
    ss.add_argument("--min-profit", type=float, default=50_000)
    ss.set_defaults(fn=cmd_short_swing)

    bc = sub.add_parser("blm-check", help="check mining-claim serials cited in filings against BLM records")
    bc.add_argument("files", nargs="+")
    bc.add_argument("--out", required=True)
    bc.add_argument("--assessment-year", type=int, required=True,
                    help="assessment year ending September 1 of this year (its fee is due the September 1 before)")
    bc.add_argument("--as-of", required=True, help="YYYY-MM-DD")
    bc.add_argument("--limit", type=int, help="check at most this many serials per file")
    bc.set_defaults(fn=cmd_blm_check)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
