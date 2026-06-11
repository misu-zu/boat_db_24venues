"""Pre-persistence validation of parsed odds pages."""
from __future__ import annotations

from itertools import permutations

from .parser import ParsedOddsPage

# All 120 valid combination codes (6P3).
ALL_COMBINATIONS: frozenset[int] = frozenset(
    a * 100 + b * 10 + c for a, b, c in permutations(range(1, 7), 3)
)
assert len(ALL_COMBINATIONS) == 120


class ValidationError(Exception):
    """Raised when a parsed page must not be persisted as a snapshot."""

    def __init__(self, code: str, detail: str):
        self.code = code          # 'incomplete' | 'parse_error' | 'mismatch'
        self.detail = detail
        super().__init__(f"{code}: {detail}")


def validate_odds_page(
    page: ParsedOddsPage,
    expected_date_yyyymmdd: str,
    expected_venue_code: str,
    expected_race_no: int,
) -> None:
    """Raise ValidationError unless the page is complete and consistent."""
    if page.is_no_data:
        raise ValidationError("parse_error", "no_data page cannot be validated")

    # --- identity consistency with the requested URL -----------------------
    if page.page_date_yyyymmdd and page.page_date_yyyymmdd != expected_date_yyyymmdd:
        raise ValidationError(
            "mismatch",
            f"page date {page.page_date_yyyymmdd} != requested {expected_date_yyyymmdd}",
        )
    if page.page_venue_code and page.page_venue_code != expected_venue_code:
        raise ValidationError(
            "mismatch",
            f"page venue {page.page_venue_code} != requested {expected_venue_code}",
        )
    if page.page_race_no and page.page_race_no != expected_race_no:
        raise ValidationError(
            "mismatch",
            f"page race {page.page_race_no} != requested {expected_race_no}",
        )

    odds = page.odds

    # --- exactly 120 rows ---------------------------------------------------
    if len(odds) != 120:
        raise ValidationError("incomplete", f"expected 120 odds rows, got {len(odds)}")

    codes = [o.combination_code for o in odds]
    code_set = set(codes)

    # --- no duplicates ------------------------------------------------------
    if len(code_set) != 120:
        dupes = sorted({c for c in codes if codes.count(c) > 1})
        raise ValidationError("parse_error", f"duplicate combinations: {dupes[:5]}")

    # --- boats 1..6 only, all distinct within a combination ----------------
    for o in odds:
        boats = (o.first_boat, o.second_boat, o.third_boat)
        if not all(1 <= b <= 6 for b in boats):
            raise ValidationError("parse_error", f"boat out of range: {boats}")
        if len(set(boats)) != 3:
            raise ValidationError("parse_error", f"repeated boat in {boats}")

    # --- full 6P3 coverage --------------------------------------------------
    if code_set != ALL_COMBINATIONS:
        missing = sorted(ALL_COMBINATIONS - code_set)
        raise ValidationError("incomplete", f"missing combinations: {missing[:5]}")

    # --- odds positive or explicitly missing --------------------------------
    for o in odds:
        if o.odds_tenths is not None and o.odds_tenths <= 0:
            raise ValidationError(
                "parse_error",
                f"non-positive odds {o.odds_text!r} for {o.combination_code}",
            )
