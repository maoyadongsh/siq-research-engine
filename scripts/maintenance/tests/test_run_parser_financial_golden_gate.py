import hashlib
import importlib.util
import json
from pathlib import Path


def _load_module():
    source = Path(__file__).resolve().parents[1] / "run_parser_financial_golden_gate.py"
    spec = importlib.util.spec_from_file_location("run_parser_financial_golden_gate_under_test", source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_manifest(path: Path, case: dict) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": "siq_parser_financial_golden_manifest_v1",
                "cases": [case],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _bank_markdown() -> str:
    return """
# 工商银行2025年度报告

财务指标
<table>
  <tr><td></td><td>2025</td><td>2024</td></tr>
  <tr><td>全年经营成果(人民币百万元)</td><td>全年经营成果(人民币百万元)</td><td>全年经营成果(人民币百万元)</td></tr>
  <tr><td>利息净收入</td><td>635,126</td><td>637,405</td></tr>
  <tr><td>营业收入</td><td>838,270</td><td>821,803</td></tr>
  <tr><td>营业利润</td><td>424,111</td><td>420,885</td></tr>
  <tr><td>税前利润</td><td>424,435</td><td>421,827</td></tr>
  <tr><td>净利润</td><td>370,766</td><td>366,946</td></tr>
  <tr><td>归属于母公司股东的净利润</td><td>368,562</td><td>365,863</td></tr>
  <tr><td>经营活动产生的现金流量净额</td><td>1,890,530</td><td>579,194</td></tr>
  <tr><td>于报告期末(人民币百万元)</td><td>于报告期末(人民币百万元)</td><td>于报告期末(人民币百万元)</td></tr>
  <tr><td>资产总额</td><td>53,477,773</td><td>48,821,746</td></tr>
  <tr><td>负债总额</td><td>49,205,749</td><td>44,834,480</td></tr>
</table>
""".strip()


def test_default_manifest_contract_passes():
    module = _load_module()

    report = module.run_gate(mode="contract")

    assert report["passed"] is True
    assert report["summary"]["case_count"] == 1
    assert report["validation_errors"] == []


def test_contract_rejects_unsafe_source_path(tmp_path):
    module = _load_module()
    manifest = tmp_path / "cases.json"
    _write_manifest(
        manifest,
        {
            "case_id": "unsafe",
            "source_path": "../secret.md",
            "source_sha256": "a" * 64,
            "expected_metrics": [{"canonical_name": "operating_revenue", "period": "2025", "value": 1}],
        },
    )

    report = module.run_gate(mode="contract", manifest_path=manifest)

    assert report["passed"] is False
    assert "cases[1].source_path must be a safe relative path" in report["validation_errors"]


def test_offline_sample_gate_checks_hash_metrics_and_quality(tmp_path):
    module = _load_module()
    sample_root = tmp_path / "samples"
    sample_root.mkdir()
    markdown = _bank_markdown()
    sample = sample_root / "icbc.md"
    sample.write_text(markdown, encoding="utf-8")
    manifest = tmp_path / "cases.json"
    _write_manifest(
        manifest,
        {
            "case_id": "icbc-test",
            "source_path": "icbc.md",
            "source_sha256": hashlib.sha256(sample.read_bytes()).hexdigest(),
            "min_bytes": 100,
            "min_lines": 10,
            "forbidden_quality_flag_codes": ["key_metric_value_conflict"],
            "expected_metrics": [
                {"canonical_name": "operating_revenue", "period": "2025", "value": 838270000000.0},
                {"canonical_name": "bank_net_interest_income", "period": "2025", "value": 635126000000.0},
            ],
        },
    )

    report = module.run_gate(mode="offline-samples", manifest_path=manifest, sample_root=sample_root)

    assert report["passed"] is True
    assert report["summary"] == {"case_count": 1, "passed": 1, "failed": 0, "missing": 0}
    assert report["results"][0]["quality_flags"] == []


def test_offline_sample_gate_fails_when_sample_is_missing(tmp_path):
    module = _load_module()
    manifest = tmp_path / "cases.json"
    _write_manifest(
        manifest,
        {
            "case_id": "missing",
            "source_path": "missing.md",
            "source_sha256": "a" * 64,
            "expected_metrics": [{"canonical_name": "operating_revenue", "period": "2025", "value": 1}],
        },
    )

    report = module.run_gate(mode="offline-samples", manifest_path=manifest, sample_root=tmp_path)

    assert report["passed"] is False
    assert report["summary"]["missing"] == 1
    assert report["results"][0]["errors"] == ["source file missing"]
