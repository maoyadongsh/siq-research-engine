from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
CONTRACTS_SRC = Path(__file__).resolve().parents[5] / "packages" / "market-contracts" / "src"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
if str(CONTRACTS_SRC) not in sys.path:
    sys.path.insert(0, str(CONTRACTS_SRC))

from analysis_bundle_renderer import (  # noqa: E402
    MAX_PRESENTATION_EVIDENCE_ITEMS,
    _change_sentence,
    _publish_staged_files,
    _ratio_sentence,
    render_analysis_bundle,
)
from analysis_input_bundle import build_analysis_input_bundle  # noqa: E402
from formal_research_packs import build_formal_research_packs  # noqa: E402
from input_adapters import SourceAdapterError, source_family_for_manifest  # noqa: E402
from input_adapters.base import normalize_fact  # noqa: E402
from siq_market_contracts import AgentArtifactV2, EvidenceRefV1, NormalizedFactV1  # noqa: E402

IDENTITY = {
    "market": "HK",
    "company_id": "HK:00005",
    "filing_id": "HK:00005:filing-1",
    "parse_run_id": "parse-1",
}


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _target(*, market="HK", form_type=None, quality="pass") -> dict:
    identity = {**IDENTITY, "market": market}
    if market == "US":
        identity.update(company_id="US:0000001", filing_id="US:0000001:accession-1")
    return {
        "schema_version": "siq_research_target_v1",
        "company_key": "opaque-key",
        "company_wiki_id": "TEST-Test-Co",
        "display_code": "TEST",
        "display_name": "Test Co",
        "research_identity": identity,
        "source_report": {
            "report_id": "report-1",
            "source_family": "sec_ixbrl" if market == "US" else "pdf_market",
            "document_format": "ixbrl_html" if market == "US" else "pdf",
            "report_type": "annual" if form_type != "10-Q" else "quarterly",
            "form_type": form_type,
            "fiscal_year": 2024,
            "fiscal_period": "FY" if form_type != "10-Q" else "Q1",
            "period_end": "2024-09-28",
            "accounting_standard": "US_GAAP" if market == "US" else "IFRS",
            "reporting_currency": "USD" if market == "US" else "HKD",
            "quality_status": quality,
        },
    }


def _tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _pdf_fixture(tmp_path: Path) -> tuple[Path, Path, dict]:
    company_dir = tmp_path / "wiki" / "hk" / "companies" / "TEST-Test-Co"
    report_dir = company_dir / "reports" / "report-1"
    target = _target()
    manifest = {
        "schema_version": "market_evidence_package_v1",
        **target["research_identity"],
        "report_id": "report-1",
        "source_id": "hkex",
        "document_format": "pdf",
        "fiscal_year": 2024,
        "period_end": "2024-09-28",
        "quality_status": "pass",
    }
    _write(report_dir / "manifest.json", manifest)
    _write(report_dir / "report.md", "# Annual report")
    _write(
        report_dir / "metrics" / "normalized_metrics.json",
        {
            "metrics": [
                {
                    "canonical_name": "revenue",
                    "label": "Revenue",
                    "value": 1200,
                    "raw_value": "1,200",
                    "currency": "HKD",
                    "unit": "HKD thousand",
                    "scale": 1000,
                    "period": "2024-09-28",
                    "source": {"pdf_page_number": 8, "table_index": 2, "quote_text": "Revenue 1,200"},
                }
            ]
        },
    )
    _write(
        report_dir / "qa" / "source_map.json",
        {
            "entries": [
                {
                    "evidence_id": "ev-1",
                    "source_type": "pdf_statement_table",
                    "target": "revenue",
                    "pdf_page_number": 8,
                    "table_index": 2,
                    "quote_text": "Revenue 1,200",
                }
            ]
        },
    )
    _write(report_dir / "metrics" / "financial_checks.json", {"status": "pass"})
    return company_dir, report_dir, target


def _sec_fixture(tmp_path: Path, *, form_type: str, quality: str = "warning") -> tuple[Path, Path, dict]:
    company_dir = tmp_path / "wiki" / "us" / "companies" / "TEST-Test-Co"
    report_dir = company_dir / "reports" / "report-1"
    target = _target(market="US", form_type=form_type, quality=quality)
    manifest = {
        "schema_version": "market_evidence_package_v1",
        **target["research_identity"],
        "report_id": "report-1",
        "source_id": "sec",
        "document_format": "ixbrl_html",
        "form": form_type,
        "fiscal_year": 2024,
        "fiscal_period": "FY" if form_type == "10-K" else "Q1",
        "period_end": "2024-09-28",
        "quality_status": quality,
        "accounting_standard": "US_GAAP",
        "accession_number": "accession-1",
        "source_url": "https://www.sec.gov/example.htm",
        "artifacts": {
            "document_full": "parser/document_full.json",
            "wiki_report_complete": "sections/report_complete.md",
            "financial_data": "metrics/financial_data.json",
            "normalized_metrics": "metrics/normalized_metrics.json",
            "financial_checks": "metrics/financial_checks.json",
            "source_map": "qa/source_map.json",
            "xbrl_facts_raw": "xbrl/facts_raw.json",
            "xbrl_contexts": "xbrl/contexts.json",
            "xbrl_units": "xbrl/units.json",
            "xbrl_labels": "xbrl/labels.json",
        },
    }
    _write(report_dir / "manifest.json", manifest)
    _write(report_dir / "parser" / "document_full.json", {"blocks": []})
    _write(report_dir / "sections" / "report_complete.md", "# Filing")
    _write(
        report_dir / "sections" / "mda.md", "---\nschema_version: sec_section_v1\n---\n# MD&A\nManagement discussion."
    )
    _write(report_dir / "metrics" / "financial_data.json", {"statements": {}})
    _write(
        report_dir / "metrics" / "normalized_metrics.json",
        {
            "metrics": [
                {
                    "metric_id": "metric-1",
                    "canonical_name": "revenue",
                    "concept": "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
                    "value": "1000000",
                    "unit": "USD",
                    "currency": "USD",
                    "period_start": "2023-10-01",
                    "period_end": "2024-09-28",
                    "fiscal_year": 2024,
                    "fiscal_period": "FY" if form_type == "10-K" else "Q1",
                    "raw_fact_id": "fact-1",
                    "raw": {"context_id": "ctx-1"},
                }
            ]
        },
    )
    _write(report_dir / "metrics" / "financial_checks.json", {"status": quality, "warnings": ["sample"]})
    _write(
        report_dir / "qa" / "source_map.json",
        {
            "entries": [
                {
                    "evidence_id": "ev-sec-1",
                    "source_type": "sec_xbrl_fact",
                    "fact_id": "fact-1",
                    "xbrl_tag": "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
                    "context_ref": "ctx-1",
                    "html_anchor": "F_1",
                    "source_url": "https://www.sec.gov/example.htm",
                    "quote_text": "1,000",
                }
            ]
        },
    )
    _write(
        report_dir / "xbrl" / "facts_raw.json",
        {
            "facts": [
                {
                    "fact_id": "fact-1",
                    "concept": "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
                    "taxonomy": "us-gaap",
                    "value_text": "1000000",
                    "context_ref": "ctx-1",
                    "period_start": "2023-10-01",
                    "period_end": "2024-09-28",
                    "html_anchor": "F_1",
                    "dimensions": {},
                }
            ]
        },
    )
    _write(report_dir / "xbrl" / "contexts.json", {"contexts": {"ctx-1": {"dimensions": {}}}})
    _write(report_dir / "xbrl" / "units.json", {"units": {"USD": "iso4217:USD"}})
    _write(report_dir / "xbrl" / "labels.json", {"labels": {}})
    return company_dir, report_dir, target


