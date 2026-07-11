from pathlib import Path

import pytest

from services import market_document_identity as identity


def test_normalize_market_code_accepts_us_sec_aliases():
    assert identity.normalize_market_code("US_SEC") == "US"
    assert identity.normalize_market_code("us-sec") == "US"
    assert identity.normalize_market_code("hk") == "HK"


def test_document_full_payload_value_prefers_explicit_document_path():
    assert identity.document_full_payload_value({"document_full_path": "doc.json", "task_id": "task-1"}) == "doc.json"
    assert identity.document_full_payload_value({"task_id": "task-1"}) == "task-1"
    assert identity.document_full_payload_value({}) is None


def test_resolve_document_full_path_accepts_directory_alias(tmp_path):
    document_full = tmp_path / "parser-results" / "task-1" / "document_full.json"
    document_full.parent.mkdir(parents=True)
    document_full.write_text("{}", encoding="utf-8")

    resolved = identity.resolve_document_full_path(
        market="HK",
        value="task-1",
        safe_market_document_full_path=lambda _market, _value: document_full.parent,
    )

    assert resolved == document_full


def test_resolve_document_full_path_rejects_non_document_full_json(tmp_path):
    other = tmp_path / "parser-results" / "task-1" / "financial_data.json"
    other.parent.mkdir(parents=True)
    other.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="document_full_path"):
        identity.resolve_document_full_path(
            market="HK",
            value=str(other),
            safe_market_document_full_path=lambda _market, _value: other,
        )


def test_document_full_path_keys_include_original_absolute_repo_and_market_relative(tmp_path):
    repo_root = tmp_path / "repo"
    root = repo_root / "data" / "pdf-parser" / "results"
    document_full = root / "hk-task" / "document_full.json"
    document_full.parent.mkdir(parents=True)
    document_full.write_text("{}", encoding="utf-8")

    keys = identity.document_full_path_keys(
        market="HK",
        value="hk-task/document_full.json",
        repo_root=repo_root,
        market_document_full_roots={"HK": root},
        safe_market_document_full_path=lambda _market, _value: document_full,
    )

    assert keys == (
        "hk-task/document_full.json",
        str(document_full.resolve()),
        str(Path("data/pdf-parser/results/hk-task/document_full.json")),
    )


def test_resolve_document_full_identity_builds_status_selector_payload(tmp_path):
    repo_root = tmp_path / "repo"
    root = repo_root / "data" / "parser-results" / "us-sec"
    document_full = root / "NVDA-10-K" / "document_full.json"
    document_full.parent.mkdir(parents=True)
    document_full.write_text("{}", encoding="utf-8")

    result = identity.resolve_document_full_identity(
        market="US_SEC",
        repo_root=repo_root,
        market_document_full_roots={"US": root},
        safe_market_document_full_path=lambda _market, _value: document_full,
        payload={"task_id": "NVDA-10-K"},
        parse_run_id="parse-us",
        filing_id="US:0001045810:0001045810-25-000023",
        task_id="NVDA-10-K",
    )

    assert result.market == "US"
    assert result.document_full_path == document_full
    assert result.path_keys
    assert result.selector_payload() == {
        "parse_run_id": "parse-us",
        "filing_id": "US:0001045810:0001045810-25-000023",
        "document_full_path": str(document_full),
        "task_id": "NVDA-10-K",
    }
    assert identity.build_status_selector(result) == result.selector_payload()
    assert identity.build_import_selector(result) == {
        "market": "US",
        "document_full_path": str(document_full),
        "task_id": "NVDA-10-K",
    }
    assert identity.build_agent_query_scope(result) == {
        "market": "US",
        "parse_run_id": "parse-us",
        "filing_id": "US:0001045810:0001045810-25-000023",
    }
    assert identity.document_full_task_path_pattern("NVDA-10-K") == "%/NVDA-10-K/document_full.json"
    assert identity.status_task_lookup_params(result) == ("NVDA-10-K", "%/NVDA-10-K/document_full.json")
    assert identity.status_task_lookup_params(identity.MarketDocumentFullIdentity(market="US")) == ()
