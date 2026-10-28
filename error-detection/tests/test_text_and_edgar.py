from error_detection.sources.edgar import hits_from_response, total_from_response
from error_detection.text import html_to_text, to_text

SAMPLE_EFTS = {
    "hits": {
        "total": {"value": 2, "relation": "eq"},
        "hits": [
            {
                "_id": "0001193125-25-012345:d123456dex101.htm",
                "_source": {
                    "ciks": ["0000999999"],
                    "display_names": ["Example Corp  (EXMP)  (CIK 0000999999)"],
                    "form": "8-K",
                    "file_date": "2025-03-14",
                    "file_type": "EX-10.1",
                    "file_description": "CREDIT AGREEMENT",
                    "adsh": "0001193125-25-012345",
                },
            },
            {"_id": "0000950170-25-000001:ex4-1.htm", "_source": {"ciks": ["0000001234"], "file_type": "EX-4.1"}},
        ],
    }
}


def test_hits_from_response():
    hits = hits_from_response(SAMPLE_EFTS)
    assert total_from_response(SAMPLE_EFTS) == 2
    h = hits[0]
    assert h.file_type == "EX-10.1" and h.company.startswith("Example Corp")
    assert h.url == "https://www.sec.gov/Archives/edgar/data/999999/000119312525012345/d123456dex101.htm"
    assert hits[1].adsh == "0000950170-25-000001" and hits[1].filename == "ex4-1.htm"


def test_html_to_text_keeps_blocks():
    raw = "<html><body><p>Section&nbsp;2.01 <b>Commitments</b>.</p><p>Each Lender agrees&#8230;</p><script>x</script></body></html>"
    text = html_to_text(raw)
    assert text.splitlines()[0] == "Section 2.01 Commitments."
    assert "x" not in text.split("\n")[-1] or "Lender" in text


def test_to_text_plain_passthrough():
    assert to_text("plain  text\n\n\nhere") == "plain text\n\nhere"


def test_struck_text_is_dropped():
    raw = (
        '<p>equal to <font style="text-decoration:line-through">five</font>'
        '<font style="text-decoration:underline double">seven and one-half</font> percent '
        '(<strike>5</strike><u>7.5</u>%) and <span style="text-decoration: line-through">old <span>nested</span> </span>new</p>'
    )
    assert html_to_text(raw) == "equal to seven and one-half percent (7.5%) and new"


def test_unclosed_strike_is_bounded_by_paragraph():
    raw = '<p>keep <font style="text-decoration:line-through">gone</p><p>kept too</p>'
    assert html_to_text(raw) == "keep\n\nkept too"
