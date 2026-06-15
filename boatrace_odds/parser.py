"""HTML parsing for the odds3t page.

ALL CSS selectors / DOM traversal for the official site live in this
module. Other modules must never depend on HTML structure.
"""
from __future__ import annotations

import re
import warnings
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

# The official pages use an XHTML doctype; lxml's HTML parser handles them
# fine, so silence the advisory warning.
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

NO_DATA_PATTERNS = ("データはありません", "データがありません")
FINAL_ODDS_LABEL = "締切時オッズ"
UPDATE_TIME_LABEL = "オッズ更新時間"
DEADLINE_ROW_LABEL = "締切予定時刻"

_HHMM_RE = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")
_ODDS_DECIMAL_RE = re.compile(r"^\d+\.\d$")
_ODDS_INT_RE = re.compile(r"^\d+$")
_VENUE_IMG_RE = re.compile(r"text_place\d*_(\d{2})\.png")


@dataclass
class TrifectaOdds:
    combination_code: int     # 123 for 1-2-3
    first_boat: int
    second_boat: int
    third_boat: int
    odds_text: str            # displayed string
    odds_tenths: int | None   # display value * 10; None = explicit missing

    def as_dict(self) -> dict:
        return {
            "combination_code": self.combination_code,
            "first_boat": self.first_boat,
            "second_boat": self.second_boat,
            "third_boat": self.third_boat,
            "odds_text": self.odds_text,
            "odds_tenths": self.odds_tenths,
        }


@dataclass
class ParsedOddsPage:
    page_status: str                       # 'selling' | 'final' | 'no_data'
    page_date_yyyymmdd: str | None = None  # from page links
    page_venue_code: str | None = None     # from venue image, leading zero kept
    page_race_no: int | None = None        # from page links
    deadline_times_hhmm: dict[int, str] = field(default_factory=dict)
    source_updated_hhmm: str | None = None  # オッズ更新時間 (selling only)
    odds: list[TrifectaOdds] = field(default_factory=list)

    @property
    def is_no_data(self) -> bool:
        return self.page_status == "no_data"

    @property
    def is_final(self) -> bool:
        return self.page_status == "final"


def _parse_odds_value(text: str) -> int | None:
    """Convert displayed odds text to tenths integer WITHOUT using float.

    '6.8' -> 68, '145.7' -> 1457, '1580' -> 15800.
    Anything else (e.g. '欠場', '', '-') -> None (explicit missing).
    """
    t = text.strip().replace(",", "")
    if _ODDS_DECIMAL_RE.match(t):
        whole, frac = t.split(".")
        return int(whole) * 10 + int(frac)
    if _ODDS_INT_RE.match(t):
        return int(t) * 10
    return None


def _extract_identity(soup: BeautifulSoup) -> tuple[str | None, str | None, int | None]:
    """Extract (date_yyyymmdd, venue_code, race_no) the page claims to show."""
    date_str: str | None = None
    venue_code: str | None = None
    race_no: int | None = None

    # Venue from the heading image: text_place2_24.png -> '24'
    head = soup.select_one("div.heading2_area img[src]")
    if head:
        m = _VENUE_IMG_RE.search(head["src"])
        if m:
            venue_code = m.group(1)

    # Date and race number from the racelist link in the tab3 navigation.
    for a in soup.select("div.tab3 a[href]"):
        href = a["href"]
        if "racelist" in href:
            qs = parse_qs(urlparse(href).query)
            if "hd" in qs:
                date_str = qs["hd"][0]
            if "rno" in qs:
                try:
                    race_no = int(qs["rno"][0])
                except ValueError:
                    pass
            if "jcd" in qs and venue_code is None:
                venue_code = qs["jcd"][0]
            break
    return date_str, venue_code, race_no


