from error_detection.joins.patents import cited_patents, normalize
from error_detection.sources.uspto import status


def nums(text):
    return [p["number"] for p in cited_patents(text)]


def test_single_and_list_citations():
    text = ("Our lead product is covered by U.S. Patent No. 9,123,456, which expires in 2031, "
            "and by U.S. Patent Nos. 10,234,567, 10,345,678 and 11,456,789.")
    assert nums(text) == ["9123456", "10234567", "10345678", "11456789"]


def test_reissue_design_and_plain_digits():
    assert nums("U.S. Pat. No. RE49,947 and United States Patent Number 10234567") == ["RE49947", "10234567"]
    assert nums("U.S. Design Patent No. D987,654") == ["D987654"]
    assert nums("U.S. Patent No. D987,654") == ["D987654"]


def test_foreign_and_application_numbers_excluded():
    text = ("European Patent No. 3,123,456 and Japanese Patent No. 6,123,456 were granted. "
            "U.S. Patent Application No. 16/123,456 is pending.")
    assert nums(text) == []


def test_out_of_range_numbers_rejected():
    assert normalize("1,234") is None
    assert normalize("99,999,999") is None
    assert normalize("12,345,678") == "12345678"


def test_status_flags_lapse():
    record = {
        "applicationMetaData": {
            "patentNumber": "9123456",
            "applicationStatusDescriptionText": "Patent Expired Due to NonPayment of Maintenance Fees Under 37 CFR 1.362",
            "applicationStatusDate": "2025-09-01",
        },
        "eventDataBag": [{"eventCode": "EXP.", "eventDate": "2025-09-01", "eventDescriptionText": "Expire Patent"}],
        "assignmentBag": [{"assignmentRecordedDate": "2015-03-10", "assigneeBag": [{"assigneeNameText": "ACME CORP"}], "conveyanceText": "ASSIGNMENT"}],
    }
    s = status(record)
    assert s["lapsed_for_nonpayment"] and s["lapse_dates"] == ["2025-09-01"] and s["latest_assignee"] == "ACME CORP"