def test_source_family_routing_uses_manifest_characteristics_not_market():
    assert (
        source_family_for_manifest({"market": "EU", "source_id": "sec", "document_format": "ixbrl_html"}) == "sec_ixbrl"
    )
    assert source_family_for_manifest({"market": "US", "source_id": "hkex", "document_format": "pdf"}) == "pdf_market"
    assert source_family_for_manifest({"market": "EU", "source_id": "esef", "document_format": "ixbrl"}) == "esef_ixbrl"


def test_derived_analysis_only_compares_same_currency_scope_and_accounting_basis():
    facts = {
        "revenue": [
            {
                "normalized_value": 100,
                "period_end": "2023-12-31",
                "currency": "USD",
                "scope": "consolidated",
                "accounting_basis": "gaap",
            },
            {
                "normalized_value": 125,
                "period_end": "2024-12-31",
                "currency": "USD",
                "scope": "consolidated",
                "accounting_basis": "gaap",
            },
        ]
    }
    assert "增长25.00%" in _change_sentence("收入", facts, "revenue")
    facts["revenue"][0]["currency"] = "EUR"
    assert _change_sentence("收入", facts, "revenue") == ""

    liabilities = {"normalized_value": 40, "period_end": "2024-12-31", "currency": "USD", "scope": "consolidated"}
    assets = {"normalized_value": 100, "period_end": "2024-12-31", "currency": "USD", "scope": "consolidated"}
    assert "40.00%" in _ratio_sentence("资产负债率", liabilities, assets)
    assets["period_end"] = "2023-12-31"
    assert _ratio_sentence("资产负债率", liabilities, assets) == ""


def test_pdf_bundle_is_manifest_bound_currency_neutral_and_read_only(tmp_path):
    company_dir, report_dir, target = _pdf_fixture(tmp_path)
    before = _tree_hash(report_dir)

    bundle = build_analysis_input_bundle(
        research_target=target,
        company_dir=company_dir,
        report_dir=report_dir,
    )

    assert _tree_hash(report_dir) == before
    assert bundle["adapter"]["source_family"] == "pdf_market"
    assert bundle["normalized_facts"][0]["currency"] == "HKD"
    assert bundle["normalized_facts"][0]["scale"] == 1000
    assert bundle["normalized_facts"][0]["evidence_refs"][0]["evidence_id"] == "ev-1"
    assert bundle["evidence_refs"][0]["pdf_page"] == 8
    assert EvidenceRefV1.from_dict(bundle["evidence_refs"][0]).kind == "pdf_table"
    assert NormalizedFactV1.from_dict(bundle["normalized_facts"][0]).currency == "HKD"
    assert "亿元" not in json.dumps(bundle, ensure_ascii=False)


def test_pdf_metric_normalization_infers_scale_from_unit_and_overrides_ratio_currency() -> None:
    amount = normalize_fact(
        {
            "canonical_name": "operating_revenue",
            "metric_name": "Revenue",
            "value": 92_352,
            "unit": "EUR million",
            "currency": "EUR",
            "scale": 1,
            "period": "2025-12-28",
        },
        identity={**IDENTITY, "market": "EU"},
        report={"period_end": "2025-12-28", "reporting_currency": "EUR"},
        source_family="pdf_market",
    )
    ratio = normalize_fact(
        {
            "canonical_name": "weighted_avg_roe",
            "metric_name": "加权平均净资产收益率",
            "value": 11.38,
            "unit": "CNY million",
            "currency": "CNY",
            "scale": 1_000_000,
            "period": "2025-12-31",
        },
        identity={**IDENTITY, "market": "CN"},
        report={"period_end": "2025-12-31", "reporting_currency": "CNY"},
        source_family="pdf_market",
    )

    assert amount["normalized_value"] == 92_352_000_000
    assert amount["scale"] == 1_000_000
    assert amount["unit"] == "EUR"
    assert "scale_unit_conflict:declared=1:unit=EUR million:applied=1000000" in amount["normalization_warnings"]
    assert ratio["normalized_value"] == 11.38
    assert ratio["scale"] == 1
    assert ratio["currency"] is None
    assert ratio["unit"] == "%"
    assert ratio["raw_unit"] == "CNY million"
    assert any(
        item.startswith("metric_unit_semantics_override:weighted_avg_roe") for item in ratio["normalization_warnings"]
    )


