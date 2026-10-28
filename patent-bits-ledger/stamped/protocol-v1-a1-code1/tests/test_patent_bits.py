import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "patent_bits"))

import bits as B  # noqa: E402


def d(code, date):
    return {"documentCode": code, "officialDate": date + "T00:00:00"}


def cl(n, text, refs=(), status="original", inserted=()):
    return {"number": n, "text": text, "refers_to": list(refs), "status": status, "inserted": list(inserted)}


# ---------------------------------------------------------------- ordering

def test_order_is_deterministic_and_normalizes_numbers():
    apps = ["17/000,001", "17000002", "17000003", "17000004"]
    o1 = B.ordered("P-2026-10-08", "A", apps)
    assert o1 == B.ordered("P-2026-10-08", "A", list(reversed(apps)))
    assert "17000001" in o1 and all(len(a) == 8 for a in o1)
    assert B.ordered("P-2026-10-08", "F", apps) != o1 or len(apps) < 2
    s = B.suffixes("B-A-dev")
    assert len(s) == 1000 and len(set(s)) == 1000 and s == B.suffixes("B-A-dev")


# ---------------------------------------------------------------- timeline

def test_timeline_rules():
    docs = [d("CLM", "2025-01-02"), d("CTNF", "2025-06-01"), d("A...", "2025-09-01"), d("REM", "2025-09-01"),
            d("CLM", "2025-09-01"), d("EXIN", "2025-10-15"), d("NOA", "2025-11-20")]
    assert B.latest_action(docs, "2025-09-01") == ("2025-06-01", "CTNF")
    assert B.response_date(docs, "2025-06-01") == "2025-09-01"
    assert B.first_counted_after(docs, "2025-09-01") == ("2025-11-20", "NOA")
    assert B.any_dated(docs, B.A_PENDING_BLOCK, "2025-09-01", "2025-09-22", lo_inclusive=True) == []
    assert B.any_dated(docs, B.A_PENDING_BLOCK, "2025-09-01", "2025-11-30", lo_inclusive=True) == ["NOA 2025-11-20"]


def test_response_needs_an_amendment_paper():
    docs = [d("CTNF", "2025-06-01"), d("REM", "2025-07-01"), d("EXIN", "2025-07-01"), d("CTFR", "2025-12-01")]
    assert B.response_date(docs, "2025-06-01") is None


def test_restriction_counts_as_latest_action():
    docs = [d("CTNF", "2025-06-01"), d("CTRS", "2025-08-01")]
    assert B.latest_action(docs, "2025-09-01") == ("2025-08-01", "CTRS")


def test_check_sources_refuses_later_material_and_other_documents():
    B.check_sources([("CTNF", "2025-06-01"), ("CLM", "2025-01-02"), ("REM", "2025-09-01")], "2025-09-01")
    with pytest.raises(B.LeakError):
        B.check_sources([("REM", "2025-09-02")], "2025-09-01")
    with pytest.raises(B.LeakError):
        B.check_sources([("REM", "")], "2025-09-01")
    with pytest.raises(B.LeakError):
        B.check_sources([("EXIN", "2025-08-01")], "2025-09-01")          # examiner paper: not a packet source
    with pytest.raises(B.LeakError):
        B.check_sources([("CLM.X", "2025-08-01")], "2025-09-01")


# ---------------------------------------------------------------- claim lists in the action

def test_claim_status_sentences():
    t = ("Claims 1-20 are pending. Claims 7-20 are withdrawn from further consideration. Claims 1-3 and 5 are rejected "
         "under 35 U.S.C. 103. Claim 4 is objected to as being dependent upon a rejected base claim, but would be "
         "allowable if rewritten in independent form including all of the limitations of the base claim and any "
         "intervening claims.")
    assert B.claims_with_status(t, "rejected") == {1, 2, 3, 5}
    assert B.allowable_claims(t) == {4}
    assert B.scored_claims_named(t) == {1, 2, 3, 4, 5}


def test_claim_status_negatives():
    assert B.allowable_claims("Claims 1-10 are rejected under 35 U.S.C. 103.") == set()
    assert B.allowable_claims("Claim 3 is objected to because of the following informalities: a typo.") == set()
    assert B.claims_with_status("Claims 1-20 are pending in this application.", "rejected") == set()


