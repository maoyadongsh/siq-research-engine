import pytest

from request_args import parse_int_arg, query_flag_enabled


def test_parse_int_arg_returns_default_for_missing_or_empty_values():
    assert parse_int_arg({}, "limit", 50) == 50
    assert parse_int_arg({"limit": ""}, "limit", 50) == 50


def test_parse_int_arg_parses_integer_strings():
    assert parse_int_arg({"limit": "25"}, "limit", 50) == 25
    assert parse_int_arg({"limit": "-3"}, "limit", 50) == -3


def test_parse_int_arg_can_fall_back_on_invalid_values():
    assert parse_int_arg({"limit": "many"}, "limit", 50, invalid_default=50) == 50


def test_parse_int_arg_raises_for_invalid_values_by_default():
    with pytest.raises(ValueError):
        parse_int_arg({"since": "later"}, "since", 0)


def test_query_flag_enabled_matches_route_download_values_exactly():
    assert query_flag_enabled({"download": "1"}, "download") is True
    assert query_flag_enabled({"download": "true"}, "download") is True
    assert query_flag_enabled({"download": "yes"}, "download") is True
    assert query_flag_enabled({"download": "True"}, "download") is False
    assert query_flag_enabled({}, "download") is False