def test_bank_pdf_adapter_does_not_promote_financial_subitems_to_profit_or_revenue(tmp_path) -> None:
    company_dir, report_dir, target = _pdf_fixture(tmp_path)
    _write(company_dir / "company.json", {"company_name": "Example Bank"})
    metrics_path = report_dir / "metrics" / "normalized_metrics.json"
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    metrics["metrics"].extend(
        [
            {
                "canonical_name": "net_profit",
                "metric_name": "Net income from financial instruments held for trading or managed on a fair value basis2",
                "value": 19_682,
                "unit": "million",
                "currency": "USD",
                "scale": 1_000_000,
                "period": "2024-09-28",
            },
            {
                "canonical_name": "operating_revenue",
                "metric_name": "- insurance service revenue",
                "value": 3_228,
                "unit": "million",
                "currency": "USD",
                "scale": 1_000_000,
                "period": "2024-09-28",
            },
        ]
    )
    _write(metrics_path, metrics)

    bundle = build_analysis_input_bundle(
        research_target=target,
        company_dir=company_dir,
        report_dir=report_dir,
    )

    by_label = {item["raw_label"]: item for item in bundle["normalized_facts"]}
    trading = by_label["Net income from financial instruments held for trading or managed on a fair value basis2"]
    insurance = by_label["- insurance service revenue"]
    assert trading["metric_key"] == "reported_trading_financial_instruments_income"
    assert insurance["metric_key"] == "reported_insurance_service_revenue"
    assert trading["core_metric_eligible"] is False
    assert insurance["core_metric_eligible"] is False
    assert trading["semantic_status"] == insurance["semantic_status"] == "canonical_conflict"
    assert bundle["source_metadata"]["semantic_conflict_count"] == 2
    assert "financial_metric_canonical_conflict" in bundle["quality"]["degraded_reasons"]


@pytest.mark.parametrize("form_type", ["10-K", "10-Q"])
def test_sec_bundle_reads_existing_ixbrl_artifacts_without_pdf_assumptions(tmp_path, form_type):
    company_dir, report_dir, target = _sec_fixture(tmp_path, form_type=form_type)
    before = _tree_hash(report_dir)

    bundle = build_analysis_input_bundle(
        research_target=target,
        company_dir=company_dir,
        report_dir=report_dir,
        sec_adapter_enabled=True,
    )

    assert _tree_hash(report_dir) == before
    assert bundle["adapter"]["source_family"] == "sec_ixbrl"
    assert bundle["source_metadata"]["filing"]["form_type"] == form_type
    assert bundle["source_metadata"]["filing"]["period_end"] == "2024-09-28"
    assert bundle["normalized_facts"][0]["currency"] == "USD"
    assert bundle["normalized_facts"][0]["accounting_basis"] == "gaap"
    locator = bundle["evidence_refs"][0]
    assert locator["html_anchor"] == "F_1"
    assert locator["xbrl_concept"].startswith("us-gaap:")
    assert "pdf_page" not in locator
    assert bundle["quality"]["status"] == "warning"
    assert bundle["financial_checks"]["status"] == "warning"
    assert "source_quality_warning" in bundle["quality"]["degraded_reasons"]
    assert bundle["source_metadata"]["section_catalog"][0]["role"] == "mda"
    assert bundle["source_metadata"]["document_summary"]["markdown_char_count"] > 0
    assert EvidenceRefV1.from_dict(bundle["evidence_refs"][0]).kind == "xbrl_fact"
    assert NormalizedFactV1.from_dict(bundle["normalized_facts"][0]).accounting_standard == "US_GAAP"


def test_formal_bundle_fails_closed_for_incomplete_or_mismatched_identity(tmp_path):
    company_dir, report_dir, target = _pdf_fixture(tmp_path)
    incomplete = copy.deepcopy(target)
    incomplete["research_identity"]["parse_run_id"] = ""
    with pytest.raises(SourceAdapterError) as exc_info:
        build_analysis_input_bundle(research_target=incomplete, company_dir=company_dir, report_dir=report_dir)
    assert exc_info.value.code == "research_identity_incomplete"

    mismatch = copy.deepcopy(target)
    mismatch["research_identity"]["filing_id"] = "HK:00005:other"
    with pytest.raises(SourceAdapterError) as exc_info:
        build_analysis_input_bundle(research_target=mismatch, company_dir=company_dir, report_dir=report_dir)
    assert exc_info.value.code == "research_identity_mismatch"


def test_sec_adapter_can_be_disabled_independently(tmp_path):
    company_dir, report_dir, target = _sec_fixture(tmp_path, form_type="10-K")
    with pytest.raises(SourceAdapterError) as exc_info:
        build_analysis_input_bundle(
            research_target=target,
            company_dir=company_dir,
            report_dir=report_dir,
            sec_adapter_enabled=False,
        )
    assert exc_info.value.code == "source_adapter_unavailable"


def test_manifest_path_traversal_is_rejected(tmp_path):
    company_dir, report_dir, target = _pdf_fixture(tmp_path)
    manifest = json.loads((report_dir / "manifest.json").read_text(encoding="utf-8"))
    manifest["artifacts"] = {"document_full": "../../outside.json"}
    (report_dir / "report.md").unlink()
    _write(report_dir / "manifest.json", manifest)
    _write(company_dir / "outside.json", {})
    with pytest.raises(SourceAdapterError) as exc_info:
        build_analysis_input_bundle(research_target=target, company_dir=company_dir, report_dir=report_dir)
    assert exc_info.value.code == "unsafe_path_rejected"