def test_claim_status_serial_comma_and_form_paragraphs():
    assert B.claims_with_status("Claims 1, 2, and 3 are rejected under 35 U.S.C. 103.", "rejected") == {1, 2, 3}
    assert B.claims_with_status("Claim(s) 1-3 is/are rejected under 35 U.S.C. 103", "rejected") == {1, 2, 3}
    assert B.allowable_claims("Claims 6, 7, and 9 are objected to as being dependent upon a rejected base claim, but "
                              "would be allowable if rewritten in independent form.") == {6, 7, 9}
    t = "Claim(s) 14-18 is/are withdrawn from further consideration. Claims 1-18 are rejected."
    assert B.scored_claims_named(t) == set(range(1, 14))


def test_withdrawn_objection_is_not_a_withdrawn_claim():
    t = ("Claims 2-6 are rejected under 35 U.S.C. 103. The objection to claim 5 is withdrawn in view of the "
         "amendment. The rejection under 35 U.S.C. 112(b) of claims 2-6 is withdrawn.")
    assert B.scored_claims_named(t) == {2, 3, 4, 5, 6}


# ---------------------------------------------------------------- parse checks

def test_clean_parse_rejects_merged_claims():
    good = [cl(1, "A device comprising a frame and a hinge."), cl(2, "The device of claim 1, wherein the frame is steel.", [1])]
    assert B.clean_parse(good)
    merged = [cl(1, "A device comprising a frame."), cl(17, "The device of claim 1, wherein the frame is steel. "
                                                           "18. (original) The device of claim 15, wherein x.", [1])]
    assert not B.clean_parse(merged)
    assert not B.clean_parse(None)
    ranged = [cl(1, "1-15. (Cancelled)", status="cancelled"), cl(16, "A device comprising a frame and a hinge.")]
    assert B.clean_parse(ranged)                                       # a cancelled range is not a merge
    junk = B.tidy_claims([cl(1, "SVG18913438.05-06-2026.MOU84.CLM.1.2.svg0.2931.567ChemistryBlack and white 1. "
                                "(Currently Amended) A propulsion system comprising a rotor.")])
    assert junk[0]["text"] == "A propulsion system comprising a rotor." and B.clean_parse(junk)
    assert not B.clean_parse([cl(2, "The device of claim 1, wherein the frame is steel.", [1])])


# ---------------------------------------------------------------- folds

BEFORE = [cl(1, "A separator comprising a porous substrate and a coating layer."),
          cl(2, "The separator of claim 1, wherein the coating layer comprises titanium oxide particles dispersed "
                "in a polyolefin binder.", [1]),
          cl(3, "The separator of claim 1, wherein the porous substrate has a porosity between forty and sixty "
                "percent.", [1]),
          cl(4, "The separator of claim 2, wherein the polyolefin binder is crosslinked titanium oxide coated.", [2])]
NAMED = {1, 2, 3, 4}


def test_scorable_needs_named_active_dependent_with_words():
    assert B.scorable(BEFORE, NAMED) == [2, 3, 4]
    assert B.scorable(BEFORE, {1, 3}) == [3]


def test_fold_cancelled_and_inserted():
    after = [cl(1, "A separator comprising a porous substrate and a coating layer, wherein the coating layer "
                   "comprises titanium oxide particles dispersed in a polyolefin binder.", status="currently amended",
                inserted=["wherein the coating layer comprises titanium oxide particles dispersed in a polyolefin "
                          "binder"]),
             cl(2, "(Cancelled)", status="cancelled"), BEFORE[2], BEFORE[3]]
    assert B.fold_labels(BEFORE, after, [2, 3, 4]) == {2: 1, 3: 0, 4: 0}


def test_kept_dependent_sharing_words_is_not_a_fold():
    # claim 4 shares vocabulary with the inserted text but stays a dependent claim
    after = [cl(1, BEFORE[0]["text"] + " wherein the polyolefin binder is crosslinked titanium oxide coated",
                status="currently amended", inserted=["wherein the polyolefin binder is crosslinked titanium oxide coated"]),
             BEFORE[1], BEFORE[2], BEFORE[3]]
    assert B.fold_labels(BEFORE, after, [2, 3, 4]) == {2: 0, 3: 0, 4: 0}


