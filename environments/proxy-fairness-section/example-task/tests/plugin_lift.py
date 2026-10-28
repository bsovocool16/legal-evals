"""Section segmentation and board-book page classification, adapted from the author's earlier fairness-opinion drafting
plugin. Standard library only.

Comments and docstrings were removed from this published copy; the code is unchanged.
"""
import re
from analyses import HEAD, VALUATION, DECK_RX, REF, NUMERIC, ANALYSES, presented
BACK = re.compile(r"^(?:general|miscellaneous)\b|^the preparation of a (?:fairness|financial) opinion|complex (?:analytical )?process|^fees?\b|engagement letter|for (?:its|their) services|is entitled to", re.I)
SUMMARY = re.compile(r"summary of (?:the )?(?:material )?(?:financial )?analys|the following is a (?:brief )?summary", re.I)
def paragraphs(text): return [p for p in re.split(r"\n\s*\n|\n", text) if p.strip()]
def heading_of(p):
    s = p.strip()
    if s.endswith("?"): return None
    if len(s) <= 95:
        for label, rx in HEAD:
            if re.search(rx, s, re.I): return label
    m = re.match(r"^\s*([^.:\n—]{4,80})[.:—]", s)
    if m:
        for label, rx in HEAD:
            if re.search(rx, m.group(1), re.I) and not re.search(r"\b(?:performed|reviewed|calculated|compared|applied|analy[sz]ed|noted)\b", m.group(1), re.I): return label
    return None
def segment(text):
    ps = paragraphs(text); n = len(ps)
    summ = next((i for i, p in enumerate(ps) if SUMMARY.search(p)), None)
    starts = [(i, l) for i, l in ((i, heading_of(p)) for i, p in enumerate(ps)) if l and (summ is None or i > summ)]
    if not starts: return text, [], ""
    first = starts[0][0]
    back_i = next((i for i in range(first + 1, n) if BACK.search(ps[i].strip()[:160]) and heading_of(ps[i]) in (None, "other") and i > starts[-1][0]), n)
    segs = []
    for k, (i, l) in enumerate(starts):
        j = starts[k + 1][0] if k + 1 < len(starts) else back_i
        if i >= back_i: break
        segs.append((l, "\n\n".join(ps[i:min(j, back_i)])))
    return "\n\n".join(ps[:first]), segs, "\n\n".join(ps[back_i:])
REFERENCE_BY_NATURE = {"trading_range", "targets"}
def pages(text): return [p for p in text.split("-----PAGE-----") if p.strip()]
def classify(text):
    out = {}
    for i, p in enumerate(pages(text)):
        title = "\n".join(p.strip().split("\n")[:3]).lower(); body = p.lower()
        for key, rx in DECK_RX.items():
            hit_title = re.search(rx, title, re.I); hit_body = re.search(rx, body, re.I) and NUMERIC.search(p)
            if hit_title or hit_body:
                status = "grid" if key == "sensitivity" else ("reference" if (REF.search(p) or key in REFERENCE_BY_NATURE) else "core"); rec = out.setdefault(key, {"status": status, "pages": [], "title_hit": False})
                rec["pages"].append(i + 1); rec["title_hit"] = rec["title_hit"] or bool(hit_title)
                if status == "core": rec["status"] = "core"
    return out
SUFFIX = r"(?:inc\.?|corp\.?|corporation|co\.?|company|ltd\.?|limited|plc|llc|l\.l\.c\.|lp|l\.p\.|holdings?|group|partners|securities|capital|markets|s\.a\.|ag|n\.v\.)"
LEGAL = re.compile(r"(?:,\s*|\s+)(?:inc\.?|corp\.?|corporation|co\.?|company|ltd\.?|limited|plc|llc|l\.l\.c\.|lp|l\.p\.|s\.a\.|n\.v\.|ag|se|holdings?|group|trust|partners|bancorp|incorporated)\.?\s*$", re.I)
META = re.compile(r"drafting basis|this (benchmark )?draft|drafting assumption|not (been )?invented|\[insert|placeholder|the supplied (facts|presentation|deck|book|text|inputs)|benchmark draft|source (conflict|issue)|unrecoverable|not available in the (book|deck|inputs)|\bTODO\b|\[\[", re.I)
MARK = re.compile(r"\[\[(?:BORROWED|NOT IN THIS BOOK)[^\]]*\]\]"); REVIEWER = re.compile(r"\[REVIEWER:[^\]]*\]")
def mask(text, names):
    t = text or ""
    for n in sorted(set(names), key=len, reverse=True):
        t = re.sub(re.escape(n), " NAME ", t, flags=re.I); short = re.sub(r"[,.]", "", n).split()
        if short and len(short[0]) > 3 and short[0].lower() not in ("the", "company", "parent"): t = re.sub(r"\b" + re.escape(short[0]) + r"\b", " NAME ", t)
    t = re.sub(r"\b(?:[A-Z][\w&'’.-]*\s+){0,3}[A-Z][\w&'’.-]*,?\s+" + SUFFIX + r"\b", " NAME ", t); return re.sub(r"\d[\d,\.]*", "#", t)
def sh(text, n): toks = re.findall(r"[a-z#']+|NAME", text); return set(" ".join(toks[i:i + n]) for i in range(len(toks) - n + 1))
def m6(text, names): return sh(re.sub(r"[A-Z]", lambda c: c.group(0).lower(), mask(text, names)).replace("name", "NAME"), 6)
def cov(a, b): return round(len(a & b) / len(a), 3) if a else None
def tables(text):
    tabs, cur = [], []
    for l in text.split("\n"):
        if l.strip().startswith("|"):
            if re.match(r"^\|(\s*:?-+:?\s*\|)+\s*$", l.strip()): continue
            cur.append([c.strip() for c in l.strip().strip("|").split("|")])
        elif cur: tabs.append(cur); cur = []
    if cur: tabs.append(cur)
    return tabs
