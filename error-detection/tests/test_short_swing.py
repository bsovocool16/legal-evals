import io
import zipfile
from datetime import date

from error_detection.screens.short_swing import (
    flag_price_outliers,
    Trade,
    families,
    load,
    match,
    norm_security,
    screen,
    within_window,
)


def tr(code, day, shares, price, security="common stock", owners=("1",), acc="a", flags=(), filed=None):
    return Trade(accession=acc, issuer_cik="9", owners=owners, day=date.fromisoformat(day), code=code, shares=shares,
                 price=price, security=security, form="4", filing_date=date.fromisoformat(filed) if filed else None,
                 flags=set(flags))


def test_window_is_less_than_six_months():
    # Morales v. Reading & Bates, 392 F. Supp. 41, 45: a sale on April 17 matches a purchase through October 15.
    assert within_window(date(1972, 4, 17), date(1972, 10, 15))
    assert not within_window(date(1972, 4, 17), date(1972, 10, 16))
    assert not within_window(date(2025, 8, 14), date(2026, 2, 13))
    assert within_window(date(2025, 8, 31), date(2026, 2, 26))
    assert not within_window(date(2025, 8, 31), date(2026, 2, 27))


def test_purchase_then_sale_in_window():
    m = match([tr("P", "2025-01-10", 100, 10), tr("S", "2025-03-01", 100, 12)])
    assert m["profit"] == 200


def test_sale_then_purchase_counts_too():
    m = match([tr("S", "2025-01-10", 100, 12), tr("P", "2025-03-01", 100, 10)])
    assert m["profit"] == 200


def test_lowest_in_highest_out():
    # Pairing 10->19 (900) beats pairing 10->12 and 14->19 (700).
    ts = [tr("P", "2025-01-02", 100, 10), tr("P", "2025-01-03", 100, 14),
          tr("S", "2025-02-01", 100, 19), tr("S", "2025-02-02", 100, 12)]
    assert match(ts)["profit"] == 900


def test_negative_outside_window():
    assert match([tr("P", "2025-01-01", 100, 10), tr("S", "2025-06-30", 100, 12)])["profit"] == 0


def test_negative_sale_below_purchase():
    assert match([tr("P", "2025-01-01", 100, 12), tr("S", "2025-02-01", 100, 10)])["profit"] == 0


def test_negative_different_security():
    ts = [tr("P", "2025-01-01", 100, 10, "class a common stock"), tr("S", "2025-02-01", 100, 12, "class b common stock")]
    assert match(ts)["profit"] == 0


def test_negative_price_ratio_held_out_as_possible_split():
    m = match([tr("P", "2025-01-01", 100, 1), tr("S", "2025-02-01", 100, 30)])
    assert m["profit"] == 0
    assert m["held_out"][0]["reasons"] == ["price_ratio"]


def test_negative_flagged_trade_held_out():
    m = match([tr("P", "2025-01-01", 100, 10, flags={"offering"}), tr("S", "2025-02-01", 100, 12)])
    assert m["profit"] == 0 and m["held_out"][0]["reasons"] == ["offering"]


def test_negative_time_barred_pair():
    m = match([tr("P", "2024-01-01", 100, 10), tr("S", "2024-02-01", 100, 12)], cutoff=date(2024, 10, 4))
    assert m["profit"] == 0


def test_joint_filing_and_amendment_counted_once():
    ts = [tr("P", "2025-01-01", 100, 10, owners=("1", "2"), acc="x", filed="2025-01-03"),
          tr("P", "2025-01-01", 100, 10, owners=("1",), acc="y", filed="2025-02-01"),
          tr("S", "2025-02-01", 100, 12, owners=("2",), acc="z")]
    fams = families(ts)
    assert len(fams) == 1
    assert match(next(iter(fams.values())))["profit"] == 200


def test_norm_security():
    assert norm_security("Common Stock, par value $0.001 per share") == "common stock"
    assert norm_security("Class A Common Stock") == "class a common stock"
    assert norm_security("Ordinary Shares") == "common stock"


def _zip(trans, footnotes="", owners=None):
    sub = ("ACCESSION_NUMBER\tFILING_DATE\tPERIOD_OF_REPORT\tDATE_OF_ORIG_SUB\tNO_SECURITIES_OWNED\tNOT_SUBJECT_SEC16\t"
           "FORM3_HOLDINGS_REPORTED\tFORM4_TRANS_REPORTED\tDOCUMENT_TYPE\tISSUERCIK\tISSUERNAME\tISSUERTRADINGSYMBOL\tREMARKS\tAFF10B5ONE\n"
           "A1\t05-JAN-2025\t\t\t\t\t\t\t4\t0000000009\tACME INC\tACME\t\t0\n"
           "A2\t05-FEB-2025\t\t\t\t\t\t\t4\t0000000009\tACME INC\tACME\t\t0\n")
    own = "ACCESSION_NUMBER\tRPTOWNERCIK\tRPTOWNERNAME\tRPTOWNER_RELATIONSHIP\tRPTOWNER_TITLE\n" + (owners or
          "A1\t0000000001\tDOE JANE\tDirector\t\nA2\t0000000001\tDOE JANE\tDirector\t\n")
    head = ("ACCESSION_NUMBER\tNONDERIV_TRANS_SK\tSECURITY_TITLE\tSECURITY_TITLE_FN\tTRANS_DATE\tTRANS_DATE_FN\tTRANS_CODE\t"
            "TRANS_TIMELINESS\tTRANS_SHARES\tTRANS_SHARES_FN\tTRANS_PRICEPERSHARE\tTRANS_PRICEPERSHARE_FN\tTRANS_ACQUIRED_DISP_CD\n")
    fn = "ACCESSION_NUMBER\tFOOTNOTE_ID\tFOOTNOTE_TXT\n" + footnotes
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("SUBMISSION.tsv", sub)
        zf.writestr("REPORTINGOWNER.tsv", own)
        zf.writestr("NONDERIV_TRANS.tsv", head + trans)
        zf.writestr("FOOTNOTES.tsv", fn)
    return buf