def test_fold_by_word_diff_without_markup():
    after = [cl(1, "A separator comprising a porous substrate and a coating layer, wherein the porous substrate has "
                   "a porosity between forty and sixty percent.", status="currently amended"),
             BEFORE[1], cl(3, "(Canceled)", status="canceled"), BEFORE[3]]
    assert B.fold_labels(BEFORE, after, [2, 3, 4]) == {2: 0, 3: 1, 4: 0}


def test_rewritten_in_independent_form_is_a_fold():
    after = [BEFORE[0], BEFORE[1],
             cl(3, "A separator comprising a porous substrate and a coating layer, wherein the porous substrate has a "
                   "porosity between forty and sixty percent.", status="currently amended"), BEFORE[3]]
    assert B.fold_labels(BEFORE, after, [2, 3, 4]) == {2: 0, 3: 1, 4: 0}


def test_garbled_reference_is_not_a_rewrite():
    after = [BEFORE[0], BEFORE[1],
             cl(3, "The separator of Claim_1, wherein the porous substrate has a porosity between forty and sixty "
                   "percent.", status="original"), BEFORE[3]]
    assert B.fold_labels(BEFORE, after, [2, 3, 4]) == {2: 0, 3: 0, 4: 0}


def test_fold_counts_words_new_to_the_independent_claim():
    before = [cl(1, "A base station comprising a processor and memory storing instructions."),
              cl(2, "The base station of claim 1, wherein the instructions further cause the base station to receive a "
                    "channel state report periodically and schedule data transmission.", [1])]
    after = [cl(1, "A base station comprising a processor and memory storing instructions, wherein the instructions "
                   "further cause the base station to receive a channel state report periodically and schedule data "
                   "transmission.", status="currently amended",
                inserted=["receive a channel state report periodically and schedule data transmission"]),
             cl(2, "(Cancelled)", status="cancelled")]
    assert B.fold_labels(before, after, [2]) == {2: 1}


def test_own_header_prefix_and_range_cancellation_are_tidied():
    cls = B.tidy_claims([cl(1, "A device comprising a frame and a hinge."),
                         cl(2, "2-5. (Canceled)", status="cancelled"),
                         cl(12, "Claim 12 (original): The device of Claim 1, wherein the frame is steel.", [1])])
    assert [c["number"] for c in cls] == [1, 2, 3, 4, 5, 12]
    assert all(B.is_cancelled(c) for c in cls if 2 <= c["number"] <= 5)
    assert cls[-1]["text"].startswith("The device of Claim 1")
    assert B.dependent_body(cls[-1]["text"]) == "the frame is steel."


def test_partial_fold_and_unchanged_claims_are_not_folds():
    after = [cl(1, "A separator comprising a porous substrate and a coating layer comprising titanium oxide.",
                status="currently amended", inserted=["comprising titanium oxide"]),
             cl(2, "(Cancelled)", status="cancelled"), BEFORE[2], BEFORE[3]]
    assert B.fold_labels(BEFORE, after, [2, 3, 4])[2] == 0
    assert B.fold_labels(BEFORE, BEFORE, [2, 3, 4]) == {2: 0, 3: 0, 4: 0}


# ---------------------------------------------------------------- answers

def test_parse_answer_strict():
    assert B.parse_answer("A", '{"p_allow": 0.3}')["p"] == 0.3
    assert B.parse_answer("A", '```json\n{"p_allow": 0.3}\n```')["p"] == 0.3
    assert B.parse_answer("A", 'I think {"p_allow": 0.3}')["parsed"] is False       # prose around the JSON
    assert B.parse_answer("A", '{"p_allow": "0.3"}')["p"] is None                    # string
    assert B.parse_answer("A", '{"p_allow": 1.2}')["p"] is None                      # out of range
    assert B.parse_answer("A", '{"p_allow": NaN}')["p"] is None
    assert B.parse_answer("F", '{"folds": {"2": NaN, "3": 0.9}}', [2, 3])["p"] == {"2": None, "3": 0.9}
    assert B.parse_answer("A", '{"p_allow": true}')["p"] is None
    r = B.parse_answer("F", '{"folds": {"2": 0.1, "3": 0.9, "9": 0.5}}', [2, 3, 4])
    assert r["p"] == {"2": 0.1, "3": 0.9, "4": None} and r["unlisted_keys"] == ["9"]


