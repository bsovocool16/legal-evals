from datetime import date

from error_detection.cli import build_parser
from error_detection.sources.blm import check, deadline, extract_serials, payment_status

DUE = deadline(2027)          # fee for the assessment year ending 2027-09-01, due 2026-09-01
LATE = date(2026, 10, 1)      # well after the posting delay


def test_deadline():
    assert DUE == date(2026, 9, 1)


def test_extract_legacy_and_mlrs_serials():
    text = ("The project comprises 42 unpatented lode claims (NMC1234567 through NMC1234608), claims NV 101234567 "
            "and NV101234568, and the AMC-123456 claim group. NMC1234567 is also listed in Schedule A.")
    assert extract_serials(text) == ["NMC1234567", "NMC1234608", "NV101234567", "NV101234568", "AMC123456"]


def test_extract_negatives():
    # too short, wrong prefix, an MLRS serial without the 10 prefix, part of a longer token, a phone number
    text = "NMC1234 and XYZ1234567 and NV1234567 and ABCNMC1234567X and call (775) 101-2345678."
    assert extract_serials(text) == []


def test_paid_claim():
    assert payment_status(True, {"Case_Status__c": "Active", "Next_Payment_Due_Date__c": "2027-09-01"}, DUE, LATE) == "paid"


def test_unpaid_claim_is_a_candidate_not_a_finding():
    case = {"Case_Status__c": "Active", "Next_Payment_Due_Date__c": "2026-09-01"}
    assert payment_status(True, case, DUE, LATE) == "no_payment_recorded"


def test_negative_not_found_in_gis_is_unknown_never_closed():
    assert payment_status(False, None, DUE, LATE) == "unknown"
    assert payment_status(False, {"Next_Payment_Due_Date__c": "2026-09-01"}, DUE, LATE) == "unknown"


def test_negative_too_early_before_payments_post():
    case = {"Case_Status__c": "Active", "Next_Payment_Due_Date__c": "2026-09-01"}
    assert payment_status(True, case, DUE, date(2026, 9, 10)) == "too_early"
    assert payment_status(True, case, DUE, date(2026, 9, 16)) == "no_payment_recorded"


def test_negative_missing_or_malformed_due_date_is_unknown():
    assert payment_status(True, {"Case_Status__c": "Active"}, DUE, LATE) == "unknown"
    assert payment_status(True, {"Next_Payment_Due_Date__c": "n/a"}, DUE, LATE) == "unknown"


def test_closed_in_record():
    case = {"Case_Status__c": "Closed", "Next_Payment_Due_Date__c": "2026-09-01"}
    assert payment_status(True, case, DUE, LATE) == "closed_in_record"


class FakeClient:
    def __init__(self, gis, cases):
        self.gis, self.cases, self.case_calls = gis, cases, []

    def gis_lookup(self, serials):
        return {s: self.gis.get(s, []) for s in serials}

    def case_record(self, record_id):
        self.case_calls.append(record_id)
        return self.cases[record_id]


def test_check_end_to_end_with_a_fake_client():
    gis = {"NV101234567": [{"layer": "Not_Closed", "SF_ID": "a1", "CSE_NAME": "BLUE 1"}],
           "NV101234568": [{"layer": "Not_Closed", "SF_ID": "a2", "CSE_NAME": "BLUE 2"}]}
    cases = {"a1": {"Case_Status__c": "Active", "Next_Payment_Due_Date__c": "2027-09-01"},
             "a2": {"Case_Status__c": "Active", "Next_Payment_Due_Date__c": "2026-09-01"}}
    rows = check("Claims NV101234567, NV101234568 and NV101234569.", FakeClient(gis, cases), DUE, LATE)
    assert [(r["serial"], r["status"]) for r in rows] == [
        ("NV101234567", "paid"), ("NV101234568", "no_payment_recorded"), ("NV101234569", "unknown")]


def test_negative_check_before_deadline_reads_no_case_records():
    gis = {"NV101234568": [{"layer": "Not_Closed", "SF_ID": "a2"}]}
    client = FakeClient(gis, {"a2": {"Next_Payment_Due_Date__c": "2026-09-01"}})
    rows = check("Claim NV101234568.", client, DUE, date(2026, 8, 1))
    assert rows[0]["status"] == "too_early" and client.case_calls == []


def test_cli_parser_knows_every_command():
    p = build_parser()
    for argv in (["edgar-fetch", "--q", "x", "--out", "d"], ["patents-join", "--dir", "d", "--out", "f"],
                 ["short-swing", "--dir", "d", "--out", "f", "--as-of", "2026-10-01"],
                 ["blm-check", "a.htm", "--out", "f", "--assessment-year", "2027", "--as-of", "2026-10-01"]):
        assert callable(p.parse_args(argv).fn)
