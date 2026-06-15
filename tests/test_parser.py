from boatrace_odds.parser import parse_odds3t_page, _parse_odds_value
from boatrace_odds.validator import ALL_COMBINATIONS


def test_selling_page_yields_120_odds(selling_html):
    page = parse_odds3t_page(selling_html)
    assert page.page_status == "selling"
    assert not page.is_final
    assert len(page.odds) == 120
    codes = {o.combination_code for o in page.odds}
    assert codes == ALL_COMBINATIONS


def test_selling_page_metadata(selling_html):
    page = parse_odds3t_page(selling_html)
    assert page.page_date_yyyymmdd == "20260610"
    assert page.page_venue_code == "24"
    assert page.page_race_no == 6
    assert page.source_updated_hhmm == "19:02"
    assert len(page.deadline_times_hhmm) == 12
    assert page.deadline_times_hhmm[1] == "17:41"
    assert page.deadline_times_hhmm[12] == "22:45"


def test_final_page_detected_as_final(final_html):
    page = parse_odds3t_page(final_html)
    assert page.page_status == "final"
    assert page.is_final
    assert len(page.odds) == 120


def test_no_data_page_detected(no_data_html):
    page = parse_odds3t_page(no_data_html)
    assert page.page_status == "no_data"
    assert page.is_no_data
    assert page.odds == []


def test_incomplete_page_has_fewer_rows(incomplete_html):
    page = parse_odds3t_page(incomplete_html)
    assert len(page.odds) < 120


def test_odds_value_conversion_without_float():
    assert _parse_odds_value("6.8") == 68
    assert _parse_odds_value("145.7") == 1457
    assert _parse_odds_value("1580") == 15800  # is-fColor1 integer display
    assert _parse_odds_value("欠場") is None
    assert _parse_odds_value("") is None
    assert _parse_odds_value("-") is None


def test_odds_text_preserved(selling_html):
    page = parse_odds3t_page(selling_html)
    by_code = {o.combination_code: o for o in page.odds}
    o = by_code[123]
    assert isinstance(o.odds_text, str)
    assert o.odds_tenths is not None
    # text and tenths agree
    whole, frac = (o.odds_text.split(".") + ["0"])[:2]
    assert o.odds_tenths == int(whole) * 10 + int(frac[0]) or "." not in o.odds_text