# ---------------------------------------------------------------- scoring

def test_bits_and_clipping():
    assert B.bits(0.5, 1) == pytest.approx(1.0)
    assert B.bits(0.25, 0) == pytest.approx(-math.log2(0.75))
    assert B.bits(1.0, 0) == pytest.approx(-math.log2(0.01))
    assert B.bits(None, 1) == pytest.approx(1.0)
    assert B.bits(float("nan"), 0) == pytest.approx(1.0)


def test_log_score_rewards_honesty_in_expectation():
    q = 0.3

    def expected(p):
        return q * B.bits(p, 1) + (1 - q) * B.bits(p, 0)
    assert expected(0.3) < min(expected(0.2), expected(0.4), expected(0.5))
    assert expected(0.3) < expected(None)                 # abstaining costs as much as saying 0.5


def test_cluster_bootstrap():
    units = [("b1", 1.0, 2), ("b1", 0.4, 1), ("b1", 0.9, 3), ("b1", 0.6, 2)]
    est, lo, hi = B.cluster_bootstrap(units, n=500)
    assert est == pytest.approx(2.9 / 8) and lo > 0 and lo <= est <= hi


def test_jeffreys_and_calibration_bins():
    assert B.jeffreys(0, 0) == 0.5 and B.jeffreys(3, 9) == pytest.approx(0.35)
    cal = B.calibration([(0.03, 0), (0.04, 1), (0.5, 1)], B.F_BINS)
    assert cal[0]["bin"] == "0.02-0.05" and cal[0]["n"] == 2


# ---------------------------------------------------------------- outcomes (ledger.py, no network)

sys.path.insert(0, str(ROOT / "scripts" / "patent_probe"))
sys.path.insert(0, str(ROOT / "scripts" / "patent_allowed"))
import ledger as L  # noqa: E402

R = "2026-07-01"
BASE = [d("CTNF", "2026-04-01"), d("A...", R), d("CLM", R), d("REM", R)]


def oa(extra, **kw):
    args = dict(horizon=77, eval_date="2026-09-30", t_commit=None, lag=0, final=True)
    args.update(kw)
    return L.outcome_A(BASE + extra, R, args.pop("horizon"), args.pop("eval_date"), args.pop("t_commit"),
                       args.pop("lag"), final=args.pop("final"))


def test_outcome_A_labels_first_counted_action():
    assert oa([d("NOA", "2026-08-20")])["y"] == 1
    assert oa([d("CTEQ", "2026-08-20")])["y"] == 1
    assert oa([d("CTFR", "2026-08-20"), d("NOA", "2026-09-10")])["y"] == 0
    assert oa([d("EXIN", "2026-07-30"), d("CTNF", "2026-08-20")])["y"] == 0


def test_outcome_A_unscored_cases():
    assert oa([d("NOA", "2026-07-15")])["unscored"] == "activity inside the pending window"
    assert oa([d("CTRS", "2026-07-22")])["unscored"] == "activity inside the pending window"
    assert oa([d("SA..", "2026-08-01"), d("NOA", "2026-08-20")])["unscored"].startswith("non-compliance")
    assert oa([d("ABN", "2026-08-01"), d("NOA", "2026-08-20")])["unscored"] == "abandoned"
    assert oa([d("NOA", "2026-09-20")])["unscored"] == "action after 77 days"
    assert oa([])["unscored"] == "no action within 77 days"
    assert oa([], eval_date="2026-09-10")["unscored"] == "no action by the evaluation date"
    # a P batch: no action yet, horizon not passed -> unresolved; pre-commit outcome is never skipped
    assert oa([], horizon=270, eval_date="2026-10-08", lag=21, final=False) == {"resolved": False}
    assert oa([], horizon=270, eval_date="2027-05-20", lag=21, final=False)["unscored"] == "no action within 270 days"
    pc = oa([d("NOA", "2026-08-20"), d("CTFR", "2026-09-01")], horizon=270, t_commit="2026-08-25", final=False)
    assert pc["unscored"] == "pre-commit outcome"


