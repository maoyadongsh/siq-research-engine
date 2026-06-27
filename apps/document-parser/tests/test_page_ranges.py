from __future__ import annotations

import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parents[1]
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from page_ranges import parse_page_ranges, selected_page_indexes


def test_parse_page_ranges_preserves_order_and_deduplicates():
    assert parse_page_ranges("3,1-2,2,5", page_count=10) == [3, 1, 2, 5]


def test_empty_page_ranges_means_all_pages_for_indexes():
    assert parse_page_ranges("") == []
    assert selected_page_indexes("", 3) == [0, 1, 2]


def test_page_ranges_drop_pages_outside_document():
    assert parse_page_ranges("2-5", page_count=3) == [2, 3]


def test_invalid_page_ranges_raise_clear_errors():
    with pytest.raises(ValueError, match="Invalid page range"):
        parse_page_ranges("5-2")
    with pytest.raises(ValueError, match="Invalid page number"):
        parse_page_ranges("abc")
    with pytest.raises(ValueError, match="does not overlap"):
        parse_page_ranges("9-10", page_count=3)
