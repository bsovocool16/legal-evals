"""Section 16(b) short-swing screen over the SEC Form 3/4/5 data sets.

Section 16(b) (15 U.S.C. §78p(b)) makes a director, officer, or more-than-10%
holder pay the issuer any profit from a purchase and a sale (or a sale and a
purchase) of the issuer's equity securities within any period of less than six
months. Courts compute the profit by matching the lowest-priced purchases with
the highest-priced sales inside the window, in either order (Smolowe v. Delendo
Corp., 136 F.2d 231 (2d Cir. 1943); Gratz v. Claughton, 187 F.2d 46 (2d Cir.
1951)). Suit must be brought within two years after the profit was realized.

Data: https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets
(quarterly zips of SUBMISSION, REPORTINGOWNER, NONDERIV_TRANS, FOOTNOTES).

Precision rules:
- Only purchases and sales coded P and S in the nonderivative table. Grants,
  exercises, tax withholding and dispositions to the issuer (A, M, F, D) are
  usually exempt under Rule 16b-3 and are skipped. Derivative trades are not
  screened yet.
- A purchase and a sale match only if they are the same security title.
- Pairs whose prices differ by more than MAX_PRICE_RATIO are held out: an
  unadjusted stock split between the two trades produces exactly that pattern.
- Trades whose footnotes mention a non-US currency, an issuer offering or plan
  (possible Rule 16b-3(c)/(d) exemption), or a disgorgement are held out.
- Owners who are only 10% holders are reported separately. The purchase that
  makes a holder a 10% owner does not count (Foremost-McKesson, Inc. v.
  Provident Securities Co., 423 U.S. 232 (1976)), and the data sets do not show
  holdings at both ends reliably.
- Joint filings (one report for several owners) are grouped into one family
  so the same trades are counted once; a Form 4/A replaces the trade it amends.
- A same-day sale and purchase of the same shares at the same price in one
  filing is treated as a transfer between the insider's own accounts.
- Rows whose own footnotes contradict the code (a "purchase" of shares that
  "were sold"; a "sale" that was tax withholding retained by the issuer) are
  held out as coding errors.
- Foreign private issuers are exempt from Section 16 (Rule 3a12-3(b)); the
  data sets do not mark them, so callers must drop 20-F/40-F filers.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

MAX_PRICE_RATIO = 2.0

CURRENCY_RE = re.compile(
    r"New Taiwan|NT\$|Canadian dollars?|\bC\$|\bCAD\b|Australian dollars?|\bA\$|\bAUD\b|pounds? sterling|\bGBP\b|£|"
    r"\beuros?\b|\bEUR\b|€|\byen\b|\bJPY\b|\bRMB\b|renminbi|\bHK\$|Hong Kong dollars?|Swiss francs?|\bCHF\b|"
    r"\bNIS\b|shekels?|\bSEK\b|kronor|rupees?|\bINR\b|pence|\bGBX\b",
    re.I,
)
OFFERING_RE = re.compile(
    r"public offering|underwritten|private placement|registered direct|purchased (?:directly )?from the (?:issuer|company)|"
    r"securities purchase agreement|subscription agreement|rights offering|employee stock purchase plan|\bESPP\b|"
    r"dividend reinvestment|\bDRIP\b|in connection with the (?:offering|IPO|initial public offering)|concurrent private",
    re.I,
)
DISGORGE_RE = re.compile(r"disgorg|16\(b\)|short[- ]swing|remit\w*\s.{0,60}profit|pa(?:y|id)\s.{0,40}to the (?:company|issuer)\s.{0,60}profit", re.I)
# A purchase whose own footnote says the shares were sold, or a "sale" that was
# really tax withholding by the issuer, is a coding error in the Form 4.
SOLD_RE = re.compile(r"\b(?:were|was|being) sold\b|sold pursuant", re.I)
WITHHELD_RE = re.compile(r"retained by (?:the )?issuer|withheld by (?:the )?(?:issuer|company)|not sold in (?:an |the )?open market|net withholding basis", re.I)
PAR_RE = re.compile(r",?\s*(?:\(.*?\)|\$?\s*[\d.]+\s*)?\s*par value.*$", re.I)


def norm_security(title: str) -> str:
    t = PAR_RE.sub("", title or "").lower()
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    t = re.sub(r"\bshares?\b", "stock", t)
    t = re.sub(r"\bordinary\b", "common", t)
    return " ".join(t.split())


def parse_date(s: str) -> date | None:
    for fmt in ("%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            continue
    return None


def add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    y, m = d.year + y, m + 1
    last = [31, 29 if y % 4 == 0 and (y % 100 or y % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1]
    return date(y, m, min(d.day, last))


def within_window(a: date, b: date) -> bool:
    """True if the two dates fall within a period of less than six months.

    The period ends at midnight two days before the corresponding date in the
    sixth month (Stella v. Graham-Paige Motors Corp., 232 F.2d 299 (2d Cir.
    1956), as applied in Morales v. Reading & Bates Offshore Drilling Co., 392
    F. Supp. 41, 45 (N.D. Okla. 1975): a sale on April 17 matches a purchase
    through October 15). Where the corresponding date does not exist (August 31
    to February), the last day of the month is used; that choice is untested.
    """
    lo, hi = min(a, b), max(a, b)
    return hi <= add_months(lo, 6) - timedelta(days=2)


@dataclass
class Trade:
    accession: str
    issuer_cik: str
    owners: tuple[str, ...]
    day: date
    code: str
    shares: float
    price: float
    security: str
    form: str
    filing_date: date | None
    late: bool = False
    notes: str = ""
    flags: set[str] = field(default_factory=set)


@dataclass
class Filing:
    issuer_cik: str
    issuer_name: str
    symbol: str
    form: str
    filing_date: date | None
    remarks: str
    owners: list[dict] = field(default_factory=list)


def _rows(zf: zipfile.ZipFile, name: str):
    with zf.open(name) as fh:
        yield from csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8", errors="replace"), delimiter="\t", quoting=csv.QUOTE_NONE)


def load(zips: list[Path]) -> tuple[dict[str, Filing], list[Trade]]:
    """Read P and S nonderivative trades and their filings from data-set zips."""
    filings: dict[str, Filing] = {}
    trades: list[Trade] = []
    for path in zips:
        with zipfile.ZipFile(path) as zf:
            raw = [r for r in _rows(zf, "NONDERIV_TRANS.tsv") if r["TRANS_CODE"] in ("P", "S")]
            wanted = {r["ACCESSION_NUMBER"] for r in raw}
            for r in _rows(zf, "SUBMISSION.tsv"):
                acc = r["ACCESSION_NUMBER"]
                if acc in wanted:
                    filings[acc] = Filing(
                        issuer_cik=r["ISSUERCIK"].lstrip("0"), issuer_name=r["ISSUERNAME"], symbol=r["ISSUERTRADINGSYMBOL"],
                        form=r["DOCUMENT_TYPE"], filing_date=parse_date(r["FILING_DATE"]), remarks=r.get("REMARKS") or "",
                    )
            for r in _rows(zf, "REPORTINGOWNER.tsv"):
                f = filings.get(r["ACCESSION_NUMBER"])
                if f is not None:
                    f.owners.append({"cik": r["RPTOWNERCIK"].lstrip("0"), "name": r["RPTOWNERNAME"],
                                     "relationship": r["RPTOWNER_RELATIONSHIP"], "title": r.get("RPTOWNER_TITLE") or ""})
            notes: dict[tuple[str, str], str] = {}
            for r in _rows(zf, "FOOTNOTES.tsv"):
                if r["ACCESSION_NUMBER"] in wanted:
                    notes[(r["ACCESSION_NUMBER"], r["FOOTNOTE_ID"])] = r["FOOTNOTE_TXT"] or ""
            for r in raw:
                acc = r["ACCESSION_NUMBER"]
                f = filings.get(acc)
                day = parse_date(r["TRANS_DATE"])
                try:
                    shares, price = float(r["TRANS_SHARES"] or 0), float(r["TRANS_PRICEPERSHARE"] or 0)
                except ValueError:
                    continue
                if f is None or day is None or shares <= 0 or price <= 0:
                    continue
                ids = set()
                for k, v in r.items():
                    if k.endswith("_FN") and v:
                        ids.update(x.strip() for x in v.split(","))
                text = " ".join(notes.get((acc, i), "") for i in sorted(ids))
                t = Trade(
                    accession=acc, issuer_cik=f.issuer_cik, owners=tuple(sorted(o["cik"] for o in f.owners)), day=day,
                    code=r["TRANS_CODE"], shares=shares, price=price, security=norm_security(r["SECURITY_TITLE"]),
                    form=f.form, filing_date=f.filing_date, late=r.get("TRANS_TIMELINESS") == "L", notes=text,
                )
                blob = text + " " + f.remarks
                if CURRENCY_RE.search(blob):
                    t.flags.add("currency")
                if t.code == "P" and OFFERING_RE.search(blob):
                    t.flags.add("offering")
                if (t.code == "P" and SOLD_RE.search(text)) or (t.code == "S" and WITHHELD_RE.search(text)):
                    t.flags.add("code_conflict")
                ad = (r.get("TRANS_ACQUIRED_DISP_CD") or "").strip()
                if ad and ad != {"P": "A", "S": "D"}[t.code]:
                    t.flags.add("code_conflict")
                if DISGORGE_RE.search(blob):
                    t.flags.add("disgorgement")
                trades.append(t)
    return filings, trades


def families(trades: list[Trade]) -> dict[tuple[str, str], list[Trade]]:
    """Group trades by issuer and owner family (owners linked by joint filings)."""
    parent: dict[tuple[str, str], tuple[str, str]] = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for t in trades:
        keys = [(t.issuer_cik, o) for o in t.owners]
        for k in keys[1:]:
            parent[find(k)] = find(keys[0])
    groups: dict[tuple[str, str], list[Trade]] = defaultdict(list)
    for t in trades:
        if t.owners:
            groups[find((t.issuer_cik, t.owners[0]))].append(t)
    out = {}
    for key, ts in groups.items():
        # Joint filings repeat the same trade; keep the latest report of each.
        # A 4/A replaces the earlier report of the same trade even if it corrects the price.
        seen: dict[tuple, Trade] = {}
        for t in sorted(ts, key=lambda t: (t.filing_date or date.min, t.accession)):
            if t.form.endswith("/A"):
                for k in [k for k in seen if k[:4] == (t.day, t.code, round(t.shares, 4), t.security)]:
                    del seen[k]
            seen[(t.day, t.code, round(t.shares, 4), t.security, round(t.price, 6))] = t
        kept = list(seen.values())
        # A sale and a purchase of the same shares at the same price on the same day in
        # one filing is a transfer between the insider's own accounts, not a trade.
        by_acc: dict[tuple, list[Trade]] = defaultdict(list)
        for t in kept:
            by_acc[(t.accession, t.day, round(t.shares, 4), round(t.price, 4))].append(t)
        drop = {id(t) for grp in by_acc.values() if {x.code for x in grp} == {"P", "S"} for t in grp}
        out[key] = sorted((t for t in kept if id(t) not in drop), key=lambda t: t.day)
    return out


def match(trades: list[Trade], cutoff: date | None = None, max_ratio: float = MAX_PRICE_RATIO) -> dict:
    """Maximum-profit matching of purchases and sales within six months.

    Pairs are taken in order of price spread, highest first, and shares are
    allocated greedily. Only pairs whose later trade is on or after `cutoff`
    (the limitations date) count. Pairs with a price ratio above `max_ratio`
    or with a flagged trade are held out and reported separately.
    """
    buys = [t for t in trades if t.code == "P"]
    sells = [t for t in trades if t.code == "S"]
    pairs, held = [], []
    for b in buys:
        for s in sells:
            if s.security != b.security or s.price <= b.price or not within_window(b.day, s.day):
                continue
            if cutoff and max(b.day, s.day) < cutoff:
                continue
            reasons = sorted(b.flags | s.flags)
            if s.price / b.price > max_ratio:
                reasons.append("price_ratio")
            (held if reasons else pairs).append((s.price - b.price, b, s, reasons))
    left = {id(t): t.shares for t in buys + sells}
    matched, profit = [], 0.0
    for spread, b, s, _ in sorted(pairs, key=lambda p: -p[0]):
        n = min(left[id(b)], left[id(s)])
        if n <= 0:
            continue
        left[id(b)] -= n
        left[id(s)] -= n
        profit += n * spread
        matched.append({"buy": b, "sell": s, "shares": n, "spread": spread})
    held_profit = 0.0
    for spread, b, s, reasons in held:
        held_profit = max(held_profit, min(b.shares, s.shares) * spread)
    return {
        "profit": profit,
        "pairs": matched,
        "held_out": [{"buy": b, "sell": s, "spread": sp, "reasons": r} for sp, b, s, r in held],
        "held_out_max_pair_profit": held_profit,
    }


def _filing_url(cik: str, accession: str) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/"


def flag_price_outliers(trades: list[Trade], factor: float = 10.0) -> None:
    """Flag trades priced far from the median for the same issuer and security.

    A per-share price entered as a total, or in the wrong units, shows up as a
    price many times the security's usual level.
    """
    by_sec: dict[tuple[str, str], list[float]] = defaultdict(list)
    for t in trades:
        by_sec[(t.issuer_cik, t.security)].append(t.price)
    med = {k: sorted(v)[len(v) // 2] for k, v in by_sec.items()}
    for t in trades:
        m = med[(t.issuer_cik, t.security)]
        if len(by_sec[(t.issuer_cik, t.security)]) >= 3 and (t.price > m * factor or t.price < m / factor):
            t.flags.add("price_outlier")


def screen(zips: list[Path], as_of: date, min_profit: float = 50_000) -> list[dict]:
    """Candidates with matched profit of at least `min_profit`, largest first."""
    filings, trades = load(zips)
    flag_price_outliers(trades)
    cutoff = date(as_of.year - 2, as_of.month, as_of.day)
    owners: dict[tuple[str, str], dict] = {}
    for f in filings.values():
        for o in f.owners:
            rec = owners.setdefault((f.issuer_cik, o["cik"]), {"name": o["name"], "relationships": set(), "titles": set()})
            rec["relationships"].update(x for x in o["relationship"].split(",") if x)
            if o["title"]:
                rec["titles"].add(o["title"])
    out = []
    for (issuer, _), ts in families(trades).items():
        if not any(t.code == "P" for t in ts) or not any(t.code == "S" for t in ts):
            continue
        m = match(ts, cutoff=cutoff)
        if not m["pairs"] or m["profit"] < min_profit:
            continue
        fam = sorted({o for t in ts for o in t.owners})
        rels = set().union(*(owners[(issuer, o)]["relationships"] for o in fam))
        insider = bool(rels & {"Director", "Officer"})
        f0 = filings[ts[-1].accession]
        out.append({
            "issuer_cik": issuer,
            "issuer": f0.issuer_name,
            "symbol": f0.symbol,
            "owners": [owners[(issuer, o)]["name"] for o in fam],
            "relationships": sorted(rels),
            "titles": sorted(set().union(*(owners[(issuer, o)]["titles"] for o in fam))),
            "class": "officer_director" if insider else ("ten_percent_only" if "TenPercentOwner" in rels else "other"),
            "profit": round(m["profit"], 2),
            "n_pairs": len(m["pairs"]),
            "first": min(min(p["buy"].day, p["sell"].day) for p in m["pairs"]).isoformat(),
            "realized_last": max(max(p["buy"].day, p["sell"].day) for p in m["pairs"]).isoformat(),
            "late_reports": sum(t.late for t in ts),
            "disgorgement_mentioned": any("disgorgement" in t.flags for t in ts),
            "held_out_reasons": sorted({r for h in m["held_out"] for r in h["reasons"]}),
            "pairs": [
                {"buy_date": p["buy"].day.isoformat(), "buy_price": p["buy"].price, "sell_date": p["sell"].day.isoformat(),
                 "sell_price": p["sell"].price, "shares": p["shares"], "security": p["buy"].security,
                 "buy_filing": _filing_url(issuer, p["buy"].accession), "sell_filing": _filing_url(issuer, p["sell"].accession)}
                for p in sorted(m["pairs"], key=lambda p: -p["shares"] * p["spread"])[:10]
            ],
        })
    return sorted(out, key=lambda r: -r["profit"])


def annual_report_form(client, cik: str) -> str | None:
    """The most recent annual report form type the issuer filed (10-K, 20-F, 40-F, N-CSR)."""
    import json as _json

    data = _json.loads(client.fetch(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json"))
    for form in data.get("filings", {}).get("recent", {}).get("form", []):
        if form in ("10-K", "20-F", "40-F", "N-CSR", "10-K/A", "20-F/A", "40-F/A"):
            return form.removesuffix("/A")
    return None