class _NoClaims:
    """Stands in for the ODP client: no XML for any document."""

    def xml_files(self, url):
        return {}


def test_outcome_F_unscored_cases():
    c = "2026-06-10"
    before = [cl(1, "A widget comprising a frame and a lever."), cl(2, "The widget of claim 1, wherein the lever is "
                                                                   "spring loaded and pivots.", refs=[1])]
    base = [d("CTNF", c)]

    def of(extra, **kw):
        a = dict(eval_date="2026-09-30", t_commit=None, floor="2026-07-01", lag=0)
        a.update(kw)
        return L.outcome_F(_NoClaims(), base + extra, c, before, [2], a["eval_date"], a["t_commit"], a["floor"], a["lag"])
    assert of([d("A...", "2026-06-15")])["unscored"] == "activity inside the pending window"
    assert of([])["unscored"] == "no response within 92 days"
    assert of([d("ABN", "2026-09-20")])["unscored"] == "abandoned"
    assert of([d("A...", "2026-06-25")])["unscored"] == "response before cutoff"
    assert of([d("A...", "2026-09-15")])["unscored"] == "late response"
    assert of([d("CTFR", "2026-07-20"), d("A...", "2026-08-01")])["unscored"] == "examiner acted before the response"
    assert of([d("A...", "2026-08-01"), d("CLM", "2026-08-01")])["unscored"] == \
        "no cleanly parsed claims on the response date"
    assert of([d("A...", "2026-08-01")], t_commit="2026-08-05", floor=None)["unscored"] == "pre-commit outcome"
    assert of([], eval_date="2026-09-25", lag=21) == {"resolved": False}


def test_word_diff_ignores_case_and_punctuation():
    old = "A widget comprising a frame, and a lever."
    assert B.word_diff_inserted(old, "A widget comprising a frame; and a Lever") == ""
    ins = B.word_diff_inserted(old, "A widget comprising a frame, and a lever, wherein the lever is spring loaded.")
    assert "spring" in ins and "frame" not in ins and "widget" not in ins


def test_new_claim_counts_whole_text_as_inserted():
    new = cl(21, "A widget comprising a frame and a spring loaded lever.", inserted=["spring loaded"])
    assert B.inserted_text(new, None) == new["text"]


def test_root_follows_garbled_reference():
    prev = {1: cl(1, "A widget comprising a frame and a lever."),
            2: cl(2, "The widget of Claim_1, wherein the lever is spring loaded.")}
    assert B.parent_of(prev[2]) == 1
    assert B.root_of(2, prev)["number"] == 1
    assert B.root_of(1, prev) is None
    loop = {3: cl(3, "The widget of claim 4, wherein x.", refs=[4]), 4: cl(4, "The widget of claim 3, wherein y.", refs=[3])}
    assert B.root_of(3, loop) is None


def test_outcome_A_same_day_conflict_and_same_day_supplement():
    assert oa([d("CTFR", "2026-08-20"), d("NOA", "2026-08-20")])["unscored"] == "allowance and rejection on the same date"
    assert oa([d("NOA", "2026-08-20"), d("CTEQ", "2026-08-20")])["y"] == 1
    assert oa([d("SA..", "2026-08-20"), d("NOA", "2026-08-20")])["y"] == 1     # filed the day of the action
    assert oa([d("NOA", "2026-08-20")])["basis"] == "2026-08-20"


def test_p_batch_days_follow_the_weekly_grid():
    assert L.batch_info("P-2026-10-15")["D"] == "2026-10-15"
    with pytest.raises(SystemExit):
        L.batch_info("P-2026-10-16")


def test_elapsed_time_results_are_dated_at_the_end_of_the_period():
    res = oa([], horizon=270, eval_date="2027-05-20", lag=21, final=False)
    assert res["basis"] == "2027-03-28" and "2027-05-20" >= L.day(res["basis"], 21)


def test_answer_parsing_survives_hostile_numbers():
    assert B.parse_answer("A", '{"p_allow": 1' + "0" * 400 + "}")["p"] is None
    assert B.parse_answer("A", "[" * 100000 + "]" * 100000)["parsed"] is False
    assert B.parse_answer("F", '{"folds": {"2": 1e999}}', [2])["p"] == {"2": None}


