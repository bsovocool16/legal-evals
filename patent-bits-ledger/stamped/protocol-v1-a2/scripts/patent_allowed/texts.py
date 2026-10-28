"""Text extraction for the allowed-claims probe: claims documents (with amendment markup), publication and grant
full text, notices of allowance, and word-level claim diffs.

Sources (USPTO Open Data Portal):
- claims documents (CLM) as ST96 XML from the file wrapper; amended listings mark added text with <com:Ins> and
  deleted text with <com:Del> or [[double brackets]] (37 C.F.R. 1.121(c));
- pre-grant publication and grant full text, per-document XML from the bulk products APPXML-SPLT and PTGRXML-SPLT
  (<p num="0045"> paragraphs, <claim num="1"> claims, <abstract>);
- office action and notice of allowance text from the Office Action Text Retrieval API (bodyText).
"""

from __future__ import annotations

import difflib
import html
import re

# ----------------------------------------------------------------------------- ST96 claims documents

_BLOCK = re.compile(r"</?(?:uspat:ClaimText|uscom:P|uscom:LI|uscom:Br|com:Br|br|uscom:Heading)\b[^>]*/?>")
_DEL = re.compile(r"<(com:Del|com:S|uscom:S|com:Strike)\b[^>]*>.*?</\1>", re.S)


def inline_text(fragment: str) -> str:
    """Clean text of an ST96 fragment: deletions dropped, block boundaries become spaces, inline tags (insertion
    marks, OCR confidence wrappers, bold, underline) are removed without adding spaces, so words split by OCR markup
    are rejoined."""
    f = re.sub(r"<\?[^>]*\?>", "", fragment)
    f = _DEL.sub("", f)
    f = _BLOCK.sub(" ", f)
    f = re.sub(r"<[^>]+>", "", f)
    f = html.unescape(f)
    f = re.sub(r"\[\[[^\]]*\]\]", "", f)
    return re.sub(r"\s+", " ", f).strip()


def inserted_spans(fragment: str) -> list[str]:
    """Text marked as inserted (<com:Ins>), adjacent marks merged (OCR wrappers often split one insertion)."""
    f = re.sub(r"<\?[^>]*\?>", "", fragment)
    f = _DEL.sub("\x03", f)
    f = _BLOCK.sub(" ", f)
    f = f.replace("<com:Ins>", "\x01").replace("</com:Ins>", "\x02")
    f = re.sub(r"<[^>]+>", "", f)
    f = html.unescape(f)
    out, cur, inside = [], [], False
    for ch in f:
        if ch == "\x01":
            inside = True
        elif ch == "\x02":
            inside = False
        elif ch == "\x03":
            continue
        elif inside:
            cur.append(ch)
        elif ch.strip():  # visible text outside an insertion ends the span
            if cur:
                out.append("".join(cur))
                cur = []
        elif cur:
            cur.append(ch)
    if cur:
        out.append("".join(cur))
    spans = [re.sub(r"\s+", " ", s).strip() for s in out]
    return [s for s in spans if s]


_STATUS = re.compile(r"^\s*\d+\s*[.)]?\s*\(\s*([A-Za-z ,\-]{3,40}?)\s*\)\s*")


def repair_references(xml: str) -> str:
    """Some renderings put the referring claim's preamble inside <uspat:ClaimReference> instead of the parent's
    number; rebuild 'claim N' from the element's idrefs attribute (the parent claim's com:id) when the element
    holds no digit."""
    ids = {m.group(1): m.group(2) for m in re.finditer(
        r'<uspat:Claim\b[^>]*com:id="([^"]+)"[^>]*>\s*<pat:ClaimNumber>\s*(\d+)\s*</pat:ClaimNumber>', xml)}

    def sub(m):
        inner = m.group(2)
        if re.search(r"\d", re.sub(r"<[^>]+>", "", inner)):
            return m.group(0)
        nums = [ids[i] for i in m.group(1).split() if i in ids]
        return "claim " + " or ".join(nums) + " " if nums else m.group(0)
    return re.sub(r'<uspat:ClaimReference\b[^>]*com:idrefs="([^"]+)"[^>]*>(.*?)</uspat:ClaimReference>', sub, xml,
                  flags=re.S)