@pytest.mark.parametrize(
    ("source_kind", "form_type"),
    (("pdf", None), ("sec", "10-K"), ("sec", "10-Q")),
)
def test_formal_bundle_runner_emits_shared_sidecar_without_mutating_sources(tmp_path, source_kind, form_type):
    if source_kind == "sec":
        company_dir, report_dir, target = _sec_fixture(tmp_path, form_type=form_type)
        bundle = build_analysis_input_bundle(
            research_target=target,
            company_dir=company_dir,
            report_dir=report_dir,
            sec_adapter_enabled=True,
        )
    else:
        company_dir, report_dir, target = _pdf_fixture(tmp_path)
        bundle = build_analysis_input_bundle(
            research_target=target,
            company_dir=company_dir,
            report_dir=report_dir,
        )
    before = _tree_hash(report_dir)
    index_path = company_dir / "_index.json"
    index_path.write_text('{"immutable":"sentinel"}\n', encoding="utf-8")
    index_before = index_path.read_bytes()
    bundle_path = tmp_path / "runtime" / f"{source_kind}-{form_type or 'annual'}-bundle.json"
    _write(bundle_path, bundle)
    output_prefix = company_dir / "analysis" / f"{source_kind}-{form_type or 'annual'}-report"
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS_DIR / "run_analysis_report.py"),
            "--input-bundle",
            str(bundle_path),
            "--output-prefix",
            str(output_prefix),
            "--force",
        ],
        cwd=SCRIPTS_DIR,
        env=os.environ.copy(),
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    result = json.loads(completed.stdout)
    assert result["ok"] is True
    assert result["pipeline_mode"] == "formal_analysis_input_bundle"
    assert result["research_identity"] == target["research_identity"]
    assert _tree_hash(report_dir) == before
    assert index_path.read_bytes() == index_before
    assert Path(result["checkpoints"]["research_pack_manifest"]).is_file()
    assert Path(result["checkpoints"]["research_pack_validation"]).is_file()
    assert Path(result["checkpoints"]["research_pack_merge_manifest"]).is_file()
    assert result["checkpoints"]["publication_staging_validation"]["ok"] is True
    sidecar = json.loads(Path(result["files"]["sidecar"]).read_text(encoding="utf-8"))
    assert sidecar["schema_version"] == "siq_agent_artifact_v2"
    assert sidecar["research_target"]["research_identity"] == target["research_identity"]
    assert sidecar["source_report_id"] == "report-1"
    artifact = AgentArtifactV2.from_dict(sidecar)
    assert artifact.research_target is not None
    assert Path(result["files"]["sidecar"]).name == f"{artifact.artifact_id}.artifact.json"
    assert Path(result["files"]["html"]).name == artifact.html_file == f"{artifact.artifact_id}.html"
    assert artifact.status == ("degraded" if source_kind == "sec" else "completed")
    for public_file in (
        result["files"]["md"],
        result["files"]["json"],
        result["files"]["html"],
        result["files"]["sidecar"],
    ):
        assert str(company_dir) not in Path(public_file).read_text(encoding="utf-8")
    html_text = Path(result["files"]["html"]).read_text(encoding="utf-8")
    assert "A 股" not in html_text and "A股" not in html_text
    assert "亿元" not in html_text
    assert ("USD million" if source_kind == "sec" else "HKD million") in html_text
    assert '<details class="evidence-catalog">' in html_text
    assert '<details open class="evidence-catalog">' not in html_text
    report_json = json.loads(Path(result["files"]["json"]).read_text(encoding="utf-8"))
    assert report_json["research_pack"]["validation_status"] == "pass"
    assert report_json["research_pack"]["merge_schema_version"] == "siq_analysis_research_pack_merge_v2"
    executive = next(item for item in report_json["sections"] if item["section_id"] == "executive_summary")
    assert "financial_modeler" in executive["research_pack_refs"]["agent_ids"]
    assert executive["research_pack_refs"]["finding_ids"]
    if source_kind == "sec":
        assert "https://www.sec.gov/example.htm#F_1" in html_text
        assert 'target="_blank" rel="noopener noreferrer"' in html_text
        assert report_json["adapter"]["source_family"] == "sec_ixbrl"
        assert "pdf_page" not in json.dumps(report_json["evidence_refs"])
        assert any(item.get("html_anchor") == "F_1" for item in report_json["evidence_refs"])
        profitability = next(item for item in report_json["sections"] if item["section_id"] == "profitability")
        assert any("管理层讨论摘要" in line for line in profitability["content"])
        assert any(
            item.get("section_role") == "mda" and item.get("local_source_id") == "sections/mda.md"
            for item in report_json["evidence_refs"]
        )
        assert profitability["evidence_ids"]
        assert "schema_version: sec_section_v1" not in json.dumps(profitability, ensure_ascii=False)
        assert "Management discussion." not in html_text.split('<details class="evidence-catalog">', 1)[0]


def test_formal_bundle_runner_rejects_cn_before_rendering(tmp_path):
    company_dir, report_dir, target = _pdf_fixture(tmp_path)
    target["research_identity"] = {
        **target["research_identity"],
        "market": "CN",
        "company_id": "CN:000001",
        "filing_id": "CN:000001:2024-annual",
    }
    manifest_path = report_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(target["research_identity"])
    _write(manifest_path, manifest)
    bundle = build_analysis_input_bundle(
        research_target=target,
        company_dir=company_dir,
        report_dir=report_dir,
    )
    bundle_path = tmp_path / "runtime" / "cn-bundle.json"
    _write(bundle_path, bundle)
    output_prefix = company_dir / "analysis" / "must-not-exist"

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS_DIR / "run_analysis_report.py"),
            "--input-bundle",
            str(bundle_path),
            "--output-prefix",
            str(output_prefix),
            "--force",
        ],
        cwd=SCRIPTS_DIR,
        env=os.environ.copy(),
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 2
    result = json.loads(completed.stdout)
    assert result["ok"] is False
    assert result["stage"] == "unsupported_market"
    assert result["details"] == {
        "market": "CN",
        "supported_markets": ["EU", "HK", "JP", "KR", "US"],
    }
    assert not output_prefix.with_suffix(".html").exists()
    assert not output_prefix.with_suffix(".json").exists()
    assert not output_prefix.with_suffix(".md").exists()


