from market_report_finder_service.data.foreign_aliases import foreign_alias_entry


def test_foreign_alias_lookup_stays_within_selected_market():
    assert foreign_alias_entry("US", "英伟达")["ticker"] == "NVDA"
    assert foreign_alias_entry("CN", "英伟达") is None


def test_foreign_alias_lookup_uses_conservative_chinese_fuzzy_match():
    assert foreign_alias_entry("US", "英伟达公司")["ticker"] == "NVDA"
    assert foreign_alias_entry("US", "完全不存在的公司") is None
