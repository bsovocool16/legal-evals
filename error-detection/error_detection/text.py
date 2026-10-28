"""Turn filed documents (mostly EDGAR HTML) into plain text that keeps paragraph breaks.

Checks rely on line starts to find section headings, so block-level tags become
newlines and inline tags are dropped.
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser

BLOCK_TAGS = {
    "p", "div", "br", "tr", "td", "th", "li", "h1", "h2", "h3", "h4", "h5", "h6",
    "table", "ul", "ol", "center", "blockquote", "pre", "hr", "page", "title",
}
SKIP_TAGS = {"script", "style", "head"}


STRIKE_TAGS = {"s", "strike", "del"}
VOID_TAGS = {"br", "hr", "img", "meta", "link", "input", "col", "area", "base", "wbr"}


def _is_strike(tag: str, attrs) -> bool:
    if tag in STRIKE_TAGS:
        return True
    for name, value in attrs:
        if name == "style" and value and "line-through" in value.replace(" ", "").lower():
            return True
    return False


class _Extractor(HTMLParser):
    """Collects text, dropping scripts, styles, and struck-through (deleted) text.

    Conformed copies filed with amendments show deletions as strikethrough next
    to the inserted text ("fiveseven"); keeping both corrupts every check.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0
        self._strike: list[list] = []  # [tag, nesting depth of that tag inside the struck element]

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self._skip += 1
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")
        if tag in VOID_TAGS:
            return
        if self._strike and self._strike[-1][0] == tag:
            self._strike[-1][1] += 1
        elif _is_strike(tag, attrs):
            self._strike.append([tag, 1])

    def handle_startendtag(self, tag, attrs):
        if tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")
        if self._strike and self._strike[-1][0] == tag:
            self._strike[-1][1] -= 1
            if self._strike[-1][1] == 0:
                self._strike.pop()
        elif tag in BLOCK_TAGS:
            # An unclosed inline strike must not swallow the rest of the document.
            while self._strike and self._strike[-1][0] not in BLOCK_TAGS:
                self._strike.pop()

    def handle_data(self, data):
        if not self._skip and not self._strike:
            self.parts.append(data)


_WS = re.compile(r"[ \t\r\f\v    ​]+")
_BLANKS = re.compile(r"\n\s*\n+")


def html_to_text(raw: str) -> str:
    parser = _Extractor()
    parser.feed(raw)
    parser.close()
    return normalize("".join(parser.parts))


def normalize(text: str) -> str:
    text = html.unescape(text)
    text = (
        text.replace("‘", "'").replace("’", "'")
        .replace("“", '"').replace("”", '"')
        .replace("–", "-").replace("—", "-").replace("‑", "-")
    )
    lines = [_WS.sub(" ", line).strip() for line in text.split("\n")]
    return _BLANKS.sub("\n\n", "\n".join(lines)).strip()


def looks_like_html(raw: str) -> bool:
    head = raw[:2000].lower()
    return "<html" in head or "<body" in head or "<p" in head or "<div" in head or "<document" in head


def to_text(raw: str) -> str:
    return html_to_text(raw) if looks_like_html(raw) else normalize(raw)