def test_load_and_screen_end_to_end(tmp_path):
    trans = ("A1\t1\tCommon Stock\t\t02-JAN-2025\t\tP\t\t10000\t\t10.00\t\n"
             "A2\t2\tCommon Stock\t\t03-FEB-2025\t\tS\t\t10000\t\t17.00\t\n"
             "A2\t3\tCommon Stock\t\t03-FEB-2025\t\tA\t\t500\t\t0\t\n")
    p = tmp_path / "q.zip"
    p.write_bytes(_zip(trans).getvalue())
    _, trades = load([p])
    assert [t.code for t in trades] == ["P", "S"]
    res = screen([p], as_of=date(2026, 10, 4), min_profit=50_000)
    assert len(res) == 1 and res[0]["profit"] == 70_000 and res[0]["class"] == "officer_director"


def test_negative_foreign_currency_footnote(tmp_path):
    trans = ("A1\t1\tCommon Stock\t\t02-JAN-2025\t\tP\t\t10000\t\t10.00\tF1\n"
             "A2\t2\tCommon Stock\t\t03-FEB-2025\t\tS\t\t10000\t\t17.00\tF1\n")
    p = tmp_path / "q.zip"
    p.write_bytes(_zip(trans, "A1\tF1\tPrices are in New Taiwan Dollars.\nA2\tF1\tPrices are in New Taiwan Dollars.\n").getvalue())
    assert screen([p], as_of=date(2026, 10, 4)) == []


def test_negative_disgorgement_footnote_marks_family(tmp_path):
    trans = ("A1\t1\tCommon Stock\t\t02-JAN-2025\t\tP\t\t10000\t\t10.00\t\n"
             "A2\t2\tCommon Stock\t\t03-FEB-2025\t\tS\t\t10000\t\t17.00\tF1\n")
    p = tmp_path / "q.zip"
    p.write_bytes(_zip(trans, "A2\tF1\tThe reporting person has agreed to disgorge any short-swing profit to the Company.\n").getvalue())
    assert screen([p], as_of=date(2026, 10, 4)) == []


def test_amendment_with_corrected_price_replaces_original():
    ts = [tr("P", "2025-08-14", 3000, 89.5, acc="orig", filed="2025-08-15"),
          tr("P", "2025-08-14", 3000, 88.5, acc="amend", filed="2025-08-20"),
          tr("S", "2025-09-01", 3000, 100)]
    ts[1].form = "4/A"
    fam = next(iter(families(ts).values()))
    assert [t.price for t in fam if t.code == "P"] == [88.5]
    assert match(fam)["profit"] == 3000 * 11.5


def test_negative_same_day_transfer_between_own_accounts():
    ts = [tr("S", "2026-05-22", 61059, 8.63, acc="t"), tr("P", "2026-05-22", 61059, 8.63, acc="t"),
          tr("P", "2026-06-22", 61059, 4.96, acc="u")]
    fam = next(iter(families(ts).values()))
    assert match(fam)["profit"] == 0


def test_negative_withholding_coded_as_sale(tmp_path):
    trans = ("A1\t1\tCommon Stock\t\t02-JAN-2025\t\tP\t\t10000\t\t10.00\t\n"
             "A2\t2\tCommon Stock\t\t03-FEB-2025\t\tS\t\t10000\t\t17.00\tF2\n")
    p = tmp_path / "q.zip"
    note = "A2\tF2\tThese shares were retained by Issuer via settlement on a net withholding basis. None of these shares were sold in an open market transaction.\n"
    p.write_bytes(_zip(trans, note).getvalue())
    assert screen([p], as_of=date(2026, 10, 4)) == []


def test_negative_purchase_footnoted_as_sold(tmp_path):
    trans = ("A1\t1\tCommon Stock\t\t02-JAN-2025\t\tP\t\t10000\t\t10.00\tF1\n"
             "A2\t2\tCommon Stock\t\t03-FEB-2025\t\tS\t\t10000\t\t17.00\t\n")
    p = tmp_path / "q.zip"
    p.write_bytes(_zip(trans, "A1\tF1\tThese shares were sold pursuant to a Rule 10b5-1(c) trading plan.\n").getvalue())
    assert screen([p], as_of=date(2026, 10, 4)) == []


def test_negative_price_entered_as_total():
    ts = [tr("P", "2025-02-03", 12000, 28920.0), tr("S", "2025-02-03", 10714, 29356.36),
          tr("S", "2025-01-10", 500, 2.9), tr("P", "2025-01-20", 500, 2.7), tr("S", "2025-03-01", 500, 3.1)]
    flag_price_outliers(ts)
    assert "price_outlier" in ts[0].flags and "price_outlier" in ts[1].flags
    assert match(ts[:2])["profit"] == 0


def test_negative_purchase_code_with_disposed_flag(tmp_path):
    trans = ("A1\t1\tCommon Stock\t\t02-JAN-2025\t\tP\t\t10000\t\t10.00\t\tD\n"
             "A2\t2\tCommon Stock\t\t03-FEB-2025\t\tS\t\t10000\t\t17.00\t\tD\n")
    p = tmp_path / "q.zip"
    p.write_bytes(_zip(trans).getvalue())
    assert screen([p], as_of=date(2026, 10, 4)) == []
