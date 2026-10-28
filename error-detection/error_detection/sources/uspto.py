"""USPTO Open Data Portal client for patent status lookups.

Needs a USPTO Open Data Portal API key in the USPTO_API_KEY environment
variable. Responses are cached on disk so reruns are free.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import requests

SEARCH_URL = "https://api.uspto.gov/api/v1/patent/applications/search"
DEFAULT_CACHE = Path("data/cache/uspto")


def _load_key() -> str | None:
    return os.environ.get("USPTO_API_KEY") or None


class UsptoClient:
    def __init__(self, api_key: str | None = None, cache_dir: Path = DEFAULT_CACHE, min_interval: float = 1.0):
        self.key = api_key or _load_key()
        if not self.key:
            raise RuntimeError("Set USPTO_API_KEY to a USPTO Open Data Portal API key.")
        self.cache = Path(cache_dir)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.min_interval = min_interval
        self._last = 0.0
        self.session = requests.Session()
        self.session.headers["X-API-KEY"] = self.key

    def patent(self, number: str) -> dict | None:
        """The file-wrapper record for a granted patent number (digits only, or RE/D prefix)."""
        path = self.cache / f"{number}.json"
        if path.exists():
            data = json.loads(path.read_text())
        else:
            for attempt in range(4):
                wait = self.min_interval - (time.monotonic() - self._last)
                if wait > 0:
                    time.sleep(wait)
                self._last = time.monotonic()
                resp = self.session.get(SEARCH_URL, params={"q": f"applicationMetaData.patentNumber:{number}", "limit": 1}, timeout=60)
                if resp.status_code in (429, 500, 502, 503, 504) and attempt < 3:
                    time.sleep(2 ** (attempt + 2))
                    continue
                if resp.status_code == 404:
                    data = {"count": 0}
                    break
                resp.raise_for_status()
                data = resp.json()
                break
            path.write_text(json.dumps(data))
        bag = data.get("patentFileWrapperDataBag") or []
        return bag[0] if bag else None


def status(record: dict) -> dict:
    """Plain status facts from a file-wrapper record."""
    meta = record.get("applicationMetaData", {})
    events = record.get("eventDataBag", []) or []
    text = meta.get("applicationStatusDescriptionText") or ""
    lapse_events = sorted((e["eventDate"] for e in events if e.get("eventCode") == "EXP."), reverse=True)
    assignees = []
    for a in sorted(record.get("assignmentBag", []) or [], key=lambda a: a.get("assignmentRecordedDate") or ""):
        for who in a.get("assigneeBag", []) or []:
            name = who.get("assigneeNameText")
            if name:
                assignees.append((a.get("assignmentRecordedDate"), name, a.get("conveyanceText")))
    return {
        "patent_number": meta.get("patentNumber"),
        "title": meta.get("inventionTitle"),
        "status": text,
        "status_date": meta.get("applicationStatusDate"),
        "grant_date": meta.get("grantDate"),
        "filing_date": meta.get("filingDate"),
        "lapsed_for_nonpayment": "nonpayment" in text.lower().replace(" ", ""),
        "lapse_dates": lapse_events,
        "first_applicant": meta.get("firstApplicantName"),
        "latest_assignee": assignees[-1][1] if assignees else None,
        "assignment_history": assignees,
    }
