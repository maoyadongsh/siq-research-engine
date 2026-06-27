from decimal import Decimal

from market_report_rules_service.normalization import infer_currency, infer_scale, parse_date, parse_decimal


def test_parse_date_accepts_common_formats():
    assert parse_date("2026-03-31").isoformat() == "2026-03-31"
    assert parse_date("2026/03/31").isoformat() == "2026-03-31"
    assert parse_date("20260331").isoformat() == "2026-03-31"


def test_parse_decimal_handles_accounting_format():
    assert parse_decimal("(1,234.50)") == Decimal("-1234.50")
    assert parse_decimal("HK$ 2,000 million") == Decimal("2000")
    assert parse_decimal("-") is None


def test_unit_helpers():
    assert infer_scale("HK$ million") == Decimal("1000000")
    assert infer_scale("RMB thousand") == Decimal("1000")
    assert infer_currency("HK$ million") == "HKD"