def test_renderer_bounds_claim_evidence_catalog_and_keeps_full_json_audit_payload(tmp_path):
    company_dir, report_dir, target = _pdf_fixture(tmp_path)
    source_map_path = report_dir / "qa" / "source_map.json"
    source_map = json.loads(source_map_path.read_text(encoding="utf-8"))
    for index in range(2, 129):
        source_map["entries"].append(
            {
                "evidence_id": f"ev-{index}",
                "source_type": "pdf_statement_table",
                "target": f"supplemental-note-{index}",
                "pdf_page_number": index + 10,
                "table_index": index,
                "quote_text": f"Revenue supporting row {index}",
            }
        )
    _write(source_map_path, source_map)
    bundle = build_analysis_input_bundle(
        research_target=target,
        company_dir=company_dir,
        report_dir=report_dir,
    )
    work_dir = company_dir / "analysis" / ".work" / "evidence-catalog"
    packs = build_formal_research_packs(bundle, work_dir=work_dir)
    result = render_analysis_bundle(
        bundle,
        output_prefix=company_dir / "analysis" / "evidence-catalog",
        research_pack_result=packs,
        staging_dir=work_dir / "publish_staging",
    )

    report = json.loads(Path(result["files"]["json"]).read_text(encoding="utf-8"))
    sidecar = json.loads(Path(result["files"]["sidecar"]).read_text(encoding="utf-8"))
    rendered = Path(result["files"]["html"]).read_text(encoding="utf-8")
    catalog_start = rendered.index('<details class="evidence-catalog">')
    visible_report = rendered[:catalog_start]
    evidence_ids = {item["evidence_id"] for item in report["evidence_refs"]}
    claim_evidence_ids = {
        evidence_id
        for claim in report["claims"]
        for evidence_id in claim["evidence_ids"]
    }
    rendered_catalog_count = rendered.count('class="evidence-reference"')

    assert report["claims"]
    assert report["report_template"]["template_id"] == "siq_overseas_annual_report_v1"
    assert report["report_template"]["market"] == "HK"
    assert "max-height:min(70vh,960px)" in rendered
    executive = next(item for item in report["sections"] if item["section_id"] == "executive_summary")
    assert executive["research_pack_item_count"] > 0
    assert {"multi_period_trend", "cross_statement_check"} <= set(executive["analysis_dimensions"])
    assert sidecar["metadata"]["claims"] == report["claims"]
    assert sidecar["metadata"]["report_template"]["template_id"] == "siq_overseas_annual_report_v1"
    assert all({"claim_id", "claim", "claim_type", "evidence_ids"} <= claim.keys() for claim in report["claims"])
    assert '<details class="evidence-catalog">' in rendered
    assert '<details class="evidence-catalog" open' not in rendered
    assert len(evidence_ids) > MAX_PRESENTATION_EVIDENCE_ITEMS
    assert rendered_catalog_count == len(claim_evidence_ids) <= MAX_PRESENTATION_EVIDENCE_ITEMS
    assert all(f'id="evidence-{evidence_id}"' in rendered for evidence_id in claim_evidence_ids)
    assert any(f'id="evidence-{evidence_id}"' not in rendered for evidence_id in evidence_ids - claim_evidence_ids)
    assert sidecar["metadata"]["evidence_catalog"] == {
        "rendered_count": rendered_catalog_count,
        "total_count": len(evidence_ids),
        "limit": MAX_PRESENTATION_EVIDENCE_ITEMS,
        "full_evidence_file": Path(result["files"]["json"]).name,
    }
    assert len(rendered.encode("utf-8")) <= 512 * 1024
    assert "全部" in rendered and "JSON 结构化附件" in rendered
    assert "审计编号：ev-1" not in visible_report
    assert "<code>" not in visible_report
    assert "营业收入（第 8 页" in visible_report


def test_semantic_conflict_fact_remains_audit_only(tmp_path):
    company_dir, report_dir, target = _pdf_fixture(tmp_path)
    bundle = build_analysis_input_bundle(
        research_target=target,
        company_dir=company_dir,
        report_dir=report_dir,
    )
    conflicting = copy.deepcopy(bundle["normalized_facts"][0])
    conflicting.update(
        fact_id="conflict-net-profit",
        metric_key="net_profit",
        canonical_name="net_profit",
        raw_label="Trading financial instruments income",
        normalized_value=99_000_000_000,
        value=99_000_000_000,
        core_metric_eligible=False,
        semantic_status="canonical_conflict",
    )
    bundle["normalized_facts"].append(conflicting)
    work_dir = company_dir / "analysis" / ".work" / "semantic-conflict"
    packs = build_formal_research_packs(bundle, work_dir=work_dir)
    result = render_analysis_bundle(
        bundle,
        output_prefix=company_dir / "analysis" / "semantic-conflict",
        research_pack_result=packs,
        staging_dir=work_dir / "publish_staging",
    )

    report = json.loads(Path(result["files"]["json"]).read_text(encoding="utf-8"))
    rendered = Path(result["files"]["html"]).read_text(encoding="utf-8")
    assert any(item["fact_id"] == "conflict-net-profit" for item in report["facts"])
    assert report["excluded_fact_count"] == 1
    assert all(item.get("metric_key") != "net_profit" for item in report["claims"])
    assert all(item.get("metric_key") != "net_profit" for item in report["kpis"])
    assert "99.00 HKD billion" not in rendered


def test_formal_renderer_rejects_output_prefix_equal_to_analysis_directory(tmp_path):
    company_dir, report_dir, target = _pdf_fixture(tmp_path)
    bundle = build_analysis_input_bundle(
        research_target=target,
        company_dir=company_dir,
        report_dir=report_dir,
    )
    analysis_dir = Path(bundle["server_paths"]["analysis_dir"])
    work_dir = analysis_dir / ".work" / "unsafe-output-prefix"
    packs = build_formal_research_packs(bundle, work_dir=work_dir)

    with pytest.raises(SourceAdapterError) as exc_info:
        render_analysis_bundle(
            bundle,
            output_prefix=analysis_dir,
            research_pack_result=packs,
            staging_dir=work_dir / "publish_staging",
        )

    assert exc_info.value.code == "unsafe_path_rejected"


