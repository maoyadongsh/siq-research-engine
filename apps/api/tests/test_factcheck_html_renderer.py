from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
FACTCHECK_SCRIPTS = (
    REPO_ROOT / "agents" / "hermes" / "profiles" / "siq_factchecker_multi_market" / "scripts"
)
if str(FACTCHECK_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(FACTCHECK_SCRIPTS))

_RENDERER_SPEC = importlib.util.spec_from_file_location(
    "siq_factchecker_multi_market_html_renderer_test",
    FACTCHECK_SCRIPTS / "generate_factcheck_html.py",
)
assert _RENDERER_SPEC and _RENDERER_SPEC.loader
_RENDERER_MODULE = importlib.util.module_from_spec(_RENDERER_SPEC)
_RENDERER_SPEC.loader.exec_module(_RENDERER_MODULE)
generate_html = _RENDERER_MODULE.generate_html


def _write_report(
    tmp_path: Path,
    *,
    summary: dict,
    evidence: list[dict],
    claim_verdicts: list[dict] | None = None,
) -> Path:
    report = {
        "schema_version": "siq_market_factcheck_v1",
        "verdict": "request_changes",
        "company_id": "HK:00005",
        "report_file": "analysis-example.html",
        "verified_at": "2026-07-16T00:00:00Z",
        "summary": summary,
        "checks": {
            "identity_consistency": {"status": "pass", "issues": []},
            "claim_support": {
                "status": "warning",
                "issues": [{"severity": "warning", "message": "一条声明需要补充证据"}],
            },
        },
        "evidence_summary": evidence,
        "claim_verdicts": claim_verdicts or [],
        "calculation_audit": [],
        "recommendations": ["补充声明与证据的结构化绑定。"],
    }
    path = tmp_path / "factcheck.json"
    path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    return path


def test_evidence_ids_are_secondary_and_complete_audit_is_collapsed(tmp_path: Path) -> None:
    metric_names = [
        "operating_revenue",
        "net_profit",
        "total_assets",
        "total_liabilities",
        "operating_cash_flow_net",
        "inventory",
    ]
    evidence = [
        {
            "source_type": "result_markdown_formal_statement_window",
            "evidence_id": f"00005-2025-annual-metric-{index:05d}",
            "canonical_name": metric_names[(index - 1) % len(metric_names)],
            "period": "2025-12-31",
            "raw_value": index * 100,
            "unit": "USD million",
            "pdf_page_number": index + 10,
            "table_index": index,
            "quote": f"{metric_names[(index - 1) % len(metric_names)]} | {index * 100}",
            "local_source_id": "metrics/normalized_metrics.json",
        }
        for index in range(1, 33)
    ]
    path = _write_report(
        tmp_path,
        summary={
            "critical": 1,
            "warning": 2,
            "evidence_rows": 32,
            "company_evidence_status": "wiki_exact_identity",
            "checked_claim_count": 8,
            "verified_claim_count": 6,
            "contradicted_claim_count": 1,
            "unsupported_claim_count": 1,
        },
        evidence=evidence,
    )

    rendered = generate_html(str(path))
    visible, audit = rendered.split('<details class="evidence-audit">', maxsplit=1)

    assert '<span>已核验声明</span><b>6</b>' in rendered
    assert '<span>异常项</span><b>3</b>' in rendered
    assert '<span>声明覆盖</span><b>75%</b>' in rendered
    assert '<span>证据定位</span><b>32/32</b>' in rendered
    assert visible.count('class="key-evidence-item"') == 4
    assert "00005-2025-annual-metric-00001" not in visible
    assert "证据编号" not in visible
    assert "00005-2025-annual-metric-00001" in audit
    assert "00005-2025-annual-metric-00032" in audit
    assert '<summary>完整证据审计清单（32 条）</summary>' in audit
    assert 'data-label="指标或声明"' in audit
    assert 'data-label="期间与定位"' in audit
    assert 'data-label="审计字段"' in audit
    assert "财务报表原文" in audit
    assert "截至 2025-12-31" in audit
    assert "PDF 第 11 页" in audit
    assert "@media (max-width: 700px)" in rendered
    assert not re.search(r'<details class="evidence-audit"[^>]*\sopen(?:\s|=|>)', rendered)


def test_evidence_semantics_remain_chinese_when_claim_list_is_unavailable(tmp_path: Path) -> None:
    evidence_id = "4f36da8c1148c33bc2bb95b2763d750d159f8ba7d1fc4c7da11b6bed432d0f50"
    path = _write_report(
        tmp_path,
        summary={
            "critical": 0,
            "warning": 1,
            "evidence_rows": 1,
            "company_evidence_status": "wiki_exact_identity",
            "checked_claim_count": 0,
            "verified_claim_count": 0,
        },
        evidence=[
            {
                "source_type": "sec_html_section",
                "evidence_id": evidence_id,
                "section_id": "item_1a",
                "html_anchor": "item_1a",
                "source_url": "https://www.sec.gov/example-report",
                "open_source_page_url": "javascript:alert(1)",
                "local_source_id": "sections/risk_factors.md",
            }
        ],
    )

    rendered = generate_html(str(path))
    visible, audit = rendered.split('<details class="evidence-audit">', maxsplit=1)

    assert '<span>已核验声明</span><b>未提供</b>' in rendered
    assert '<span>声明覆盖</span><b>有限核查</b>' in rendered
    assert "风险因素（Item 1A）" in visible
    assert "查看披露原文" in visible
    assert "javascript:" not in rendered
    assert evidence_id not in visible
    assert evidence_id in audit


def test_structured_claim_verdicts_render_each_status_and_reason(tmp_path: Path) -> None:
    path = _write_report(
        tmp_path,
        summary={
            "critical": 1,
            "warning": 1,
            "checked_claim_count": 3,
            "verified_claim_count": 1,
            "contradicted_claim_count": 1,
            "unsupported_claim_count": 1,
        },
        evidence=[],
        claim_verdicts=[
            {
                "claim_id": "revenue-current",
                "claim": "2025 年营业收入为 100 亿美元。",
                "status": "verified",
                "metric_key": "operating_revenue",
                "period": "2025-12-31",
                "reason": "",
            },
            {
                "claim_id": "profit-current",
                "claim": "2025 年净利润为 20 亿美元。",
                "status": "contradicted",
                "metric_key": "net_profit",
                "period": "2025-12-31",
                "reason": "声明数值与源指标归一化数值不一致",
            },
            {
                "claim_id": "risk-detail",
                "claim": "公司不存在重大市场风险。",
                "status": "unsupported",
                "reason": "声明证据缺少可回溯定位",
            },
        ],
    )

    rendered = generate_html(str(path))

    assert '<h2 id="claims-title">逐条声明核验</h2>' in rendered
    assert rendered.count('class="claim-item ') == 3
    assert "已验证" in rendered
    assert "存在反证" in rendered
    assert "证据不足" in rendered
    assert "声明数值与源指标归一化数值不一致" in rendered
    assert "声明证据缺少可回溯定位" in rendered
    assert "营业收入 · 截至 2025-12-31" in rendered