def parse_claims(xml: str) -> list[dict] | None:
    """Claims of an ST96 ClaimsDocument: number, status, clean text (claim number and status prefix removed),
    inserted spans, and the claims it refers to. None if a claim has no number or numbers repeat."""
    xml = repair_references(xml)
    claims = []
    for m in re.finditer(r"<uspat:Claim\b[^>]*>(.*?)</uspat:Claim>", xml, re.S):
        body = m.group(1)
        num = re.search(r"<pat:ClaimNumber>\s*(\d+)\s*</pat:ClaimNumber>", body)
        if not num:
            return None
        n = int(num.group(1))
        st = re.search(r"<uspat:ClaimStatusCategory>\s*([^<]*?)\s*</uspat:ClaimStatusCategory>", body)
        status = re.sub(r"[^a-z ]", "", (st.group(1) if st else "").lower()).strip()
        body = re.sub(r"<uspat:ClaimStatusCategory>.*?</uspat:ClaimStatusCategory>", "", body, flags=re.S)
        body = re.sub(r"<pat:ClaimNumber>.*?</pat:ClaimNumber>", "", body, flags=re.S)
        text = inline_text(body)
        sm = _STATUS.match(text)
        if sm:
            if not status:
                status = sm.group(1).lower().strip()
            text = text[sm.end():]
        else:
            text = re.sub(r"^\s*\d+\s*[.)]\s*", "", text)
        status = status or "original"
        refs = sorted({int(x) for x in re.findall(r"\bclaims?\s*(\d+)", text[:300], re.I)} - {n})
        claims.append({"number": n, "status": status, "text": text.strip(), "inserted": inserted_spans(body),
                       "refers_to": refs})
    if not claims:
        return None
    nums = [c["number"] for c in claims]
    if len(set(nums)) != len(nums):
        return None
    return sorted(claims, key=lambda c: c["number"])


def is_active(c: dict) -> bool:
    s = c.get("status", "")
    return not any(w in s for w in ("cancel", "withdrawn", "deleted", "not entered")) and \
        not re.fullmatch(r"\W*(?:cancel+ed|canceled|withdrawn)\W*", c.get("text", ""), re.I) and \
        len(c.get("text", "")) > 15


def independent(claims: list[dict]) -> list[int]:
    return [c["number"] for c in claims if is_active(c) and not c["refers_to"]]


# ----------------------------------------------------------------------------- publication / grant full text

def _t(fragment: str) -> str:
    f = re.sub(r"<\?[^>]*\?>", "", fragment)
    f = re.sub(r"<(?:maths|math)\b.*?</(?:maths|math)>", " [formula] ", f, flags=re.S)
    f = re.sub(r"<(?:figref|b|i|u|sub|sup|smallcaps|o)\b[^>]*>|</(?:figref|b|i|u|sub|sup|smallcaps|o)>", "", f)
    f = re.sub(r"<[^>]+>", " ", f)
    return re.sub(r"\s+", " ", html.unescape(f)).strip()


def fulltext_parts(xml: str) -> dict:
    """Abstract, numbered description paragraphs and headings, and claims of a publication or grant XML."""
    ab = re.search(r"<abstract\b[^>]*>(.*?)</abstract>", xml, re.S)
    desc = re.search(r"<description\b[^>]*>(.*?)</description>", xml, re.S)
    items = []
    if desc:
        for m in re.finditer(r"<heading\b[^>]*>(.*?)</heading>|<p\b([^>]*)>(.*?)</p>", desc.group(1), re.S):
            if m.group(1) is not None:
                t = _t(m.group(1))
                if t:
                    items.append(("h", "", t))
            else:
                num = re.search(r'num="(\d+)"', m.group(2))
                body = m.group(3)
                if "<table" in body:
                    rows = []
                    for r in re.findall(r"<row\b[^>]*>(.*?)</row>", body, re.S):
                        cells = [_t(c) for c in re.findall(r"<entry\b[^>]*>(.*?)</entry>", r, re.S)]
                        if any(cells):
                            rows.append("| " + " | ".join(cells) + " |")
                    body = re.sub(r"<tables\b.*?</tables>|<table\b.*?</table>", " ", body, flags=re.S)
                    t = (_t(body) + "\n" + "\n".join(rows)).strip()
                else:
                    t = _t(body)
                if t:
                    items.append(("p", num.group(1) if num else "", t))
    claims = [(int(m.group(1)), re.sub(r"^\d+\s*\.\s*", "", _t(m.group(2))))
              for m in re.finditer(r"<claim\b[^>]*num=\"0*(\d+)\"[^>]*>(.*?)</claim>", xml, re.S)]
    title = re.search(r"<invention-title\b[^>]*>(.*?)</invention-title>", xml, re.S)
    return {"abstract": _t(ab.group(1)) if ab else "", "items": items, "claims": claims,
            "title": _t(title.group(1)) if title else ""}