def test_publish_rolls_back_sidecar_and_payloads_when_final_html_rename_fails(tmp_path, monkeypatch):
    staging = tmp_path / "analysis" / ".work" / "run" / "publish_staging"
    final = tmp_path / "analysis"
    staging.mkdir(parents=True)
    final.mkdir(exist_ok=True)
    staged_paths = {key: staging / f"artifact.{key}" for key in ("sidecar", "md", "json", "html")}
    final_paths = {key: final / f"artifact.{key}" for key in staged_paths}
    for key, path in staged_paths.items():
        path.write_text(key, encoding="utf-8")
    original_replace = Path.replace

    def fail_final_html(self, target):
        if self == staged_paths["html"] and Path(target) == final_paths["html"]:
            raise OSError("simulated final HTML rename failure")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_final_html)
    with pytest.raises(SourceAdapterError) as exc_info:
        _publish_staged_files(staged_paths, final_paths, allow_overwrite=False)

    assert exc_info.value.code == "artifact_publish_failed"
    assert not any(path.exists() for path in final_paths.values())


def test_pdf_latest_fallback_cannot_rebind_another_report_identity(tmp_path):
    company_dir, report_dir, target = _pdf_fixture(tmp_path)
    (report_dir / "metrics" / "normalized_metrics.json").unlink()
    (report_dir / "qa" / "source_map.json").unlink()
    other_report = company_dir / "reports" / "report-2"
    other_report.mkdir(parents=True)
    _write(other_report / "manifest.json", {"report_id": "report-2"})
    other_identity = {
        "report_id": "report-2",
        "filing_id": "HK:00005:filing-2",
        "parse_run_id": "parse-2",
    }
    _write(
        company_dir / "metrics" / "latest" / "normalized_metrics.json",
        {
            **other_identity,
            "metrics": [
                {
                    **other_identity,
                    "canonical_name": "revenue",
                    "value": 999,
                    "currency": "HKD",
                    "period": "2024-09-28",
                }
            ],
        },
    )
    _write(
        company_dir / "evidence" / "source_map_latest.json",
        {
            **other_identity,
            "entries": [
                {
                    **other_identity,
                    "evidence_id": "wrong-report",
                    "target": "revenue",
                    "pdf_page_number": 1,
                    "task_id": "other-task",
                }
            ],
        },
    )

    bundle = build_analysis_input_bundle(
        research_target=target,
        company_dir=company_dir,
        report_dir=report_dir,
    )

    assert bundle["normalized_facts"] == []
    assert bundle["evidence_refs"] == []
    assert "structured_metrics_unavailable" in bundle["quality"]["degraded_reasons"]
    assert "evidence_map_unavailable" in bundle["quality"]["degraded_reasons"]


def test_sec_10q_disambiguates_qtd_ytd_contexts_and_non_gaap(tmp_path):
    company_dir, report_dir, target = _sec_fixture(tmp_path, form_type="10-Q")
    metrics_path = report_dir / "metrics" / "normalized_metrics.json"
    facts_path = report_dir / "xbrl" / "facts_raw.json"
    contexts_path = report_dir / "xbrl" / "contexts.json"
    source_map_path = report_dir / "qa" / "source_map.json"
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    facts = json.loads(facts_path.read_text(encoding="utf-8"))
    source_map = json.loads(source_map_path.read_text(encoding="utf-8"))

    metrics["metrics"][0].update(
        period_start="2024-07-01",
        period_end="2024-09-28",
        raw={"context_id": "ctx-qtd"},
    )
    facts["facts"][0].update(
        context_ref="ctx-qtd",
        period_start="2024-07-01",
        period_end="2024-09-28",
        duration_days=89,
    )
    source_map["entries"][0].update(context_ref="ctx-qtd")
    contexts = {
        "contexts": {
            "ctx-qtd": {
                "context_ref": "ctx-qtd",
                "period_start": "2024-07-01",
                "period_end": "2024-09-28",
                "duration_days": 89,
                "dimensions": {},
            },
            "ctx-ytd": {
                "context_ref": "ctx-ytd",
                "period_start": "2024-01-01",
                "period_end": "2024-09-28",
                "duration_days": 271,
                "dimensions": {},
            },
        }
    }

    def append_metric(
        *,
        metric_id,
        fact_id,
        context_ref,
        concept,
        label,
        value,
        period_start,
        taxonomy,
        is_extension=False,
        is_non_gaap=False,
    ):
        metrics["metrics"].append(
            {
                "metric_id": metric_id,
                "canonical_name": "revenue" if concept.startswith("us-gaap:") else metric_id,
                "concept": concept,
                "label": label,
                "value": value,
                "unit": "USD",
                "currency": "USD",
                "period_start": period_start,
                "period_end": "2024-09-28",
                "fiscal_year": 2024,
                "fiscal_period": "Q3",
                "raw_fact_id": fact_id,
                "raw": {"context_id": context_ref},
                "is_non_gaap": is_non_gaap,
            }
        )
        facts["facts"].append(
            {
                "fact_id": fact_id,
                "concept": concept,
                "taxonomy": taxonomy,
                "is_extension": is_extension,
                "value_text": value,
                "context_ref": context_ref,
                "period_start": period_start,
                "period_end": "2024-09-28",
                "duration_days": 271 if context_ref == "ctx-ytd" else 89,
                "html_anchor": fact_id,
                "dimensions": {},
            }
        )
        source_map["entries"].append(
            {
                "evidence_id": f"ev-{fact_id}",
                "source_type": "sec_xbrl_fact",
                "fact_id": fact_id,
                "xbrl_tag": concept,
                "context_ref": context_ref,
                "html_anchor": fact_id,
                "source_url": "https://www.sec.gov/example.htm",
                "quote_text": str(value),
            }
        )

    append_metric(
        metric_id="metric-ytd",
        fact_id="fact-ytd",
        context_ref="ctx-ytd",
        concept="us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
        label="Revenue YTD",
        value="2500000",
        period_start="2024-01-01",
        taxonomy="us-gaap",
    )
    append_metric(
        metric_id="adjusted_ebitda",
        fact_id="fact-nongaap",
        context_ref="ctx-qtd",
        concept="test:AdjustedEBITDA",
        label="Adjusted EBITDA (non-GAAP)",
        value="400000",
        period_start="2024-07-01",
        taxonomy="test",
        is_extension=True,
        is_non_gaap=True,
    )
    append_metric(
        metric_id="custom_revenue",
        fact_id="fact-extension",
        context_ref="ctx-qtd",
        concept="test:CustomRevenueDisclosure",
        label="Custom Revenue Disclosure",
        value="500000",
        period_start="2024-07-01",
        taxonomy="test",
        is_extension=True,
    )
    _write(metrics_path, metrics)
    _write(facts_path, facts)
    _write(contexts_path, contexts)
    _write(source_map_path, source_map)

    bundle = build_analysis_input_bundle(
        research_target=target,
        company_dir=company_dir,
        report_dir=report_dir,
        sec_adapter_enabled=True,
    )

    revenue_facts = [item for item in bundle["normalized_facts"] if item["metric_key"] == "revenue"]
    assert {item["qtd_ytd_type"] for item in revenue_facts} == {"qtd", "ytd"}
    assert len({item["context_signature"] for item in revenue_facts}) == 2
    assert {item["context_ref"] for item in revenue_facts} == {"ctx-qtd", "ctx-ytd"}
    by_key = {item["metric_key"]: item for item in bundle["normalized_facts"]}
    assert by_key["adjusted_ebitda"]["accounting_basis"] == "non_gaap"
    assert by_key["custom_revenue"]["accounting_basis"] == "company_extension"
    assert bundle["source_metadata"]["xbrl_summary"]["period_basis_counts"]["qtd"] >= 1
    assert bundle["source_metadata"]["xbrl_summary"]["period_basis_counts"]["ytd"] >= 1