def _extract_deadlines(soup: BeautifulSoup) -> dict[int, str]:
    """Extract the 1R..12R scheduled deadline times (HH:MM)."""
    result: dict[int, str] = {}
    for td in soup.find_all("td"):
        if DEADLINE_ROW_LABEL in td.get_text(strip=True):
            row = td.find_parent("tr")
            if row is None:
                continue
            cells = [c for c in row.find_all("td") if c is not td]
            for idx, cell in enumerate(cells, start=1):
                text = cell.get_text(strip=True)
                if _HHMM_RE.match(text):
                    result[idx] = text
            break
    return result


def _extract_update_time(soup: BeautifulSoup) -> str | None:
    node = soup.select_one("p.tab4_refreshText")
    if node is None:
        return None
    text = node.get_text(" ", strip=True)
    m = re.search(r"([01]?\d|2[0-3]):[0-5]\d", text)
    return m.group(0) if m else None


def _is_final_page(soup: BeautifulSoup) -> bool:
    node = soup.select_one("p.tab4_time")
    return node is not None and FINAL_ODDS_LABEL in node.get_text(strip=True)


def _is_no_data_page(soup: BeautifulSoup, html_text: str) -> bool:
    for pat in NO_DATA_PATTERNS:
        if pat in html_text:
            return True
    return False


def _extract_trifecta_odds(soup: BeautifulSoup) -> list[TrifectaOdds]:
    """Parse the 6-column trifecta odds table into 120 long rows.

    Layout: 6 column groups (first boat 1..6). Each tbody row carries
    either 18 tds (rows that open a new second-boat block:
    [second, third, odds] x 6) or 12 tds ([third, odds] x 6).
    """
    # Locate the odds table: the one whose cells carry class 'oddsPoint'.
    odds_table = None
    for table in soup.find_all("table"):
        if table.find("td", class_="oddsPoint"):
            odds_table = table
            break
    if odds_table is None:
        return []

    tbody = odds_table.find("tbody")
    rows = tbody.find_all("tr") if tbody else []

    current_second: dict[int, int | None] = {c: None for c in range(6)}
    result: list[TrifectaOdds] = []

    for tr in rows:
        tds = tr.find_all("td")
        if len(tds) == 18:
            per_col = 3
        elif len(tds) == 12:
            per_col = 2
        else:
            # Unknown row shape -> structural change; report nothing parsed
            # from this row but keep going so the validator flags the count.
            continue
        for col in range(6):
            group = tds[col * per_col:(col + 1) * per_col]
            if per_col == 3:
                second_text = group[0].get_text(strip=True)
                try:
                    current_second[col] = int(second_text)
                except ValueError:
                    current_second[col] = None
                third_td, odds_td = group[1], group[2]
            else:
                third_td, odds_td = group[0], group[1]

            second = current_second[col]
            third_text = third_td.get_text(strip=True)
            try:
                third = int(third_text)
            except ValueError:
                continue
            if second is None:
                continue
            first = col + 1
            odds_text = odds_td.get_text(strip=True)
            result.append(
                TrifectaOdds(
                    combination_code=first * 100 + second * 10 + third,
                    first_boat=first,
                    second_boat=second,
                    third_boat=third,
                    odds_text=odds_text,
                    odds_tenths=_parse_odds_value(odds_text),
                )
            )
    return result


def parse_odds3t_page(html_text: str) -> ParsedOddsPage:
    """Parse one odds3t HTML document."""
    soup = BeautifulSoup(html_text, "lxml")

    if _is_no_data_page(soup, html_text):
        return ParsedOddsPage(page_status="no_data")

    date_str, venue_code, race_no = _extract_identity(soup)
    page = ParsedOddsPage(
        page_status="final" if _is_final_page(soup) else "selling",
        page_date_yyyymmdd=date_str,
        page_venue_code=venue_code,
        page_race_no=race_no,
        deadline_times_hhmm=_extract_deadlines(soup),
        source_updated_hhmm=_extract_update_time(soup),
        odds=_extract_trifecta_odds(soup),
    )
    return page
