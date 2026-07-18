"""Wiki scope and fulltext fallback helpers for the Hermes agent runtime."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from services import agent_runtime_fallback_contexts

ReadJsonFile = Callable[[Path], Any | None]
IDENTITY_SELECTION_FIELDS = ("company_id", "filing_id", "parse_run_id")
PDF_MARKETS = {"CN", "HK", "JP", "KR", "EU"}
PDF_MARKET_QUERY_LEXICON: tuple[dict[str, Any], ...] = (
    {
        "triggers": (
            "人才",
            "人才战略",
            "人才策略",
            "人的资本",
            "人力资本",
            "human capital",
            "talent",
        ),
        "terms": {
            "HK": ("人才", "人力資本", "human capital", "talent", "development", "training"),
            "JP": ("人的資本", "人材育成", "人材戦略", "人材", "人財"),
            "KR": ("인적자본", "인재", "인재육성", "인재전략"),
            "EU": ("human capital", "talent", "people strategy", "training", "development"),
        },
    },
    {
        "triggers": (
            "员工",
            "人员",
            "人效",
            "职工",
            "雇员",
            "僱員",
            "人力",
            "工龄",
            "employee",
            "workforce",
            "headcount",
        ),
        "terms": {
            "HK": ("僱員", "雇員", "員工", "employee", "staff", "workforce", "headcount"),
            "JP": ("従業員", "社員", "人員", "平均勤続年数", "employee", "workforce"),
            "KR": ("직원", "직원수", "임직원", "종업원", "평균근속연수", "성별합계"),
            "EU": ("employee", "employees", "workforce", "personnel", "headcount", "mitarbeiter", "effectif"),
        },
    },
    {
        "triggers": ("薪酬", "工资", "报酬", "人工成本", "人力成本", "人均薪酬"),
        "terms": {
            "HK": ("remuneration", "employee costs", "staff costs", "薪酬", "工資"),
            "JP": ("給与", "平均年間給与", "人件費", "報酬"),
            "KR": ("급여", "보수", "인건비", "1인평균급여액"),
            "EU": ("remuneration", "employee costs", "personnel expense", "staff costs", "wages"),
        },
    },
    {
        "triggers": ("研发", "研究开发", "研究与开发", "研发费用", "研发投入", "技术创新", "research", "development", "r&d"),
        "terms": {
            "HK": ("research and development", "R&D", "研發", "研发"),
            "JP": ("研究開発", "研究開発費", "R&D"),
            "KR": ("연구개발", "연구개발비", "R&D"),
            "EU": ("research and development", "R&D", "research expense", "development expense"),
        },
    },
    {
        "triggers": ("分部", "业务构成", "收入构成", "地区构成", "产品构成", "业务板块", "segment", "revenue mix"),
        "terms": {
            "HK": ("segment", "分部", "地區", "產品", "revenue by segment"),
            "JP": ("セグメント", "事業別", "地域別", "売上収益"),
            "KR": ("부문", "사업부문", "지역별", "매출액"),
            "EU": ("segment", "business segment", "geographical", "revenue by segment"),
        },
    },
    {
        "triggers": ("董事会", "董事", "治理", "委员会", "独立董事", "监事", "board", "governance", "committee"),
        "terms": {
            "HK": ("board of directors", "董事會", "董事", "corporate governance", "committee"),
            "JP": ("取締役", "取締役会", "コーポレートガバナンス", "委員会"),
            "KR": ("이사회", "이사", "기업지배구조", "위원회", "사외이사"),
            "EU": ("board of directors", "supervisory board", "corporate governance", "committee"),
        },
    },
    {
        "triggers": ("分红", "股息", "派息", "股利", "回购", "股份回购", "dividend", "buyback", "repurchase"),
        "terms": {
            "HK": ("dividend", "股息", "派息", "share repurchase"),
            "JP": ("配当", "配当金", "自己株式", "株主還元"),
            "KR": ("배당", "배당금", "자기주식", "주주환원"),
            "EU": ("dividend", "share repurchase", "share buyback", "distribution"),
        },
    },
    {
        "triggers": ("风险", "诉讼", "处罚", "合规", "监管", "或有事项", "risk", "litigation", "compliance"),
        "terms": {
            "HK": ("risk", "litigation", "penalty", "compliance", "contingent"),
            "JP": ("リスク", "訴訟", "法令遵守", "偶発債務"),
            "KR": ("위험", "소송", "제재", "준법", "우발부채"),
            "EU": ("risk", "litigation", "penalty", "compliance", "contingent liability"),
        },
    },
    {
        "triggers": ("商誉", "goodwill", "商譽"),
        "terms": {
            "HK": ("goodwill", "商譽", "商誉"),
            "JP": ("のれん", "goodwill", "無形資産"),
            "KR": ("영업권", "goodwill", "무형자산"),
            "EU": ("goodwill", "écart d'acquisition", "Geschäfts- oder Firmenwert"),
        },
    },
)


def expand_pdf_market_query_terms(message: str, market: str) -> list[str]:
    market = str(market or "").upper()
    if market not in PDF_MARKETS or market == "CN":
        return []
    normalized = re.sub(r"\s+", "", message or "").lower()
    output: list[str] = []
    for group in PDF_MARKET_QUERY_LEXICON:
        if not any(re.sub(r"\s+", "", str(trigger or "")).lower() in normalized for trigger in group["triggers"]):
            continue
        output.extend(str(term) for term in group["terms"].get(market, ()))
    return list(dict.fromkeys(output))


def pdf_market_intent_bonus(message: str, market: str, text: str) -> int:
    """Boost disclosure anchors over incidental mentions of translated terms."""
    query = re.sub(r"\s+", "", message or "").lower()
    haystack = str(text or "").lower()
    market = str(market or "").upper()
    anchors: list[str] = []
    if any(
        term in query
        for term in (
            "员工",
            "人员",
            "人才",
            "人的资本",
            "人力资本",
            "humancapital",
            "talent",
            "employee",
            "workforce",
            "职工",
            "雇员",
            "僱員",
            "工龄",
        )
    ):
        anchors = {
            "HK": ["number of employees", "total workforce", "headcount", "employees by"],
            "JP": ["人的資本", "人材育成", "人材戦略", "従業員数", "平均勤続年数", "平均年間給与"],
            "KR": ["직원 수", "직원수", "평균근속연수", "성별합계"],
            "EU": ["human capital", "talent", "number of employees", "total number of employees", "total group employees", "headcount", "breakdown of total employees"],
        }.get(market, [])
    elif any(term in query for term in ("研发", "研究开发", "研究与开发", "技术创新")):
        anchors = {"JP": ["研究開発費"], "KR": ["연구개발비"], "HK": ["research and development"], "EU": ["research and development"]}.get(market, [])
    elif any(term in query for term in ("分部", "业务构成", "收入构成", "地区构成", "产品构成")):
        anchors = {"JP": ["セグメント情報"], "KR": ["사업부문", "부문별"], "HK": ["segment information", "revenue by segment"], "EU": ["segment information", "revenue by segment"]}.get(market, [])
    elif any(term in query for term in ("董事会", "治理", "委员会", "独立董事")):
        anchors = {"JP": ["取締役会"], "KR": ["이사회", "사외이사"], "HK": ["board of directors", "corporate governance"], "EU": ["board of directors", "supervisory board", "corporate governance"]}.get(market, [])
    elif any(term in query for term in ("分红", "股息", "派息", "股利", "回购")):
        anchors = {"JP": ["配当金", "株主還元"], "KR": ["배당금", "주주환원"], "HK": ["dividend per share", "share repurchase"], "EU": ["dividend per share", "share buyback"]}.get(market, [])
    return sum(120 for anchor in anchors if anchor.lower() in haystack)


def _identity_text(identity: Mapping[str, Any] | None, field: str) -> str:
    return str((identity or {}).get(field) or "").strip()


def _normalized_company_id(value: Any) -> str:
    text = str(value or "").strip()
    if ":" not in text:
        return text
    market, suffix = text.split(":", 1)
    market = "US" if market.upper() in {"US", "US_SEC", "US-SEC"} else market.upper()
    if market == "US" and suffix.upper().startswith("CIK"):
        suffix = suffix[3:]
    return f"{market}:{suffix}"


def _normalized_market_identifier(value: Any) -> str:
    text = str(value or "").strip()
    upper = text.upper()
    if upper.startswith("US_SEC:"):
        return f"US:{text.split(':', 1)[1]}"
    if upper.startswith("US-SEC:"):
        return f"US:{text.split(':', 1)[1]}"
    return text


def _safe_report_id(value: Any) -> str:
    text = str(value or "").strip()
    path = Path(text)
    if not text or path.is_absolute() or len(path.parts) != 1 or text in {".", ".."}:
        return ""
    return text


def _report_manifest(company_dir: Path, report: Mapping[str, Any], read_json_file: ReadJsonFile) -> dict[str, Any]:
    report_id = _safe_report_id(report.get("report_id"))
    if not report_id:
        return {}
    payload = read_json_file(company_dir / "reports" / report_id / "manifest.json") or {}
    return payload if isinstance(payload, dict) else {}


def select_report_for_research_identity(
    company: Mapping[str, Any],
    company_dir: Path,
    research_identity: Mapping[str, Any],
    *,
    read_json_file: ReadJsonFile,
) -> dict[str, Any]:
    requested = {field: _identity_text(research_identity, field) for field in IDENTITY_SELECTION_FIELDS}
    company_id = str(company.get("company_id") or company.get("company_wiki_id") or "").strip()
    requested_company_id = _normalized_company_id(requested["company_id"])

    reports = [item for item in (company.get("reports") or []) if isinstance(item, dict)]
    if not reports:
        return {"selection_status": "identity_mismatch", "selection_reason": "reports_missing"}

    filing_matches: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for report in reports:
        if not _safe_report_id(report.get("report_id")):
            continue
        manifest = _report_manifest(company_dir, report, read_json_file)
        filing_id = str(report.get("filing_id") or manifest.get("filing_id") or "").strip()
        if requested["filing_id"] and _normalized_market_identifier(filing_id) != _normalized_market_identifier(
            requested["filing_id"]
        ):
            continue
        filing_matches.append((report, manifest))
    if requested["filing_id"] and not filing_matches:
        return {"selection_status": "identity_mismatch", "selection_reason": "filing_id_not_found"}

    parse_matches: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for report, manifest in filing_matches:
        parse_run_id = str(report.get("parse_run_id") or manifest.get("parse_run_id") or "").strip()
        if requested["parse_run_id"] and parse_run_id != requested["parse_run_id"]:
            continue
        parse_matches.append((report, manifest))
    if requested["parse_run_id"] and not parse_matches:
        return {"selection_status": "identity_mismatch", "selection_reason": "parse_run_id_not_found"}
    if len(parse_matches) != 1:
        return {
            "selection_status": "identity_mismatch",
            "selection_reason": "identity_selector_ambiguous" if parse_matches else "identity_selector_not_found",
        }

    report, manifest = parse_matches[0]
    authoritative_company_id = str(report.get("company_id") or manifest.get("company_id") or "").strip()
    fallback_company_ids = {_normalized_company_id(company_id), _normalized_company_id(company_dir.name)}
    if requested_company_id:
        if authoritative_company_id:
            company_matches = requested_company_id == _normalized_company_id(authoritative_company_id)
        else:
            company_matches = not company_id or requested_company_id in fallback_company_ids
        if not company_matches:
            return {"selection_status": "identity_mismatch", "selection_reason": "company_id_mismatch"}
    selected = {**report}
    selected["selection_status"] = "identity_exact"
    selected["market"] = report.get("market") or manifest.get("market")
    selected["company_id"] = report.get("company_id") or manifest.get("company_id")
    selected["filing_id"] = report.get("filing_id") or manifest.get("filing_id")
    selected["parse_run_id"] = report.get("parse_run_id") or manifest.get("parse_run_id")
    selected["task_id"] = (
        report.get("task_id")
        or report.get("parser_result_task_id")
        or manifest.get("task_id")
        or manifest.get("parser_result_task_id")
    )
    selected["_manifest"] = manifest
    return selected


def report_text_blob(report: dict[str, Any]) -> str:
    metadata_raw = report.get("source_filename_metadata")
    metadata = metadata_raw if isinstance(metadata_raw, dict) else {}
    values = [
        report.get("report_id"),
        report.get("report_kind"),
        report.get("source_filename"),
        metadata.get("report_type"),
        metadata.get("report_end"),
    ]
    return " ".join(str(item or "") for item in values).lower()


def report_is_annual(report: dict[str, Any]) -> bool:
    text = report_text_blob(report)
    return "annual" in text or "年报" in text or "年度报告" in text or "2025-annual" in text


def report_is_quarterly(report: dict[str, Any]) -> bool:
    text = report_text_blob(report)
    return any(term in text for term in ("quarter", "quarterly", "季报", "季度", "半年报", "半年度"))


def select_report_from_company_json(
    company: dict[str, Any],
    message: str | None = None,
    *,
    annual_terms: Sequence[str],
    quarterly_terms: Sequence[str],
) -> dict[str, Any]:
    reports = [item for item in (company.get("reports") or []) if isinstance(item, dict)]
    if not reports:
        return {}

    text = re.sub(r"\s+", "", message or "").lower()
    wants_quarterly = any(term.lower() in text for term in quarterly_terms)
    wants_annual = any(term.lower() in text for term in annual_terms)

    if wants_quarterly:
        quarterly = next((item for item in reports if report_is_quarterly(item)), None)
        if quarterly:
            return quarterly
    if wants_annual or not wants_quarterly:
        annual = next((item for item in reports if item.get("report_id") == "2025-annual"), None)
        if annual:
            return annual
        annual = next((item for item in reports if report_is_annual(item)), None)
        if annual:
            return annual

    requested_report_id = company.get("primary_report_id")
    report = next((item for item in reports if item.get("report_id") == requested_report_id), None)
    return report or reports[0]


def _report_artifact_path(
    company_dir: Path,
    report: Mapping[str, Any],
    *,
    key: str,
    default_name: str,
) -> Path:
    report_id = _safe_report_id(report.get("report_id")) or "2025-annual"
    manifest = report.get("_manifest") if isinstance(report.get("_manifest"), Mapping) else {}
    full_document_paths = report.get("full_document_paths")
    full_document_paths = full_document_paths if isinstance(full_document_paths, Mapping) else {}
    full_document_item = full_document_paths.get(key)
    full_document_item = full_document_item if isinstance(full_document_item, Mapping) else {}
    manifest_paths = manifest.get("paths") if isinstance(manifest.get("paths"), Mapping) else {}
    raw = report.get(key) or full_document_item.get("path") or manifest_paths.get(key) or default_name
    relative = Path(str(raw))
    if relative.is_absolute():
        candidate = relative
    elif relative.parts and relative.parts[0] == "reports":
        candidate = company_dir / relative
    else:
        candidate = company_dir / "reports" / report_id / relative
    try:
        candidate.resolve().relative_to(company_dir.resolve())
    except ValueError:
        return company_dir / "reports" / report_id / default_name
    return candidate


def primary_report_for_company(
    company_dir: Path,
    message: str | None = None,
    *,
    local_citation_module: Any | None,
    read_json_file: ReadJsonFile,
    annual_terms: Sequence[str],
    quarterly_terms: Sequence[str],
    research_identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    company = read_json_file(company_dir / "company.json") or {}
    company = company if isinstance(company, dict) else {}
    has_strict_selector = any(_identity_text(research_identity, field) for field in ("filing_id", "parse_run_id"))
    if has_strict_selector:
        report = select_report_for_research_identity(
            company,
            company_dir,
            research_identity or {},
            read_json_file=read_json_file,
        )
        if report.get("selection_status") != "identity_exact":
            return report
        report_id = str(report.get("report_id") or "")
        return {
            **report,
            "report_id": report_id,
            "document_full": _report_artifact_path(
                company_dir,
                report,
                key="document_full",
                default_name="document_full.json",
            ),
            "report_md": _report_artifact_path(
                company_dir,
                report,
                key="wiki_report_complete",
                default_name="report.md",
            ),
        }

    primary = getattr(local_citation_module, "primary_report", None) if local_citation_module else None
    if callable(primary):
        try:
            report = primary(company_dir, query_text=message)
            if isinstance(report, dict):
                return enrich_report_identity(company_dir, report, read_json_file=read_json_file)
        except TypeError:
            try:
                report = primary(company_dir)
                if isinstance(report, dict) and not message:
                    return enrich_report_identity(company_dir, report, read_json_file=read_json_file)
            except Exception:
                pass
        except Exception:
            pass
    report = select_report_from_company_json(
        company,
        message,
        annual_terms=annual_terms,
        quarterly_terms=quarterly_terms,
    ) if isinstance(company, dict) else {}
    report_id = (
        report.get("report_id")
        or (company.get("primary_report_id") if isinstance(company, dict) else None)
        or "2025-annual"
    )
    return enrich_report_identity(company_dir, {
        "report_id": report_id,
        "task_id": report.get("task_id") or report.get("parser_result_task_id") or company.get("task_id"),
        "document_full": _report_artifact_path(
            company_dir,
            report,
            key="document_full",
            default_name="document_full.json",
        ),
        "report_md": _report_artifact_path(
            company_dir,
            report,
            key="wiki_report_complete",
            default_name="report.md",
        ),
    }, read_json_file=read_json_file)


def enrich_report_identity(
    company_dir: Path,
    report: dict[str, Any],
    *,
    read_json_file: ReadJsonFile,
) -> dict[str, Any]:
    report_id = str(report.get("report_id") or "").strip()
    if not report_id:
        return report
    manifest = read_json_file(company_dir / "reports" / report_id / "manifest.json")
    if not isinstance(manifest, dict):
        return report
    output = dict(report)
    for key in ("market", "company_id", "filing_id", "parse_run_id", "task_id"):
        if output.get(key) in (None, "") and manifest.get(key) not in (None, ""):
            output[key] = manifest[key]
    return output


def existing_company_file(company_dir: Path, rel_candidates: list[str | None]) -> Path | None:
    for rel in rel_candidates:
        if not rel:
            continue
        path = company_dir / rel
        if path.exists():
            return path
    return None


def _existing_scoped_report_path(company_dir: Path, value: Any) -> Path | None:
    if value in (None, ""):
        return None
    path = Path(str(value))
    candidate = path if path.is_absolute() else company_dir / path
    try:
        candidate.resolve().relative_to(company_dir.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def company_artifact_paths(
    company_dir: Path,
    report_id: str,
    *,
    read_json_file: ReadJsonFile,
    strict_report: bool = False,
) -> dict[str, Path]:
    company = read_json_file(company_dir / "company.json") or {}
    metrics = company.get("metrics") if isinstance(company, dict) else {}
    by_report = (metrics.get("by_report") or {}).get(report_id) if isinstance(metrics, dict) else {}
    latest = metrics.get("latest") if isinstance(metrics, dict) else {}
    evidence = company.get("evidence") if isinstance(company, dict) else {}

    candidates: dict[str, list[str | None]] = {
        "three_statements": [
            by_report.get("three_statements") if isinstance(by_report, dict) else None,
            f"metrics/reports/{report_id}/three_statements.json",
            by_report.get("financial_data") if isinstance(by_report, dict) else None,
            f"reports/{report_id}/metrics/financial_data.json",
        ],
        "key_metrics": [
            by_report.get("key_metrics") if isinstance(by_report, dict) else None,
            f"metrics/reports/{report_id}/key_metrics.json",
        ],
        "validation": [
            by_report.get("validation") if isinstance(by_report, dict) else None,
            f"metrics/reports/{report_id}/validation.json",
            f"reports/{report_id}/metrics/financial_checks.json",
        ],
        "manifest": [f"reports/{report_id}/manifest.json"],
        "evidence_index": [
            evidence.get("evidence_index") if isinstance(evidence, dict) else None,
            "evidence/evidence_index.json",
        ],
        "pdf_refs": [
            evidence.get("pdf_refs") if isinstance(evidence, dict) else None,
            "evidence/pdf_refs.json",
        ],
        "report_md": [f"reports/{report_id}/report.md"],
        "report_json": [f"reports/{report_id}/report.json"],
        "document_full": [f"reports/{report_id}/document_full.json"],
        "evidence_semantic": ["semantic/evidence_semantic.json"],
        "retrieval_index": ["semantic/retrieval_index.json"],
        "document_links": ["semantic/document_links.json"],
        "note_links": ["semantic/note_links.json"],
    }
    if not strict_report:
        candidates["three_statements"][2:2] = [
            latest.get("three_statements") if isinstance(latest, dict) else None,
            "metrics/latest/three_statements.json",
            metrics.get("three_statements") if isinstance(metrics, dict) else None,
            "metrics/three_statements.json",
        ]
        candidates["three_statements"].extend(
            [latest.get("financial_data") if isinstance(latest, dict) else None]
        )
        candidates["key_metrics"].extend(
            [
                latest.get("key_metrics") if isinstance(latest, dict) else None,
                "metrics/latest/key_metrics.json",
                metrics.get("key_metrics") if isinstance(metrics, dict) else None,
                "metrics/key_metrics.json",
            ]
        )
        candidates["validation"].extend(
            [
                latest.get("validation") if isinstance(latest, dict) else None,
                "metrics/latest/validation.json",
                metrics.get("validation") if isinstance(metrics, dict) else None,
                "metrics/validation.json",
            ]
        )
    else:
        for key in (
            "evidence_index",
            "pdf_refs",
            "evidence_semantic",
            "retrieval_index",
            "document_links",
            "note_links",
        ):
            candidates[key] = []
    return {
        key: path
        for key, rels in candidates.items()
        if (path := existing_company_file(company_dir, rels))
    }


def table_meta_by_line(
    company_dir: Path,
    report_id: str,
    *,
    read_json_file: ReadJsonFile,
) -> list[dict[str, Any]]:
    indexed = read_json_file(company_dir / "reports" / report_id / "tables" / "table_index.json") or {}
    indexed_tables = indexed.get("tables") if isinstance(indexed, dict) else []
    output = [table for table in indexed_tables if isinstance(table, dict)] if isinstance(indexed_tables, list) else []
    report_json = read_json_file(company_dir / "reports" / report_id / "report.json") or {}
    tables = report_json.get("tables") if isinstance(report_json, dict) else []
    if isinstance(tables, list):
        output.extend(table for table in tables if isinstance(table, dict))
    document_full = read_json_file(company_dir / "reports" / report_id / "document_full.json") or {}
    enhanced = document_full.get("content_list_enhanced") if isinstance(document_full, dict) else {}
    tables = enhanced.get("tables") if isinstance(enhanced, dict) else []
    if isinstance(tables, list):
        output.extend(table for table in tables if isinstance(table, dict))
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[Any, Any, Any]] = set()
    for table in output:
        key = (
            table.get("table_index"),
            table.get("line") or table.get("md_line") or table.get("markdown_line"),
            table.get("pdf_page_number") or table.get("pdf_page"),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(table)
    return deduped


def document_full_text_items(
    document_full: dict[str, Any],
    terms: list[str],
    *,
    snippet_chars: int,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    content_list = document_full.get("content_list") if isinstance(document_full, dict) else []
    if isinstance(content_list, list):
        for index, item in enumerate(content_list):
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            score = agent_runtime_fallback_contexts._line_match_score(text, terms)
            if score <= 0:
                continue
            page_idx = agent_runtime_fallback_contexts._safe_int(item.get("page_idx"))
            items.append(
                {
                    "score": score,
                    "order": index,
                    "text": text[:snippet_chars],
                    "pdf_page": page_idx + 1 if page_idx is not None else None,
                    "type": item.get("type"),
                }
            )

    enhanced = document_full.get("content_list_enhanced") if isinstance(document_full, dict) else {}
    tables = enhanced.get("tables") if isinstance(enhanced, dict) else []
    if isinstance(tables, list):
        for index, table in enumerate(tables):
            if not isinstance(table, dict):
                continue
            text = " ".join(str(table.get(key) or "") for key in ("heading", "preview", "unit"))
            score = agent_runtime_fallback_contexts._line_match_score(text, terms)
            if score <= 0:
                continue
            items.append(
                {
                    "score": score + 8,
                    "order": 100000 + index,
                    "text": text[:snippet_chars],
                    "pdf_page": table.get("pdf_page_number") or table.get("pdf_page"),
                    "table_index": table.get("table_index"),
                    "md_line": table.get("line") or table.get("md_line") or table.get("markdown_line"),
                    "type": "table",
                }
            )
    return items


def should_consider_wiki_fulltext_fallback(
    message: str,
    context: Any | None = None,
    *,
    fallback_terms: Sequence[str],
    generic_terms: set[str] | tuple[str, ...] = (),
    is_general_assistant_request: Callable[[str], bool],
    resolve_company_dir: Callable[[str, Any | None], Path | None],
    context_company: Callable[[Any | None], dict[str, Any]],
) -> bool:
    text = re.sub(r"\s+", "", message or "").casefold()
    if not text or is_general_assistant_request(text):
        return False
    if resolve_company_dir(message, context) is None:
        return False
    if any(
        normalized_term and normalized_term in text
        for term in fallback_terms
        if (normalized_term := re.sub(r"\s+", "", str(term or "")).casefold())
    ):
        return True
    company = context_company(context)
    aliases = agent_runtime_fallback_contexts._company_aliases("", company)
    terms = agent_runtime_fallback_contexts._fallback_search_terms(message, aliases, tuple(fallback_terms))
    if agent_runtime_fallback_contexts._specific_fulltext_terms(terms, generic_terms):
        return True
    return bool(company and any(term in text for term in ("多少", "数据", "情况", "如何", "怎么样", "说明", "披露")))


def indexed_evidence_rows(
    company_dir: Path,
    report_id: str,
    terms: list[str],
    *,
    message: str,
    market: str,
    read_json_file: ReadJsonFile,
    snippet_chars: int,
) -> list[dict[str, Any]]:
    """Search canonical evidence/retrieval indexes before scanning full report text."""
    sources = (
        ("evidence/evidence_index.json", "evidence", "wiki_evidence"),
        ("semantic/retrieval_index.json", "chunks", "wiki_retrieval_index"),
        ("semantic/evidence_semantic.json", "evidence", "wiki_semantic_evidence"),
    )
    rows: list[dict[str, Any]] = []
    seen: set[tuple[Any, Any, Any, Any]] = set()
    for relative_path, collection_key, source_type in sources:
        payload = read_json_file(company_dir / relative_path) or {}
        items = payload.get(collection_key) if isinstance(payload, dict) else []
        if not isinstance(items, list):
            continue
        for order, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            item_report_id = str(item.get("report_id") or "").strip()
            if item_report_id and item_report_id != report_id:
                continue
            text = " ".join(
                str(item.get(key) or "")
                for key in (
                    "metric_key",
                    "metric_name",
                    "topic",
                    "heading",
                    "statement_title",
                    "quote_text",
                    "text",
                    "summary",
                )
            ).strip()
            score = agent_runtime_fallback_contexts._line_match_score(text, terms)
            if score <= 0 or not agent_runtime_fallback_contexts._line_matches_any_term(text, terms):
                continue
            pdf_page = item.get("pdf_page_number") or item.get("pdf_page")
            table_index = item.get("table_index")
            md_line = item.get("md_line") or item.get("line") or item.get("markdown_line")
            key = (item.get("evidence_id") or item.get("chunk_id"), pdf_page, table_index, md_line)
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "source_type": source_type,
                    "file": relative_path,
                    "score": score + 20 + pdf_market_intent_bonus(message, market, text),
                    "order": order,
                    "snippet": text[:snippet_chars],
                    "task_id": item.get("task_id"),
                    "pdf_page": pdf_page,
                    "table_index": table_index,
                    "md_line": md_line,
                    "evidence_id": item.get("evidence_id"),
                }
            )
    rows.sort(key=lambda row: (-int(row.get("score") or 0), int(row.get("order") or 0)))
    return rows


def wiki_fulltext_fallback_result(
    message: str,
    context: Any | None = None,
    *,
    fallback_terms: Sequence[str],
    generic_terms: set[str] | tuple[str, ...],
    max_snippets: int,
    snippet_chars: int,
    is_general_assistant_request: Callable[[str], bool],
    resolve_company_dir: Callable[[str, Any | None], Path | None],
    context_company: Callable[[Any | None], dict[str, Any]],
    read_json_file: ReadJsonFile,
    primary_report_for_company: Callable[[Path, str | None, Any | None], dict[str, Any]],
) -> dict[str, Any] | None:
    if not should_consider_wiki_fulltext_fallback(
        message,
        context,
        fallback_terms=fallback_terms,
        generic_terms=generic_terms,
        is_general_assistant_request=is_general_assistant_request,
        resolve_company_dir=resolve_company_dir,
        context_company=context_company,
    ):
        return None
    company_dir = resolve_company_dir(message, context)
    if not company_dir:
        return None
    report = primary_report_for_company(company_dir, message, context)
    if report.get("selection_status") == "identity_mismatch":
        return None
    report_id = str(report.get("report_id") or "2025-annual")
    report_root = company_dir / "reports" / report_id
    report_md = next(
        (
            path
            for candidate in (
                report.get("report_md"),
                report_root / "report.md",
                report_root / "sections" / "report_complete.md",
                report_root / "parser" / "report_complete.md",
            )
            if (path := _existing_scoped_report_path(company_dir, candidate)) is not None
        ),
        report_root / "report.md",
    )
    document_full_path = next(
        (
            path
            for candidate in (
                report.get("document_full"),
                report_root / "document_full.json",
                report_root / "parser" / "document_full.json",
            )
            if (path := _existing_scoped_report_path(company_dir, candidate)) is not None
        ),
        report_root / "document_full.json",
    )
    try:
        report_md_file = report_md.relative_to(company_dir).as_posix()
    except ValueError:
        report_md_file = report_md.name
    try:
        document_full_file = document_full_path.relative_to(company_dir).as_posix()
    except ValueError:
        document_full_file = document_full_path.name
    if not report_md.is_file() and not document_full_path.is_file():
        return None

    company = read_json_file(company_dir / "company.json") or {}
    aliases = agent_runtime_fallback_contexts._company_aliases(company_dir.name, company)
    terms = agent_runtime_fallback_contexts._fallback_search_terms(message, aliases, tuple(fallback_terms))
    market = str(company.get("market") or "CN").upper()
    terms = list(dict.fromkeys([*terms, *expand_pdf_market_query_terms(message, market)]))
    if not terms:
        return None
    specific_terms = agent_runtime_fallback_contexts._specific_fulltext_terms(terms, generic_terms)
    if not specific_terms:
        return None

    rows = indexed_evidence_rows(
        company_dir,
        report_id,
        specific_terms,
        message=message,
        market=market,
        read_json_file=read_json_file,
        snippet_chars=snippet_chars,
    )[:max_snippets]
    for row in rows:
        row["task_id"] = row.get("task_id") or report.get("task_id")
    tables = table_meta_by_line(company_dir, report_id, read_json_file=read_json_file)
    lines: list[str] = []
    if report_md.is_file():
        try:
            lines = report_md.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            lines = []
    if lines:
        scored_lines = [
            (
                agent_runtime_fallback_contexts._line_match_score(line, terms)
                + pdf_market_intent_bonus(message, market, line),
                index,
                line,
            )
            for index, line in enumerate(lines, start=1)
        ]
        scored_lines = [
            (score, index, line)
            for score, index, line in scored_lines
            if score > 0 and agent_runtime_fallback_contexts._line_matches_any_term(line, specific_terms)
        ]
        scored_lines.sort(key=lambda item: (-item[0], item[1]))
        seen_lines: set[int] = set()
        for score, line_number, line in scored_lines[: max_snippets * 2]:
            if len(rows) >= max_snippets:
                break
            if any(abs(line_number - seen) <= 1 for seen in seen_lines):
                continue
            seen_lines.add(line_number)
            table = agent_runtime_fallback_contexts._nearest_table_meta(tables, line_number)
            pdf_page = (
                (table.get("pdf_page_number") or table.get("pdf_page")) if table else None
            ) or agent_runtime_fallback_contexts._nearest_report_pdf_page(lines, line_number)
            table_index = table.get("table_index") if table else None
            rows.append(
                {
                    "source_type": "wiki_report_fulltext",
                    "file": report_md_file,
                    "score": score,
                    "snippet": agent_runtime_fallback_contexts._snippet_window(
                        lines,
                        line_number,
                        radius=2,
                        snippet_chars=snippet_chars,
                    ),
                    "task_id": report.get("task_id"),
                    "pdf_page": pdf_page,
                    "table_index": table_index,
                    "md_line": line_number,
                }
            )

    if len(rows) < max_snippets and document_full_path.is_file():
        document_full = read_json_file(document_full_path) or {}
        if isinstance(document_full, dict):
            existing_keys = {
                (row.get("pdf_page"), row.get("table_index"), row.get("md_line"), row.get("snippet"))
                for row in rows
            }
            items = document_full_text_items(document_full, terms, snippet_chars=snippet_chars)
            for item in items:
                item["score"] = int(item.get("score") or 0) + pdf_market_intent_bonus(
                    message,
                    market,
                    str(item.get("text") or ""),
                )
            items.sort(key=lambda item: (-int(item.get("score") or 0), int(item.get("order") or 0)))
            for item in items:
                item_text = str(item.get("text") or "")
                if not agent_runtime_fallback_contexts._line_matches_any_term(item_text, specific_terms):
                    continue
                key = (item.get("pdf_page"), item.get("table_index"), item.get("md_line"), item.get("text"))
                if key in existing_keys:
                    continue
                rows.append(
                    {
                        "source_type": "wiki_document_full",
                        "file": document_full_file,
                        "score": item.get("score"),
                        "snippet": str(item.get("text") or "")[:snippet_chars],
                        "task_id": report.get("task_id"),
                        "pdf_page": item.get("pdf_page"),
                        "table_index": item.get("table_index"),
                        "md_line": item.get("md_line"),
                        "content_type": item.get("type"),
                    }
                )
                existing_keys.add(key)
                if len(rows) >= max_snippets:
                    break

    translated_terms = expand_pdf_market_query_terms(message, market)
    if translated_terms and rows:
        def translated_match_count(row: dict[str, Any]) -> int:
            snippet = str(row.get("snippet") or "")
            return sum(
                1
                for term in translated_terms
                if agent_runtime_fallback_contexts._line_matches_any_term(snippet, [term])
            )

        best_match_count = max(translated_match_count(row) for row in rows)
        if best_match_count > 0:
            rows = [row for row in rows if translated_match_count(row) == best_match_count]

    deduped_rows: dict[str, dict[str, Any]] = {}
    for row in rows:
        snippet_key = re.sub(r"\[PDF_PAGE:\s*\d+\]", "", str(row.get("snippet") or ""))
        snippet_key = re.sub(r"\s+", "", snippet_key).casefold()
        if not snippet_key:
            snippet_key = repr((row.get("pdf_page"), row.get("table_index"), row.get("md_line")))
        existing = deduped_rows.get(snippet_key)
        if existing is None or int(row.get("pdf_page") or 0) > int(existing.get("pdf_page") or 0):
            deduped_rows[snippet_key] = row
    rows = list(deduped_rows.values())

    if not rows:
        return None

    return {
        "company_dir": company_dir,
        "market": market,
        "company_id": company.get("company_id") or company_dir.name,
        "company_name": company.get("company_short_name") or company.get("company_full_name") or company_dir.name,
        "stock_code": company.get("stock_code") or company_dir.name.split("-", 1)[0],
        "report_id": report_id,
        "filing_id": report.get("filing_id"),
        "parse_run_id": report.get("parse_run_id"),
        "task_id": report.get("task_id"),
        "report_md": report_md,
        "document_full": document_full_path,
        "terms": terms,
        "rows": rows,
    }


def render_wiki_fulltext_fallback_context(
    result: dict[str, Any],
    *,
    evidence_url: Callable[[Any, Any, Any, str], str | None],
) -> str:
    rows = result.get("rows") or []
    lines = [
        (
            "以下是后端在结构化 Wiki metrics/evidence/semantic 未命中或命中不足时，"
            "从完整年报 Markdown 和完整解析 JSON 确定性检索出的全文兜底证据。"
        ),
        "输出要求：",
        "- 优先基于这些原文片段回答；不得再说“未找到/无法回答”，除非下方片段确实无关。",
        (
            "- `reports/<report_id>/report.md` 是完整报告正文；"
            "`reports/<report_id>/document_full.json` 是完整解析容器。不要使用 `graph/report.md`，"
            "不要把 `report.json` 当 full json。"
        ),
        "- 必须在 `## 引用来源` 保留 `source_type/file/task_id/pdf_page/table_index/md_line`；字段为空时写 `未返回`。",
        (
            f"- 公司: {result.get('company_name')} / 代码 {result.get('stock_code')} / "
            f"company_id={result.get('company_id')}"
        ),
        f"- 报告: report_id={result.get('report_id')} / task_id={result.get('task_id') or '未返回'}",
        f"- 完整 Markdown: {result.get('report_md')}",
        f"- 完整 full JSON: {result.get('document_full')}",
        f"- 检索词: {', '.join(result.get('terms') or [])}",
        "",
        "## 全文兜底证据",
    ]
    for index, row in enumerate(rows, start=1):
        table_index = row.get("table_index") if row.get("table_index") not in (None, "") else "未返回"
        lines.extend(
            [
                "",
                f"### F{index}. {row.get('source_type')} / score={row.get('score')}",
                f"- file={row.get('file')}",
                (
                    f"- task_id={row.get('task_id') or '未返回'}, "
                    f"pdf_page={row.get('pdf_page') or '未返回'}, "
                    f"table_index={table_index}, md_line={row.get('md_line') or '未返回'}"
                ),
                "```text",
                str(row.get("snippet") or "").strip(),
                "```",
            ]
        )
    lines.extend(["", "## 全文兜底引用"])
    for index, row in enumerate(rows, start=1):
        task_id = row.get("task_id")
        pdf_page = row.get("pdf_page")
        table_index = row.get("table_index")
        links = []
        pdf_url = evidence_url(task_id, pdf_page, table_index, "pdf")
        page_url = evidence_url(task_id, pdf_page, table_index, "page")
        table_url = evidence_url(task_id, pdf_page, table_index, "table")
        if pdf_url:
            links.append(f"[打开PDF页]({pdf_url})")
        if page_url:
            links.append(f"[查看页来源]({page_url})")
        if table_url:
            links.append(f"[查看表格]({table_url})")
        lines.append(
            f"[F{index}] source_type={row.get('source_type')}, file={row.get('file')}, "
            f"metric={','.join(result.get('terms') or []) or '全文检索'}, period={result.get('report_id')}, "
            f"task_id={task_id or '未返回'}, pdf_page={pdf_page or '未返回'}, "
            f"table_index={table_index if table_index not in (None, '') else '未返回'}, "
            f"md_line={row.get('md_line') or '未返回'}"
            + (("，" + "，".join(links)) if links else "")
        )
    return "\n".join(lines)


def build_wiki_fulltext_fallback_context(
    message: str,
    context: Any | None = None,
    *,
    fallback_terms: Sequence[str],
    generic_terms: set[str] | tuple[str, ...],
    max_snippets: int,
    snippet_chars: int,
    is_general_assistant_request: Callable[[str], bool],
    resolve_company_dir: Callable[[str, Any | None], Path | None],
    context_company: Callable[[Any | None], dict[str, Any]],
    read_json_file: ReadJsonFile,
    primary_report_for_company: Callable[[Path, str | None, Any | None], dict[str, Any]],
    evidence_url: Callable[[Any, Any, Any, str], str | None],
) -> str | None:
    result = wiki_fulltext_fallback_result(
        message,
        context,
        fallback_terms=fallback_terms,
        generic_terms=generic_terms,
        max_snippets=max_snippets,
        snippet_chars=snippet_chars,
        is_general_assistant_request=is_general_assistant_request,
        resolve_company_dir=resolve_company_dir,
        context_company=context_company,
        read_json_file=read_json_file,
        primary_report_for_company=primary_report_for_company,
    )
    if not result:
        return None
    return render_wiki_fulltext_fallback_context(result, evidence_url=evidence_url)


def build_company_wiki_scope_context(
    message: str,
    context: Any | None = None,
    *,
    wiki_root: Any,
    resolve_company_dir: Callable[[str, Any | None], Path | None],
    read_json_file: ReadJsonFile,
    primary_report_for_company: Callable[[Path, str | None, Any | None], dict[str, Any]],
    company_artifact_paths: Callable[[Path, str, bool], dict[str, Path]],
    clean_context_value: Callable[[Any], str],
) -> str | None:
    company_dir = resolve_company_dir(message, context)
    if not company_dir:
        return None
    company = read_json_file(company_dir / "company.json") or {}
    report = primary_report_for_company(company_dir, message, context)
    if report.get("selection_status") == "identity_mismatch":
        return None
    report_id = str(report.get("report_id") or "2025-annual")
    strict_report = report.get("selection_status") == "identity_exact"
    paths = company_artifact_paths(company_dir, report_id, strict_report)
    if strict_report:
        paths = dict(paths)
        for artifact_key in ("report_md", "document_full"):
            if path := _existing_scoped_report_path(company_dir, report.get(artifact_key)):
                paths[artifact_key] = path

    company_name = (
        company.get("company_short_name")
        or company.get("company_full_name")
        or (company_dir.name.split("-", 1)[1] if "-" in company_dir.name else company_dir.name)
    )
    stock_code = company.get("stock_code") or company_dir.name.split("-", 1)[0]
    manifest = report.get("_manifest") if isinstance(report.get("_manifest"), Mapping) else {}
    company_id = (
        manifest.get("company_id")
        or report.get("company_id")
        or company.get("company_id")
        or company.get("company_wiki_id")
        or "未返回"
    )
    lines = [
        (
            "以下是后端已确定的单家公司 Wiki 工作集。回答本题时必须以此为公司边界；"
            "除非用户在本轮明确指定其他公司，不得沿用会话历史中的其他公司、"
            "备份 wiki 或 profile 目录。"
        ),
        f"- Wiki 根目录: {wiki_root}",
        f"- 公司: {company_name} / 代码 {stock_code} / company_id={company_id}",
        f"- 公司目录: {company_dir}",
        f"- 主报告: report_id={report_id}, task_id={report.get('task_id') or '未返回'}",
        (
            "- 数据优先级: 三大表 `three_statements.json` > 核心指标 `key_metrics.json` > "
            "legacy `evidence/evidence_index.json` / `evidence/pdf_refs.json` > semantic "
            "`evidence_semantic.json` / `retrieval_index.json` > `reports/<report_id>/report.json` "
            "的 tables > 完整 `reports/<report_id>/report.md` > 完整 "
            "`reports/<report_id>/document_full.json` > PostgreSQL fallback。"
        ),
        (
            "- 深度回溯协议: 任何一层证据文件存在但为空、字段为 `未返回`、或没有可打开 "
            "`/api/pdf_page` / `/api/source` 链接时，不得下结论说“无法溯源”；"
            "必须继续检查下一层，尤其是 `report.json.tables`、"
            "`document_full.content_list_enhanced.tables` 和 semantic evidence。"
        ),
        (
            "- 溯源合格标准: 至少给出 `task_id` + `pdf_page` 或 `table_index`，并优先生成 "
            "`/api/pdf_page/{task_id}/{page}`、`/api/source/{task_id}/page/{page}`、"
            "`/api/source/{task_id}/table/{table_index}`。`pdf_page=未返回` 或 "
            "`table_index=未返回` 只能作为临时状态，不能作为最终证据充分结论。"
        ),
        (
            "- 工作流约束: 先基于三大表确认金额、期间和表格来源，"
            "再用附注/semantic 解释构成或原因；不得用附注表替代三大表主表口径。"
        ),
        (
            "- 兜底约束: 不得读取 `graph/report.md` 作为完整报告；不得把 "
            "`reports/<report_id>/report.json` 当作 full json。完整解析容器固定为 "
            "`document_full.json`。"
        ),
    ]
    if strict_report:
        lines.append(
            "- ResearchIdentity 精确匹配: "
            f"filing_id={report.get('filing_id')}, parse_run_id={report.get('parse_run_id')}；"
            "禁止回退 primary/latest 或跨报告 semantic/metrics。"
        )
    if company.get("industry"):
        lines.append(f"- 行业: {clean_context_value(company['industry'])}")
    for label, key in (
        ("三大表", "three_statements"),
        ("核心指标", "key_metrics"),
        ("校验结果", "validation"),
        ("证据索引", "evidence_index"),
        ("PDF页码映射", "pdf_refs"),
        ("语义证据", "evidence_semantic"),
        ("年报Markdown", "report_md"),
        ("完整full JSON", "document_full"),
        ("年报JSON", "report_json"),
        ("语义检索索引", "retrieval_index"),
        ("附注跳转", "document_links"),
        ("附注表索引", "note_links"),
    ):
        path = paths.get(key)
        if path:
            lines.append(f"- {label}: {path}")
    return "\n".join(lines)


__all__ = [
    "build_company_wiki_scope_context",
    "build_wiki_fulltext_fallback_context",
    "company_artifact_paths",
    "document_full_text_items",
    "existing_company_file",
    "primary_report_for_company",
    "render_wiki_fulltext_fallback_context",
    "report_is_annual",
    "report_is_quarterly",
    "report_text_blob",
    "select_report_for_research_identity",
    "select_report_from_company_json",
    "should_consider_wiki_fulltext_fallback",
    "table_meta_by_line",
    "wiki_fulltext_fallback_result",
]
