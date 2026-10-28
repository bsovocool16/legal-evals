"""Small USPTO Open Data Portal client for the patent-examination probes.

Endpoints used (all need the X-API-KEY header; the key is read from the
USPTO_API_KEY environment variable and never written anywhere):

- Office Action Text Retrieval API, POST
  https://api.uspto.gov/api/v1/patent/oa/oa_actions/v1/records
  (form fields: criteria, start, rows). Fields used: patentApplicationNumber,
  legacyDocumentCodeIdentifier, submissionDate, bodyText, techCenter,
  groupArtUnitNumber, inventionTitle, inventionSubjectMatterCategory,
  applicationTypeCategory, filingDate, obsoleteDocumentIdentifier.
- Office Action Rejection API, POST
  https://api.uspto.gov/api/v1/patent/oa/oa_rejections/v2/records.
  Fields used: obsoleteDocumentIdentifier, actionTypeCategory, legalSectionCode,
  paragraphNumber, claimNumberArrayDocument, hasRejDP.
- Patent file wrapper documents, GET
  https://api.uspto.gov/api/v1/patent/applications/{app}/documents.
  Fields used: documentCode, officialDate, documentIdentifier, downloadOptionBag.
- Document download (XML archive), GET the downloadUrl whose mimeTypeIdentifier
  is XML (".../download/applications/{app}/{doc}/xmlarchive"); a tar file.

Responses are cached under data/cache/odp/ (not published).
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import tarfile
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "cache" / "odp"
OA_URL = "https://api.uspto.gov/api/v1/patent/oa/{api}/records"
DOCS_URL = "https://api.uspto.gov/api/v1/patent/applications/{app}/documents"


def load_key() -> str:
    key = os.environ.get("USPTO_API_KEY")
    if not key:
        raise RuntimeError("Set USPTO_API_KEY to a USPTO Open Data Portal API key.")
    return key


class Odp:
    def __init__(self, cache: Path = CACHE, min_interval: float = 0.6):
        self.cache = Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.min_interval = min_interval
        self._last = 0.0
        self.session = requests.Session()
        self.session.headers["X-API-KEY"] = load_key()

    def _wait(self):
        gap = self.min_interval - (time.monotonic() - self._last)
        if gap > 0:
            time.sleep(gap)
        self._last = time.monotonic()

    def _request(self, method: str, url: str, **kw) -> requests.Response:
        for attempt in range(5):
            self._wait()
            try:
                r = self.session.request(method, url, timeout=120, **kw)
            except requests.RequestException:
                if attempt == 4:
                    raise
                time.sleep(2 ** (attempt + 1))
                continue
            if r.status_code in (429, 500, 502, 503, 504) and attempt < 4:
                time.sleep(2 ** (attempt + 2))
                continue
            return r
        return r

    def _cached(self, name: str):
        p = self.cache / name
        return json.loads(p.read_text()) if p.exists() else None

    def _store(self, name: str, data):
        p = self.cache / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data))
        return data

    def oa_search(self, criteria: str, start: int = 0, rows: int = 20, api: str = "oa_actions/v1",
                  fields: str | None = None) -> dict:
        h = hashlib.sha1(f"{api}|{criteria}|{start}|{rows}|{fields}".encode()).hexdigest()[:16]
        name = f"oa/{api.replace('/', '_')}_{h}.json"
        got = self._cached(name)
        if got is not None:
            return got
        data = {"criteria": criteria, "start": start, "rows": rows}
        if fields:
            data["fl"] = fields
        r = self._request("POST", OA_URL.format(api=api), data=data)
        r.raise_for_status()
        return self._store(name, r.json())

    def documents(self, app: str) -> list[dict]:
        name = f"docs/{app}.json"
        got = self._cached(name)
        if got is None:
            r = self._request("GET", DOCS_URL.format(app=app))
            if r.status_code == 404:
                got = {"documentBag": []}
            else:
                r.raise_for_status()
                got = r.json()
            self._store(name, got)
        return got.get("documentBag") or []

    def xml_files(self, url: str) -> dict[str, str]:
        """Members of an XML archive download, as {member name: text}."""
        key = hashlib.sha1(url.encode()).hexdigest()[:20]
        p = self.cache / "xml" / f"{key}.tar"
        if not p.exists():
            r = self._request("GET", url)
            r.raise_for_status()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(r.content)
        out = {}
        with tarfile.open(fileobj=io.BytesIO(p.read_bytes())) as tf:
            for m in tf.getmembers():
                if m.isfile() and m.name.lower().endswith(".xml"):
                    out[m.name] = tf.extractfile(m).read().decode("utf-8", errors="replace")
        return out


def xml_url(doc: dict) -> str | None:
    for opt in doc.get("downloadOptionBag") or []:
        if opt.get("mimeTypeIdentifier") == "XML":
            return opt.get("downloadUrl")
    return None
