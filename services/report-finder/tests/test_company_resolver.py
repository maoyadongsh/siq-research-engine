from report_finder_service.services.company_resolver import CompanyResolver


def test_resolve_alias():
    resolved, candidates = CompanyResolver().resolve_with_candidates("贵州茅台")
    assert resolved.ticker == "600519"
    assert resolved.exchange == "SSE"
    assert len(candidates) >= 1


def test_resolve_substring():
    resolved, candidates = CompanyResolver().resolve_with_candidates("那个做茅台的公司")
    assert resolved.ticker == "600519"
    assert candidates[0].ticker == "600519"


def test_normalize_cn_equity_name():
    normalized = CompanyResolver._normalize_cn_equity_name("*ST国华股份有限公司")
    assert normalized == "国华"


def test_resolve_exact_ticker_static():
    resolved, candidates = CompanyResolver().resolve_with_candidates("随便写个名字", ticker="000001")
    assert resolved.ticker == "000001"
    assert resolved.match_reason == "cninfo_exact_ticker:000001"
    assert candidates[0].ticker == "000001"


def test_resolve_candidates_for_cn_name():
    resolved, candidates = CompanyResolver().resolve_with_candidates("国华网安")
    assert resolved.ticker == "000004"
    assert any(candidate.ticker == "000004" for candidate in candidates)


def test_parse_ticker_like_query():
    assert CompanyResolver._maybe_ticker_from_query("SZ000001") == "000001"
    assert CompanyResolver._maybe_ticker_from_query("09626") == "9626"
    assert CompanyResolver._maybe_ticker_from_query("平安银行") is None
    assert CompanyResolver._maybe_ticker_from_query("NASDAQ:PDD") is None