def test_final_records_prefer_the_first_answer():
    recs = [{"unit": "A-1", "infra_failed": True, "n": 1}, {"unit": "A-1", "infra_failed": False, "n": 2},
            {"unit": "A-1", "infra_failed": False, "n": 3}, {"unit": "A-2", "infra_failed": True, "n": 4}]
    f = L.final_records(recs)
    assert f["A-1"]["n"] == 2 and f["A-2"]["n"] == 4


# ---------------------------------------------------------------- amendment 1 (fold labeller)

def test_stemmer_and_boilerplate():
    assert B.stem("determined") == B.stem("determining") == B.stem("determine")
    assert B.stem("detected") == B.stem("detecting")
    assert B.content_words("wherein the instructions are executable by the processor to cause the system to rank") \
        == ["rank"]


def test_fallback_needs_the_limitation_in_the_inserted_text():
    before = [cl(1, "A system comprising a model that scores packet loss concealment output quality."),
              cl(2, "The system of claim 1, wherein the model ranks packet loss concealment models.", refs=[1])]
    # claim 2 cancelled; claim 1 gains unrelated text that happens to share a word with claim 2
    after = [cl(1, "A system comprising a model that scores packet loss concealment output quality using labels.",
                status="currently amended", inserted=["using labels"]),
             cl(2, "(Cancelled)", status="cancelled")]
    assert B.fold_labels(before, after, [2]) == {2: 0}
    after_ok = [cl(1, "A system comprising a model that scores packet loss concealment output quality, wherein the "
                      "model ranks packet loss concealment models.", status="currently amended",
                   inserted=["wherein the model ranks packet loss concealment models"]),
                cl(2, "(Cancelled)", status="cancelled")]
    assert B.fold_labels(before, after_ok, [2]) == {2: 1}


def test_unpunctuated_next_claim_header_is_a_merge():
    merged = cl(3, "The method of claim 2, wherein training comprises resizing each video. 4 The method of claim 3, "
                   "wherein training uses the resized videos.", refs=[2])
    assert B.merged_claim(merged)
    fine = cl(5, "The method of claim 4, wherein the angle is between 3 and 4. The angle is measured as in claim 1.",
              refs=[4])
    assert not B.merged_claim(fine)
    own = cl(4, "4 The method of claim 3, wherein the video is resized.", refs=[3])
    assert not B.merged_claim(own)


def test_new_parallel_independent_claim_is_not_a_fold_of_shared_words():
    before = [cl(1, "A method comprising receiving downlink control information with a single allocation field for "
                    "multiple cells and scheduling the cells."),
              cl(2, "The method of claim 1, wherein the downlink control information schedules the multiple cells.",
                 refs=[1])]
    after = [cl(1, before[0]["text"], status="original"), cl(2, "(Cancelled)", status="cancelled"),
             cl(21, "An apparatus comprising circuitry configured to receive downlink control information with a single "
                    "allocation field for multiple cells and schedule the cells.", status="new")]
    assert B.fold_labels(before, after, [2]) == {2: 0}
    after_fold = [cl(1, before[0]["text"], status="original"), cl(2, "(Cancelled)", status="cancelled"),
                  cl(21, "An apparatus comprising circuitry configured to receive downlink control information with a "
                         "single allocation field for multiple cells and schedule the cells, wherein a joint table is "
                         "configured by a scheduling cell.", status="new")]
    before3 = before + [cl(3, "The method of claim 1, wherein a joint table is configured by a scheduling cell.",
                           refs=[1])]
    assert B.fold_labels(before3, after_fold + [cl(3, "(Cancelled)", status="cancelled")], [3]) == {3: 1}


def test_claim_lists_parse_fast_and_allow_a_trailing_comma():
    import time
    t = time.time()
    B.claims_with_status("claims " + "1234567890" * 30 + " 1, 2, 3, " * 50 + " xyz", "rejected")
    assert time.time() - t < 0.5
    assert B.claims_with_status("Claims 3, 4, 7, are rejected. Claims 1,2,4-6, is/are rejected", "rejected") == \
        {1, 2, 3, 4, 5, 6, 7}
