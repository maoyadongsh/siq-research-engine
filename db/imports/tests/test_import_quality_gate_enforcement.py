import importlib.util
from pathlib import Path

import pytest


def _load(name: str):
    path = Path(__file__).resolve().parents[1] / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class FakeConn:
    def __init__(self):
        self.transaction_started = False

    def transaction(self):
        self.transaction_started = True
        raise AssertionError("quality gate enforcement should run before database transactions")


def _validation(market: str):
    class Validation:
        ok = True
        errors = []
        manifest = {
            "market": market,
            "filing_id": f"{market}:filing",
            "company_id": f"{market}:company",
            "ticker": "TEST",
            "parser_version": "p1",
            "rules_version": "r1",
            "artifact_hashes": {},
        }

    return Validation()


@pytest.mark.parametrize(
    ("module_name", "kwargs", "market"),
    [
        ("import_hk_evidence_package_to_postgres", {"schema": "pdf2md_hk"}, "HK"),
        ("import_eu_evidence_package_to_postgres", {"schema": "eu_ifrs"}, "EU"),
        ("import_market_xbrl_package_to_postgres", {"schema": "edinet_jp", "market": "JP"}, "JP"),
    ],
)
def test_importers_enforce_quality_gates_before_canonical_writes(tmp_path, monkeypatch, module_name, kwargs, market):
    module = _load(module_name)
    conn = FakeConn()
    monkeypatch.setattr(module, "validate_evidence_package", lambda package_dir: _validation(market))

    def block(*args, **kwargs):
        raise SystemExit("Quality gate blocked canonical import; decision=block; hard_gate_rule_ids=package.quality_status.fail")

    monkeypatch.setattr(module, "enforce_quality_gates", block)

    with pytest.raises(SystemExit) as excinfo:
        module.import_package(conn, tmp_path, **kwargs)

    assert "package.quality_status.fail" in str(excinfo.value)
    assert conn.transaction_started is False


def test_hk_force_review_audit_is_written_and_retrieval_skipped(tmp_path, monkeypatch):
    module = _load("import_hk_evidence_package_to_postgres")
    conn = FakeConn()
    monkeypatch.setattr(module, "validate_evidence_package", lambda package_dir: _validation("HK"))
    monkeypatch.setattr(module, "compute_artifact_hashes", lambda package_dir: {"manifest.json": "abc"})
    monkeypatch.setattr(module, "read_json", lambda path: {"overall_status": "warning"} if str(path).endswith("quality_report.json") else {})

    class Enforcement:
        gates = {"canonical_decision": "review", "retrieval_decision": "review"}
        promotion_override = {"audit_log_id": "qg-audit-test"}

    calls = []
    monkeypatch.setattr(module, "enforce_quality_gates", lambda *args, **kwargs: Enforcement())
    monkeypatch.setattr(module, "quality_with_gate_audit", lambda quality, enforcement: {**quality, "promotion_override": enforcement.promotion_override})
    monkeypatch.setattr(module, "should_write_target", lambda enforcement, target: False if target == "retrieval" else True)
    monkeypatch.setattr(module, "_upsert_company", lambda *args: calls.append("company"))
    monkeypatch.setattr(module, "_upsert_filing", lambda *args: calls.append("filing"))
    monkeypatch.setattr(module, "_upsert_parse_run", lambda *args: calls.append(("parse_run", args[6])))
    monkeypatch.setattr(module, "_delete_run_rows", lambda *args: calls.append("delete"))
    monkeypatch.setattr(module, "_insert_artifacts", lambda *args: calls.append("artifacts"))
    monkeypatch.setattr(module, "_insert_sections", lambda *args: calls.append("sections"))
    monkeypatch.setattr(module, "_insert_pdf_pages", lambda *args: calls.append("pages"))
    monkeypatch.setattr(module, "_insert_tables", lambda *args: calls.append("tables"))
    monkeypatch.setattr(module, "_insert_evidence", lambda *args: calls.append("evidence"))
    monkeypatch.setattr(module, "_insert_financial_facts", lambda *args: calls.append("facts"))
    monkeypatch.setattr(module, "_insert_statement_items", lambda *args: calls.append("items"))
    monkeypatch.setattr(module, "_insert_checks", lambda *args: calls.append("checks"))
    monkeypatch.setattr(module, "_insert_quality_report", lambda *args: calls.append("quality"))
    monkeypatch.setattr(module, "_insert_retrieval_chunks", lambda *args: calls.append("retrieval"))

    class TxConn(FakeConn):
        def transaction(self):
            self.transaction_started = True

            class Tx:
                def __enter__(self_inner):
                    return self_inner

                def __exit__(self_inner, exc_type, exc, tb):
                    return False

            return Tx()

    parse_run_id = module.import_package(
        TxConn(),
        tmp_path,
        force_review=True,
        force_requested_by="analyst@example.com",
        force_reason="Reviewed source pages.",
    )

    assert parse_run_id
    assert "retrieval" not in calls
    parse_run_quality = [item for item in calls if isinstance(item, tuple) and item[0] == "parse_run"][0][1]
    assert parse_run_quality["promotion_override"]["audit_log_id"] == "qg-audit-test"