@pytest.mark.parametrize(
    ("kind", "company_name", "primary_metric", "ratio_metric"),
    (
        ("bank", "Example Bank", "net_interest_income", "capital_adequacy_ratio"),
        ("insurance", "Example Insurance", "insurance_revenue", "solvency_ratio"),
    ),
)
def test_financial_institution_renderer_does_not_apply_industrial_metrics(
    tmp_path,
    kind,
    company_name,
    primary_metric,
    ratio_metric,
):
    company_dir, report_dir, target = _pdf_fixture(tmp_path)
    _write(company_dir / "company.json", {"company_short_name": company_name})
    metrics_path = report_dir / "metrics" / "normalized_metrics.json"
    source_map_path = report_dir / "qa" / "source_map.json"
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    source_map = json.loads(source_map_path.read_text(encoding="utf-8"))
    for index, (metric_key, value, unit) in enumerate(
        ((primary_metric, 800, "HKD million"), (ratio_metric, 14.5, "%")),
        20,
    ):
        metrics["metrics"].append(
            {
                "canonical_name": metric_key,
                "label": metric_key,
                "value": value,
                "raw_value": str(value),
                "currency": "HKD" if unit != "%" else None,
                "unit": unit,
                "scale": 1_000_000 if unit != "%" else 1,
                "period": "2024-09-28",
                "source": {"pdf_page_number": index, "table_index": index, "quote_text": metric_key},
            }
        )
        source_map["entries"].append(
            {
                "evidence_id": f"ev-{metric_key}",
                "source_type": "pdf_statement_table",
                "target": metric_key,
                "pdf_page_number": index,
                "table_index": index,
                "quote_text": metric_key,
            }
        )
    _write(metrics_path, metrics)
    _write(source_map_path, source_map)
    source_hash = _tree_hash(report_dir)

    bundle = build_analysis_input_bundle(
        research_target=target,
        company_dir=company_dir,
        report_dir=report_dir,
    )
    assert bundle["entity_profile"]["kind"] == kind
    assert bundle["capabilities"]["operating_cash_flow_analysis"] is False
    ratio = next(item for item in bundle["normalized_facts"] if item["metric_key"] == ratio_metric)
    assert ratio["currency"] is None

    work_dir = company_dir / "analysis" / ".work" / f"{kind}-fixture"
    packs = build_formal_research_packs(bundle, work_dir=work_dir)
    result = render_analysis_bundle(
        bundle,
        output_prefix=company_dir / "analysis" / f"{kind}-fixture",
        research_pack_result=packs,
        staging_dir=work_dir / "publish_staging",
    )
    report = json.loads(Path(result["files"]["json"]).read_text(encoding="utf-8"))
    cash_flow = next(item for item in report["sections"] if item["section_id"] == "cash_flow")
    revenue_quality = next(item for item in report["sections"] if item["section_id"] == "revenue_quality")
    capital = next(item for item in report["sections"] if item["section_id"] == "capital_allocation")
    assert cash_flow["status"] == "not_applicable"
    assert any("不适用" in item for item in cash_flow["content"])
    assert any(("净利息收入" if kind == "bank" else "保险服务收入") in item for item in revenue_quality["content"])
    assert any(("资本充足率" if kind == "bank" else "偿付能力充足率") in item for item in capital["content"])
    assert _tree_hash(report_dir) == source_hash


