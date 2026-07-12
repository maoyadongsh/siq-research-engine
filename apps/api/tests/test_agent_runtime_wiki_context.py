import json
from pathlib import Path

from services import agent_runtime_wiki_context as wiki_context


def _read_json_file(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def test_select_report_from_company_json_prefers_annual_for_annual_question():
    company = {
        "primary_report_id": "2025-quarterly",
        "reports": [
            {"report_id": "2025-quarterly", "report_kind": "quarterly"},
            {"report_id": "2025-annual", "report_kind": "annual"},
        ],
    }

    assert wiki_context.select_report_from_company_json(
        company,
        "请看2025年报",
        annual_terms=("年报", "annual"),
        quarterly_terms=("季报", "quarterly"),
    )["report_id"] == "2025-annual"


def test_primary_report_for_company_uses_exact_filing_and_manifest_parse_run(tmp_path):
    company_dir = tmp_path / "HK-00700-Tencent"
    target_report_dir = company_dir / "reports" / "2025-annual-target"
    target_report_dir.mkdir(parents=True)
    (company_dir / "company.json").write_text(
        json.dumps(
            {
                "market": "HK",
                "company_id": "HK:00700",
                "primary_report_id": "2025-annual-latest",
                "reports": [
                    {
                        "report_id": "2025-annual-latest",
                        "filing_id": "HK:00700:latest",
                        "parse_run_id": "parse-latest",
                    },
                    {
                        "report_id": "2025-annual-target",
                        "filing_id": "HK:00700:target",
                        "parser_result_task_id": "task-target",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    (target_report_dir / "manifest.json").write_text(
        json.dumps(
            {
                "filing_id": "HK:00700:target",
                "parse_run_id": "parse-target",
                "paths": {
                    "document_full": "parser/document_full.json",
                    "wiki_report_complete": "sections/report_complete.md",
                },
            }
        ),
        encoding="utf-8",
    )

    class ForbiddenLocalCitation:
        @staticmethod
        def primary_report(*_args, **_kwargs):
            raise AssertionError("strict identity selection must bypass latest/primary helpers")

    report = wiki_context.primary_report_for_company(
        company_dir,
        "查看年报",
        local_citation_module=ForbiddenLocalCitation(),
        read_json_file=_read_json_file,
        annual_terms=("年报",),
        quarterly_terms=("季报",),
        research_identity={
            "market": "HK",
            "company_id": "HK:00700",
            "filing_id": "HK:00700:target",
            "parse_run_id": "parse-target",
        },
    )

    assert report["selection_status"] == "identity_exact"
    assert report["report_id"] == "2025-annual-target"
    assert report["filing_id"] == "HK:00700:target"
    assert report["parse_run_id"] == "parse-target"
    assert report["task_id"] == "task-target"
    assert report["document_full"] == target_report_dir / "parser" / "document_full.json"
    assert report["report_md"] == target_report_dir / "sections" / "report_complete.md"


def test_primary_report_for_company_fails_closed_on_parse_run_mismatch(tmp_path):
    company_dir = tmp_path / "US-AAPL"
    report_dir = company_dir / "reports" / "2025-10-K"
    report_dir.mkdir(parents=True)
    (company_dir / "company.json").write_text(
        json.dumps(
            {
                "company_id": "US:0000320193",
                "reports": [{"report_id": "2025-10-K", "filing_id": "US:AAPL:2025-10-K"}],
            }
        ),
        encoding="utf-8",
    )
    (report_dir / "manifest.json").write_text(
        json.dumps({"filing_id": "US:AAPL:2025-10-K", "parse_run_id": "parse-real"}),
        encoding="utf-8",
    )

    report = wiki_context.primary_report_for_company(
        company_dir,
        "FY2025 revenue",
        local_citation_module=None,
        read_json_file=_read_json_file,
        annual_terms=("annual",),
        quarterly_terms=("quarter",),
        research_identity={
            "company_id": "US:CIK0000320193",
            "filing_id": "US:AAPL:2025-10-K",
            "parse_run_id": "parse-other",
        },
    )

    assert report == {
        "selection_status": "identity_mismatch",
        "selection_reason": "parse_run_id_not_found",
    }


def test_company_artifact_paths_prefers_by_report_and_latest(tmp_path):
    company_dir = tmp_path / "600000-demo"
    metrics_dir = company_dir / "metrics" / "reports" / "2025-annual"
    metrics_dir.mkdir(parents=True)
    three_statements = metrics_dir / "three_statements.json"
    three_statements.write_text("{}", encoding="utf-8")
    latest_key_metrics = company_dir / "metrics" / "latest" / "key_metrics.json"
    latest_key_metrics.parent.mkdir(parents=True)
    latest_key_metrics.write_text("{}", encoding="utf-8")
    (company_dir / "company.json").write_text(
        json.dumps(
            {
                "metrics": {
                    "by_report": {
                        "2025-annual": {
                            "three_statements": "metrics/reports/2025-annual/three_statements.json"
                        }
                    },
                    "latest": {"key_metrics": "metrics/latest/key_metrics.json"},
                }
            }
        ),
        encoding="utf-8",
    )

    paths = wiki_context.company_artifact_paths(company_dir, "2025-annual", read_json_file=_read_json_file)

    assert paths["three_statements"] == three_statements
    assert paths["key_metrics"] == latest_key_metrics


def test_company_artifact_paths_strict_report_never_uses_latest_or_global_semantic(tmp_path):
    company_dir = tmp_path / "US-AAPL"
    latest_key_metrics = company_dir / "metrics" / "latest" / "key_metrics.json"
    latest_key_metrics.parent.mkdir(parents=True)
    latest_key_metrics.write_text("{}", encoding="utf-8")
    semantic = company_dir / "semantic" / "document_links.json"
    semantic.parent.mkdir(parents=True)
    semantic.write_text("{}", encoding="utf-8")
    (company_dir / "company.json").write_text(
        json.dumps(
            {
                "metrics": {"latest": {"key_metrics": "metrics/latest/key_metrics.json"}},
            }
        ),
        encoding="utf-8",
    )

    strict_paths = wiki_context.company_artifact_paths(
        company_dir,
        "2024-10-K",
        read_json_file=_read_json_file,
        strict_report=True,
    )
    legacy_paths = wiki_context.company_artifact_paths(
        company_dir,
        "2024-10-K",
        read_json_file=_read_json_file,
    )

    assert "key_metrics" not in strict_paths
    assert "document_links" not in strict_paths
    assert legacy_paths["key_metrics"] == latest_key_metrics
    assert legacy_paths["document_links"] == semantic


def test_company_scope_propagates_exact_identity_to_strict_artifact_lookup(tmp_path):
    company_dir = tmp_path / "HK-00700-Tencent"
    company_dir.mkdir()
    (company_dir / "company.json").write_text(
        json.dumps({"company_id": "HK:00700", "company_short_name": "Tencent"}),
        encoding="utf-8",
    )
    strict_calls = []

    rendered = wiki_context.build_company_wiki_scope_context(
        "腾讯营收",
        {"research_identity": {"market": "HK"}},
        wiki_root=tmp_path,
        resolve_company_dir=lambda _message, _context: company_dir,
        read_json_file=_read_json_file,
        primary_report_for_company=lambda _company_dir, _message, _context: {
            "selection_status": "identity_exact",
            "report_id": "2025-annual",
            "task_id": "task-2025",
            "filing_id": "HK:00700:2025-annual",
            "parse_run_id": "parse-2025",
        },
        company_artifact_paths=lambda _company_dir, _report_id, strict: strict_calls.append(strict) or {},
        clean_context_value=str,
    )

    assert strict_calls == [True]
    assert "filing_id=HK:00700:2025-annual, parse_run_id=parse-2025" in rendered
    assert "禁止回退 primary/latest" in rendered


def test_company_scope_reuses_exact_us_manifest_artifact_paths(tmp_path):
    company_dir = tmp_path / "US-AAPL"
    report_dir = company_dir / "reports" / "2025-10-K"
    document_full = report_dir / "parser" / "document_full.json"
    report_md = report_dir / "sections" / "report_complete.md"
    document_full.parent.mkdir(parents=True)
    report_md.parent.mkdir(parents=True)
    document_full.write_text("{}", encoding="utf-8")
    report_md.write_text("# Apple 2025 Form 10-K", encoding="utf-8")
    (company_dir / "company.json").write_text(
        json.dumps(
            {
                "company_id": "US:0000320193",
                "company_short_name": "Apple",
                "stock_code": "AAPL",
                "reports": [
                    {
                        "report_id": "2025-10-K",
                        "filing_id": "US:AAPL:2025-10-K",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (report_dir / "manifest.json").write_text(
        json.dumps(
            {
                "company_id": "US:0000320193",
                "filing_id": "US:AAPL:2025-10-K",
                "parse_run_id": "parse-us-2025",
                "paths": {
                    "document_full": "parser/document_full.json",
                    "wiki_report_complete": "sections/report_complete.md",
                },
            }
        ),
        encoding="utf-8",
    )
    context = {
        "research_identity": {
            "company_id": "US:CIK0000320193",
            "filing_id": "US:AAPL:2025-10-K",
            "parse_run_id": "parse-us-2025",
        }
    }

    rendered = wiki_context.build_company_wiki_scope_context(
        "FY2025 revenue",
        context,
        wiki_root=tmp_path,
        resolve_company_dir=lambda _message, _context: company_dir,
        read_json_file=_read_json_file,
        primary_report_for_company=lambda directory, message, _context: (
            wiki_context.primary_report_for_company(
                directory,
                message,
                local_citation_module=None,
                read_json_file=_read_json_file,
                annual_terms=("annual",),
                quarterly_terms=("quarter",),
                research_identity=context["research_identity"],
            )
        ),
        company_artifact_paths=lambda directory, report_id, strict: (
            wiki_context.company_artifact_paths(
                directory,
                report_id,
                read_json_file=_read_json_file,
                strict_report=strict,
            )
        ),
        clean_context_value=str,
    )

    assert f"- 年报Markdown: {report_md}" in rendered
    assert f"- 完整full JSON: {document_full}" in rendered
    assert f"{report_dir / 'report.md'}" not in rendered
    assert f"{report_dir / 'document_full.json'}" not in rendered


def test_company_scope_uses_canonical_manifest_company_id(tmp_path):
    company_dir = tmp_path / "US-AAPL"
    company_dir.mkdir()
    (company_dir / "company.json").write_text(
        json.dumps({"company_short_name": "Apple", "stock_code": "AAPL"}),
        encoding="utf-8",
    )

    rendered = wiki_context.build_company_wiki_scope_context(
        "FY2025 revenue",
        {"research_identity": {"market": "US"}},
        wiki_root=tmp_path,
        resolve_company_dir=lambda _message, _context: company_dir,
        read_json_file=_read_json_file,
        primary_report_for_company=lambda _company_dir, _message, _context: {
            "selection_status": "identity_exact",
            "report_id": "2025-10-K",
            "filing_id": "US:AAPL:2025-10-K",
            "parse_run_id": "parse-us-2025",
            "_manifest": {"company_id": "US:0000320193"},
        },
        company_artifact_paths=lambda _company_dir, _report_id, _strict: {},
        clean_context_value=str,
    )

    assert "company_id=US:0000320193" in rendered
    assert "company_id=US-AAPL" not in rendered


def test_company_scope_rejects_exact_artifact_paths_outside_company(tmp_path):
    company_dir = tmp_path / "US-AAPL"
    company_dir.mkdir()
    outside_document = tmp_path / "other-company" / "document_full.json"
    outside_report = tmp_path / "other-company" / "report.md"
    outside_document.parent.mkdir()
    outside_document.write_text("{}", encoding="utf-8")
    outside_report.write_text("# Other company", encoding="utf-8")
    (company_dir / "company.json").write_text(
        json.dumps({"company_id": "US:0000320193", "company_short_name": "Apple"}),
        encoding="utf-8",
    )

    rendered = wiki_context.build_company_wiki_scope_context(
        "FY2025 revenue",
        None,
        wiki_root=tmp_path,
        resolve_company_dir=lambda _message, _context: company_dir,
        read_json_file=_read_json_file,
        primary_report_for_company=lambda _company_dir, _message, _context: {
            "selection_status": "identity_exact",
            "report_id": "2025-10-K",
            "filing_id": "US:AAPL:2025-10-K",
            "parse_run_id": "parse-us-2025",
            "document_full": outside_document,
            "report_md": outside_report,
        },
        company_artifact_paths=lambda _company_dir, _report_id, _strict: {},
        clean_context_value=str,
    )

    assert str(outside_document) not in rendered
    assert str(outside_report) not in rendered


def test_wiki_fulltext_fallback_result_searches_report_md(tmp_path):
    task_id = "7dbc35a7-7626-4e81-810e-5dbb764434e0"
    company_dir = tmp_path / "600104-上汽集团"
    report_dir = company_dir / "reports" / "2025-annual"
    report_dir.mkdir(parents=True)
    (company_dir / "company.json").write_text(
        json.dumps({"company_short_name": "上汽集团", "stock_code": "600104"}),
        encoding="utf-8",
    )
    (report_dir / "report.md").write_text(
        "\n".join(
            [
                "[PDF_PAGE: 11]",
                "公司新能源业务继续推进。",
                "市场占有率 13.1%，维持行业领先。",
            ]
        ),
        encoding="utf-8",
    )

    result = wiki_context.wiki_fulltext_fallback_result(
        "上汽集团市场占有率是多少？",
        None,
        fallback_terms=("市场占有率", "报告"),
        generic_terms={"报告"},
        max_snippets=3,
        snippet_chars=200,
        is_general_assistant_request=lambda _message: False,
        resolve_company_dir=lambda _message, _context: company_dir,
        context_company=lambda _context: {"name": "上汽集团"},
        read_json_file=_read_json_file,
        primary_report_for_company=lambda _company_dir, _message, _context: {
            "report_id": "2025-annual",
            "task_id": task_id,
        },
    )

    assert result is not None
    assert result["company_id"] == "600104-上汽集团"
    assert result["rows"][0]["source_type"] == "wiki_report_fulltext"
    assert result["rows"][0]["pdf_page"] == 11
    assert "市场占有率 13.1%" in result["rows"][0]["snippet"]


def test_wiki_fulltext_fallback_result_supports_parser_report_layout(tmp_path):
    company_dir = tmp_path / "AAPL-Apple-Inc"
    report_dir = company_dir / "reports" / "2025-10-K"
    parser_dir = report_dir / "parser"
    parser_dir.mkdir(parents=True)
    (company_dir / "company.json").write_text(
        json.dumps(
            {
                "company_id": "US:0000320193",
                "market": "US",
                "company_short_name": "Apple Inc",
                "aliases": ["Apple", "AAPL"],
            }
        ),
        encoding="utf-8",
    )
    (parser_dir / "report_complete.md").write_text(
        "\n".join(["[PDF_PAGE: 42]", "Revenue was 100."]),
        encoding="utf-8",
    )
    (parser_dir / "document_full.json").write_text(
        json.dumps(
            {
                "content_list": [
                    {"type": "text", "text": "Revenue was 100.", "page_idx": 41}
                ]
            }
        ),
        encoding="utf-8",
    )

    result = wiki_context.wiki_fulltext_fallback_result(
        "Apple revenue",
        None,
        fallback_terms=("revenue",),
        generic_terms=(),
        max_snippets=3,
        snippet_chars=200,
        is_general_assistant_request=lambda _message: False,
        resolve_company_dir=lambda _message, _context: company_dir,
        context_company=lambda _context: {"name": "Apple Inc"},
        read_json_file=_read_json_file,
        primary_report_for_company=lambda _company_dir, _message, _context: {
            "report_id": "2025-10-K",
            "task_id": "task-us-2025",
        },
    )

    assert result is not None
    assert result["report_md"] == parser_dir / "report_complete.md"
    assert result["document_full"] == parser_dir / "document_full.json"
    assert result["rows"]
    assert any(row["source_type"] == "wiki_report_fulltext" for row in result["rows"])


def test_wiki_fulltext_fallback_matches_multiword_financial_term(tmp_path):
    company_dir = tmp_path / "AAPL-Apple-Inc"
    company_dir.mkdir()

    assert wiki_context.should_consider_wiki_fulltext_fallback(
        "US Apple Inc total liabilities",
        None,
        fallback_terms=("total liabilities",),
        is_general_assistant_request=lambda _message: False,
        resolve_company_dir=lambda _message, _context: company_dir,
        context_company=lambda _context: {},
    )


def test_render_wiki_fulltext_fallback_context_uses_evidence_links():
    rendered = wiki_context.render_wiki_fulltext_fallback_context(
        {
            "company_name": "上汽集团",
            "stock_code": "600104",
            "company_id": "600104-上汽集团",
            "report_id": "2025-annual",
            "task_id": "task-1",
            "report_md": "/wiki/reports/2025-annual/report.md",
            "document_full": "/wiki/reports/2025-annual/document_full.json",
            "terms": ["市场占有率"],
            "rows": [
                {
                    "source_type": "wiki_report_fulltext",
                    "file": "reports/2025-annual/report.md",
                    "score": 25,
                    "snippet": "市场占有率 13.1%",
                    "task_id": "task-1",
                    "pdf_page": 11,
                    "table_index": "",
                    "md_line": 3,
                }
            ],
        },
        evidence_url=lambda task_id, pdf_page, table_index, kind: f"https://example/{kind}/{task_id}/{pdf_page}/{table_index}",
    )

    assert "全文兜底证据" in rendered
    assert "市场占有率 13.1%" in rendered
    assert "[打开PDF页](https://example/pdf/task-1/11/)" in rendered
