"""SEC EDGAR access: full-text search for exhibits, and document download.

SEC fair-access rules: identify yourself in the User-Agent ("Name email") and
stay under 10 requests per second. Set SEC_USER_AGENT before use.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterator

import requests

EFTS_URL = "https://efts.sec.gov/LATEST/search-index"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{adsh}/{filename}"
PAGE_SIZE = 100


@dataclass
class Hit:
    adsh: str
    cik: str
    filename: str
    file_type: str
    form: str
    file_date: str
    company: str
    description: str
    items: list = field(default_factory=list)       # 8-K item numbers, e.g. ["5.07", "9.01"]
    inc_states: list = field(default_factory=list)  # state(s) of incorporation, e.g. ["DE"]

    @property
    def url(self) -> str:
        return ARCHIVE_URL.format(cik=int(self.cik), adsh=self.adsh.replace("-", ""), filename=self.filename)

    @property
    def key(self) -> str:
        return f"{self.adsh}_{self.filename}"


def hits_from_response(payload: dict) -> list[Hit]:
    out = []
    for h in payload.get("hits", {}).get("hits", []):
        src = h.get("_source", {})
        _id = h.get("_id", "")
        adsh, _, filename = _id.partition(":")
        ciks = src.get("ciks") or [""]
        names = src.get("display_names") or [""]
        out.append(Hit(
            adsh=src.get("adsh") or adsh,
            cik=ciks[0],
            filename=filename,
            file_type=src.get("file_type", ""),
            form=src.get("form", ""),
            file_date=src.get("file_date", ""),
            company=names[0],
            description=src.get("file_description", "") or "",
            items=list(src.get("items") or []),
            inc_states=list(src.get("inc_states") or []),
        ))
    return out


def total_from_response(payload: dict) -> int:
    total = payload.get("hits", {}).get("total", {})
    return total.get("value", 0) if isinstance(total, dict) else int(total or 0)


class EdgarClient:
    def __init__(self, user_agent: str | None = None, min_interval: float = 0.12, session: requests.Session | None = None):
        ua = user_agent or os.environ.get("SEC_USER_AGENT")
        if not ua:
            raise RuntimeError(
                "SEC requires a User-Agent naming you and an email address. "
                'Set SEC_USER_AGENT, e.g. export SEC_USER_AGENT="Jane Doe jane@example.com".'
            )
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": ua, "Accept-Encoding": "gzip, deflate"})
        self.min_interval = min_interval
        self._last = 0.0

    def _get(self, url: str, params: dict | None = None, retries: int = 4) -> requests.Response:
        for attempt in range(retries + 1):
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            resp = self.session.get(url, params=params, timeout=60)
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(2 ** (attempt + 1))
                continue
            resp.raise_for_status()
            return resp
        raise RuntimeError("unreachable")

    def search(self, q: str, forms: list[str] | None = None, start: str | None = None,
               end: str | None = None, offset: int = 0) -> dict:
        params = {"q": q, "from": offset}
        if forms:
            params["forms"] = ",".join(forms)
        if start or end:
            params.update({"dateRange": "custom", "startdt": start or "2001-01-01", "enddt": end or time.strftime("%Y-%m-%d")})
        return self._get(EFTS_URL, params=params).json()

    def iter_hits(self, q: str, forms: list[str] | None = None, start: str | None = None,
                  end: str | None = None, file_type_prefix: str | None = None,
                  limit: int = 200) -> Iterator[Hit]:
        seen = set()
        offset = 0
        yielded = 0
        while yielded < limit:
            payload = self.search(q, forms=forms, start=start, end=end, offset=offset)
            page = hits_from_response(payload)
            if not page:
                return
            for hit in page:
                if hit.key in seen:
                    continue
                seen.add(hit.key)
                if file_type_prefix and not hit.file_type.upper().startswith(file_type_prefix.upper()):
                    continue
                yield hit
                yielded += 1
                if yielded >= limit:
                    return
            offset += PAGE_SIZE
            if offset >= total_from_response(payload) or offset >= 10_000:
                return

    def fetch(self, url: str) -> str:
        resp = self._get(url)
        return resp.content.decode(resp.encoding or "utf-8", errors="replace")


def download(client: EdgarClient, hits: Iterator[Hit], out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    meta_path = out_dir / "manifest.jsonl"
    saved = []
    with meta_path.open("a") as meta:
        for hit in hits:
            dest = out_dir / hit.key
            if not dest.exists():
                dest.write_text(client.fetch(hit.url))
                meta.write(json.dumps({**asdict(hit), "url": hit.url, "path": dest.name}) + "\n")
            saved.append(dest)
    return saved
