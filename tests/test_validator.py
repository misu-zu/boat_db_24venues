import pytest

from boatrace_odds.parser import ParsedOddsPage, TrifectaOdds, parse_odds3t_page
from boatrace_odds.validator import (
    ALL_COMBINATIONS,
    ValidationError,
    validate_odds_page,
)


def _full_page(**overrides) -> ParsedOddsPage:
    odds = [
        TrifectaOdds(
            combination_code=code,
            first_boat=code // 100,
            second_boat=(code // 10) % 10,
            third_boat=code % 10,
            odds_text="6.8",
            odds_tenths=68,
        )
        for code in sorted(ALL_COMBINATIONS)
    ]
    page = ParsedOddsPage(
        page_status="selling",
        page_date_yyyymmdd="20260610",
        page_venue_code="24",
        page_race_no=6,
        odds=odds,
    )
    for k, v in overrides.items():
        setattr(page, k, v)
    return page


def test_valid_page_passes():
    validate_odds_page(_full_page(), "20260610", "24", 6)


def test_119_rows_rejected():
    page = _full_page()
    page.odds = page.odds[:-1]
    with pytest.raises(ValidationError) as exc:
        validate_odds_page(page, "20260610", "24", 6)
    assert exc.value.code == "incomplete"


def test_duplicate_combination_rejected():
    page = _full_page()
    page.odds[1] = page.odds[0]
    with pytest.raises(ValidationError):
        validate_odds_page(page, "20260610", "24", 6)


def test_same_boat_twice_rejected():
    page = _full_page()
    bad = page.odds[0]
    bad.second_boat = bad.first_boat
    with pytest.raises(ValidationError):
        validate_odds_page(page, "20260610", "24", 6)


def test_boat_out_of_range_rejected():
    page = _full_page()
    page.odds[0].first_boat = 7
    with pytest.raises(ValidationError):
        validate_odds_page(page, "20260610", "24", 6)


def test_negative_odds_rejected():
    page = _full_page()
    page.odds[0].odds_tenths = 0
    with pytest.raises(ValidationError):
        validate_odds_page(page, "20260610", "24", 6)


def test_explicit_missing_odds_allowed():
    page = _full_page()
    page.odds[0].odds_tenths = None
    page.odds[0].odds_text = "欠場"
    validate_odds_page(page, "20260610", "24", 6)


def test_url_page_mismatch_rejected():
    page = _full_page()
    with pytest.raises(ValidationError) as exc:
        validate_odds_page(page, "20260611", "24", 6)
    assert exc.value.code == "mismatch"
    with pytest.raises(ValidationError):
        validate_odds_page(page, "20260610", "01", 6)
    with pytest.raises(ValidationError):
        validate_odds_page(page, "20260610", "24", 7)


def test_incomplete_fixture_rejected(incomplete_html):
    page = parse_odds3t_page(incomplete_html)
    with pytest.raises(ValidationError):
        validate_odds_page(page, "20260610", "24", 6)
