"""Unpatented mining claims cited in filings, checked against the BLM Mineral and Land Records System (MLRS).

The holder of an unpatented mining claim on federal land must pay a claim maintenance fee for each claim on or before
September 1 of each year (30 U.S.C. 28f(a)); the BLM records the payment against the assessment year that begins that
day. Failure to pay "shall conclusively constitute a forfeiture" of the claim, which "shall be deemed null and void by
operation of law" (30 U.S.C. 28i; see 43 C.F.R. 3830.91). A claimant who, with related parties, holds no more than ten
claims may instead certify for a small-miner waiver, and a defective waiver can be cured within 60 days of notice
(30 U.S.C. 28f(d)). A filing that describes claims as held after the BLM
record shows no payment can therefore overstate what the issuer owns.

This module extracts claim serial numbers from a filing and reads their status from two public BLM sources:

- the ArcGIS layers BLM_Natl_MLRS_Mining_Claims_Not_Closed and _Closed (serial, legacy serial, name, disposition and the
  MLRS record id). Neither layer has claimant or payment status, and the Closed layer covers only a rolling window of
  recently modified records, so a serial that is not found there is not evidence that a claim is closed;
- the MLRS public case record (case status and next payment due date), read through the site's guest Aura endpoint.

Precision rules (from a first test on 21 issuers, in which both apparent lapses turned out to be relocations):

1. A serial not found in GIS is "unknown", never "closed".
2. No claim is judged before the payment deadline plus a posting delay (15 days by default; mailed payments post late).
3. "no_payment_recorded" is a candidate for review, not a finding. Before calling a lapse, a reviewer must look for
   relocations of the same ground by the claimant, its optionees or affiliates in the prior 18 months, and for a
   small-miner waiver. Neither check is automated here.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import time
from collections.abc import Iterable

# Legacy state serials (NMC1234567, AMC 123456, ...) and current MLRS serials (NV101234567, AZ 105012345, ...).
SERIAL_RE = re.compile(
    r"\b((?:NMC|AMC|IMC|MMC|CAMC|UMC|ORMC|WMC|WYMC|NMMC|CMC)\s?-?\d{5,8}"
    r"|(?:NV|AZ|ID|MT|CA|UT|OR|WY|NM|CO|AK|WA|SD)\s?10\d{7,8})\b"
)
MLRS_SERIAL = re.compile(r"^[A-Z]{2}10\d{7,8}$")

GIS_URL = "https://gis.blm.gov/nlsdb/rest/services/HUB/BLM_Natl_MLRS_Mining_Claims_{layer}/FeatureServer/0/query"
GIS_FIELDS = "CSE_NR,LEG_CSE_NR,CSE_NAME,CSE_DISP,SF_ID,Modified,Created"
AURA_URL = "https://mlrs.blm.gov/s/sfsites/aura?r=1&other.x=1"
# The site's public framework build id. It changes when the platform is updated; re-read it from the page source if
# calls start failing.
FWUID = "WUdfaXlIZDNDQ0lZLWNFZDMtVGZ3d2tVMjdnTGFERUU2S3FfSVdrcU92bkExNC4xOTIuODM4ODYwOA"
CASE_FIELDS = ["Name", "Case_Name__c", "Case_Status__c", "Disposition_Date__c", "Next_Payment_Due_Date__c",
               "Date_of_Location__c", "Lead_File_Number__c", "Legacy_Serial_Number__c", "Case_Type__c",
               "Geographic_State_Code__c", "LastModifiedDate"]


def normalize(serial: str) -> str:
    return serial.upper().replace(" ", "").replace("-", "")


def extract_serials(text: str) -> list[str]:
    """Distinct claim serials in the text, normalized (no spaces or hyphens), in order of first appearance."""
    out: dict[str, None] = {}
    for m in SERIAL_RE.finditer(text):
        out.setdefault(normalize(m.group(1)), None)
    return list(out)


def deadline(assessment_year_end: int) -> dt.date:
    """The payment deadline for the assessment year ending September 1 of the given year (due the September 1 before)."""
    return dt.date(assessment_year_end - 1, 9, 1)


def payment_status(found_in_gis: bool, case: dict | None, due: dt.date, as_of: dt.date, posting_days: int = 15) -> str:
    """Status of one claim for the payment due on `due`, judged on `as_of`.

    Returns one of: "too_early" (the deadline plus the posting delay has not passed), "unknown" (not found in GIS, no case
    record, or no next payment date), "closed_in_record" (the case record itself says closed), "paid" (the next payment
    due date is after `due`), "no_payment_recorded" (a candidate for review; see the module docstring).
    """
    if as_of < due + dt.timedelta(days=posting_days):
        return "too_early"
    if not found_in_gis or not case:
        return "unknown"
    status = str(case.get("Case_Status__c") or "").lower()
    if "closed" in status or "void" in status:
        return "closed_in_record"
    nxt = case.get("Next_Payment_Due_Date__c")
    if not nxt:
        return "unknown"
    try:
        nxt_date = dt.date.fromisoformat(str(nxt)[:10])
    except ValueError:
        return "unknown"
    return "paid" if nxt_date > due else "no_payment_recorded"


class MlrsClient:
    """Read-only client for the BLM GIS layers and the MLRS public case records. Keep requests slow."""

    def __init__(self, min_interval: float = 0.5, session=None):
        import requests

        self.session = session or requests.Session()
        self.min_interval = min_interval
        self._last = 0.0

    def _wait(self) -> None:
        gap = self.min_interval - (time.monotonic() - self._last)
        if gap > 0:
            time.sleep(gap)
        self._last = time.monotonic()

    def _gis(self, layer: str, field: str, serials: list[str]) -> list[dict]:
        where = f"{field} IN ({','.join(repr(s) for s in serials)})"
        for attempt in range(4):
            self._wait()
            try:
                r = self.session.post(GIS_URL.format(layer=layer), timeout=120, data={
                    "where": where, "outFields": GIS_FIELDS, "returnGeometry": "false", "f": "json"})
                r.raise_for_status()
                return [f["attributes"] for f in r.json().get("features", [])]
            except Exception:
                if attempt == 3:
                    raise
                time.sleep(2 ** (attempt + 1))
        return []

    def gis_lookup(self, serials: Iterable[str]) -> dict[str, list[dict]]:
        """GIS rows for each serial, from both layers (an empty list means not found, not closed)."""
        serials = [normalize(s) for s in serials]
        out: dict[str, list[dict]] = {s: [] for s in serials}
        current = [s for s in serials if MLRS_SERIAL.match(s)]
        legacy = [s for s in serials if not MLRS_SERIAL.match(s)]
        for layer in ("Not_Closed", "Closed"):
            for field, group in (("CSE_NR", current), ("LEG_CSE_NR", legacy)):
                for k in range(0, len(group), 150):
                    for a in self._gis(layer, field, group[k:k + 150]):
                        key = a["CSE_NR"] if field == "CSE_NR" else a["LEG_CSE_NR"]
                        for d in ("Modified", "Created"):
                            if a.get(d):
                                a[d] = dt.datetime.fromtimestamp(a[d] / 1000, dt.timezone.utc).date().isoformat()
                        out.setdefault(normalize(key or ""), []).append({**a, "layer": layer})
        return out

    def case_record(self, record_id: str) -> dict:
        """The MLRS case record's fields (status, next payment due date, ...), or {"error": ...}."""
        ctx = {"mode": "PROD", "fwuid": FWUID, "app": "siteforce:communityApp",
               "loaded": {"APPLICATION@markup://siteforce:communityApp": "1712_xZHiuQoc1HHcvGz4vs6mGA"},
               "dn": [], "globals": {}, "uad": False}
        msg = {"actions": [{"id": "1;a", "descriptor": "aura://RecordUiController/ACTION$getRecordWithFields",
                            "callingDescriptor": "UNKNOWN",
                            "params": {"recordId": record_id, "fields": ["BLM_Case__c." + f for f in CASE_FIELDS]}}]}
        self._wait()
        r = self.session.post(AURA_URL, timeout=90, data={"message": json.dumps(msg), "aura.context": json.dumps(ctx),
                                                          "aura.pageURI": "/s/", "aura.token": "null"})
        action = r.json()["actions"][0]
        if action.get("state") != "SUCCESS":
            return {"error": str(action.get("error"))[:300]}
        return {k: v.get("value") for k, v in action["returnValue"]["fields"].items()}


def check(text: str, client: MlrsClient, due: dt.date, as_of: dt.date, limit: int | None = None) -> list[dict]:
    """Status of each claim serial cited in `text` (the first `limit` serials, if given)."""
    serials = extract_serials(text)[:limit] if limit else extract_serials(text)
    rows = client.gis_lookup(serials)
    out = []
    for s in serials:
        found = rows.get(s) or []
        record_id = next((a.get("SF_ID") for a in found if a.get("SF_ID")), None)
        case = client.case_record(record_id) if record_id and as_of >= due else None
        if case and "error" in case:
            case = None
        out.append({"serial": s, "gis_layers": sorted({a["layer"] for a in found}),
                    "name": next((a.get("CSE_NAME") for a in found if a.get("CSE_NAME")), None),
                    "case_status": (case or {}).get("Case_Status__c"),
                    "next_payment_due": (case or {}).get("Next_Payment_Due_Date__c"),
                    "status": payment_status(bool(found), case, due, as_of)})
    return out