def test_formal_renderer_emits_responsive_evidence_bound_financial_charts(tmp_path):
    company_dir, report_dir, target = _pdf_fixture(tmp_path)
    metrics_path = report_dir / "metrics" / "normalized_metrics.json"
    source_map_path = report_dir / "qa" / "source_map.json"
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    source_map = json.loads(source_map_path.read_text(encoding="utf-8"))
    series = (
        ("2022-09-28", 900, 120, 90),
        ("2023-09-28", 1050, 145, 110),
        ("2024-09-28", 1200, 170, 130),
    )
    page = 30
    for period, revenue, operating_profit, parent_profit in series:
        for metric_key, value in (
            ("operating_revenue", revenue),
            ("operating_profit", operating_profit),
            ("parent_net_profit", parent_profit),
        ):
            page += 1
            metrics["metrics"].append(
                {
                    "canonical_name": metric_key,
                    "label": metric_key,
                    "value": value,
                    "raw_value": str(value),
                    "currency": "HKD",
                    "unit": "HKD million",
                    "scale": 1_000_000,
                    "period": period,
                    "source": {"pdf_page_number": page, "table_index": page, "quote_text": metric_key},
                }
            )
            source_map["entries"].append(
                {
                    "evidence_id": f"ev-{metric_key}-{period}",
                    "source_type": "pdf_statement_table",
                    "target": metric_key,
                    "pdf_page_number": page,
                    "table_index": page,
                    "quote_text": metric_key,
                }
            )
    for metric_key, value in (("total_assets", 5000), ("total_liabilities", 4100)):
        page += 1
        metrics["metrics"].append(
            {
                "canonical_name": metric_key,
                "label": metric_key,
                "value": value,
                "raw_value": str(value),
                "currency": "HKD",
                "unit": "HKD million",
                "scale": 1_000_000,
                "period": "2024-09-28",
                "source": {"pdf_page_number": page, "table_index": page, "quote_text": metric_key},
            }
        )
        source_map["entries"].append(
            {
                "evidence_id": f"ev-{metric_key}",
                "source_type": "pdf_statement_table",
                "target": metric_key,
                "pdf_page_number": page,
                "table_index": page,
                "quote_text": metric_key,
            }
        )
    _write(metrics_path, metrics)
    _write(source_map_path, source_map)

    bundle = build_analysis_input_bundle(
        research_target=target,
        company_dir=company_dir,
        report_dir=report_dir,
    )
    work_dir = company_dir / "analysis" / ".work" / "visual-quality"
    packs = build_formal_research_packs(bundle, work_dir=work_dir)
    result = render_analysis_bundle(
        bundle,
        output_prefix=company_dir / "analysis" / "visual-quality",
        research_pack_result=packs,
        staging_dir=work_dir / "publish_staging",
    )

    report = json.loads(Path(result["files"]["json"]).read_text(encoding="utf-8"))
    rendered = Path(result["files"]["html"]).read_text(encoding="utf-8")
    assert {item["kind"] for item in report["visuals"]} == {"trend", "structure", "profit_bridge"}
    assert len(next(item for item in report["visuals"] if item["kind"] == "trend")["points"]) == 3
    assert 'class="financial-chart"' in rendered
    assert 'data-chart-kind="trend"' in rendered
    assert 'data-chart-kind="structure"' in rendered
    assert 'data-chart-kind="profit_bridge"' in rendered
    assert 'role="img"' in rendered
    assert 'href="#evidence-' in rendered
    assert "@media(max-width:760px)" in rendered
    assert "aspect-ratio:720/310" in rendered
    assert "HKD" in rendered
    assert "亿元" not in rendered


@pytest.mark.parametrize(
    ("market", "currency"),
    (("HK", "HKD"), ("JP", "JPY"), ("KR", "KRW"), ("EU", "EUR")),
)
def test_overseas_pdf_markets_share_formal_pack_and_renderer_without_currency_assumptions(tmp_path, market, currency):
    company_dir, report_dir, target = _pdf_fixture(tmp_path / market.lower())
    identity = {
        "market": market,
        "company_id": f"{market}:TEST",
        "filing_id": f"{market}:TEST:filing-1",
        "parse_run_id": "parse-1",
    }
    target["research_identity"] = identity
    target["source_report"]["reporting_currency"] = currency
    manifest_path = report_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(identity)
    _write(manifest_path, manifest)
    metrics_path = report_dir / "metrics" / "normalized_metrics.json"
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    metrics["metrics"][0].update(currency=currency, unit=f"{currency} thousand")
    _write(metrics_path, metrics)
    before = _tree_hash(report_dir)

    bundle = build_analysis_input_bundle(
        research_target=target,
        company_dir=company_dir,
        report_dir=report_dir,
    )
    work_dir = company_dir / "analysis" / ".work" / f"{market}-fixture"
    packs = build_formal_research_packs(bundle, work_dir=work_dir)
    result = render_analysis_bundle(
        bundle,
        output_prefix=company_dir / "analysis" / f"{market}-fixture",
        research_pack_result=packs,
        staging_dir=work_dir / "publish_staging",
    )

    sidecar = json.loads(Path(result["files"]["sidecar"]).read_text(encoding="utf-8"))
    html = Path(result["files"]["html"]).read_text(encoding="utf-8")
    report = json.loads(Path(result["files"]["json"]).read_text(encoding="utf-8"))
    assert sidecar["research_target"]["research_identity"] == identity
    assert sidecar["source_family"] == "pdf_market"
    assert report["market_policy"]["market"]["code"] == market
    assert all(
        len(report["market_policy"]["sections"][chapter]) >= 2
        for chapter in ("business_overview", "risk_factors", "controls", "accounting_quality", "tracking")
    )
    assert currency in html
    if currency != "CNY":
        assert "亿元" not in html
    assert _tree_hash(report_dir) == before


def test_cn_legacy_evidence_accepts_exact_report_and_task_id_without_filing_id(tmp_path):
    company_dir, report_dir, target = _pdf_fixture(tmp_path)
    identity = {
        "market": "CN",
        "company_id": "CN:TEST",
        "filing_id": "CN:TEST:report-1",
        "parse_run_id": "task-cn-1",
    }
    target["research_identity"] = identity
    manifest_path = report_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(identity)
    _write(manifest_path, manifest)
    (report_dir / "qa" / "source_map.json").unlink()
    _write(
        company_dir / "evidence" / "pdf_refs.json",
        {
            "entries": [
                {
                    "evidence_id": "cn-authoritative",
                    "report_id": "report-1",
                    "task_id": "task-cn-1",
                    "target": "revenue",
                    "pdf_page_number": 8,
                    "table_index": 2,
                    "quote_text": "Revenue 1,200",
                }
            ]
        },
    )

    bundle = build_analysis_input_bundle(
        research_target=target,
        company_dir=company_dir,
        report_dir=report_dir,
    )

    assert any(item["evidence_id"] == "cn-authoritative" for item in bundle["evidence_refs"])