def description_md(parts: dict) -> str:
    out = []
    for kind, num, t in parts["items"]:
        out.append(f"\n## {t}\n" if kind == "h" else f"[{num}] {t}\n" if num else f"{t}\n")
    return "\n".join(out).strip() + "\n"


def paragraphs(parts: dict) -> dict[str, str]:
    return {num: t for kind, num, t in parts["items"] if kind == "p" and num}


# ----------------------------------------------------------------------------- notices of allowance

_RFA = re.compile(r"(?:statement\s+of\s+)?reasons?\s+for\s+(?:the\s+indication\s+of\s+)?allowa(?:nce|ble)", re.I)
_RFA_END = re.compile(r"Any comments considered necessary by applicant|Conclusion\b|Any inquiry concerning this "
                      r"communication", re.I)


_ASM = re.compile(r"Allowable Subject Matter", re.I)


def reasons_for_allowance(noa: str) -> str | None:
    """The examiner's statement of reasons for allowance (37 C.F.R. 1.104(e)), or None. Taken from the text after
    'reasons for allowance'; failing that, from an 'Allowable Subject Matter' heading."""
    t = noa.replace("\r", "")
    m = None
    for cand in _RFA.finditer(t):
        m = cand
        if re.match(r"[\s:.]*", t[cand.end():cand.end() + 3]):
            break
    if not m:
        m = _ASM.search(t)
    if not m:
        return None
    rest = t[m.end():]
    e = _RFA_END.search(rest)
    body = rest[:e.start()] if e else rest[:6000]
    body = re.sub(r"[ \t]+", " ", body).strip(" :.\n")
    return body if len(body) > 30 else None


_EXA = re.compile(r"EXAMINER[’']?S\s+AMENDMENT", re.I)


def examiners_amendment(noa: str) -> str | None:
    """Text of an examiner's amendment section in a notice of allowance, or None."""
    m = _EXA.search(noa)
    if not m:
        return None
    rest = noa[m.end():]
    e = re.search(r"(?:Allowable Subject Matter|REASONS? FOR ALLOWANCE|statement of reasons for allowance|"
                  r"Allowance\s*\n|The following is an examiner)", rest, re.I)
    body = rest[:e.start()] if e else rest[:8000]
    body = re.sub(r"[ \t]+", " ", body).strip()
    return body if len(body) > 30 else None


# ----------------------------------------------------------------------------- diffs

_TOK = re.compile(r"\w+(?:[.\-/]\w+)*|[^\w\s]")


def words(text: str) -> list[str]:
    return _TOK.findall(text)


def _norm_word(w: str) -> str:
    return w.lower()


def added_spans(old: str, new: str) -> list[str]:
    """Word-level insertions (and replacement text) in `new` relative to `old`, as text spans."""
    a, b = words(old), words(new)
    sm = difflib.SequenceMatcher(a=[_norm_word(w) for w in a], b=[_norm_word(w) for w in b], autojunk=False)
    out = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op in ("insert", "replace") and j2 > j1:
            out.append(join_words(b[j1:j2]))
    return [s for s in out if re.search(r"\w", s)]


def deleted_spans(old: str, new: str) -> list[str]:
    a, b = words(old), words(new)
    sm = difflib.SequenceMatcher(a=[_norm_word(w) for w in a], b=[_norm_word(w) for w in b], autojunk=False)
    return [join_words(a[i1:i2]) for op, i1, i2, j1, j2 in sm.get_opcodes()
            if op in ("delete", "replace") and i2 > i1 and re.search(r"\w", join_words(a[i1:i2]))]


def join_words(ws: list[str]) -> str:
    s = " ".join(ws)
    s = re.sub(r"\s+([,.;:)\]])", r"\1", s)
    s = re.sub(r"([(\[])\s+", r"\1", s)
    return s
