#!/usr/bin/env python3
"""Backtest whether parser result artifacts are ready for Wiki ingestion."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any


BASE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BASE_DIR.parents[1]
WIKI_ROOT = REPO_ROOT / "data" / "wiki"
sys.path.insert(0, str(BASE_DIR))

import pdf_parser_result_manifest_service as manifests  # noqa: E402


CORE_STATEMENTS = ("balance_sheet", "income_statement", "cash_flow_statement")

IDENTITY_KEYS_BY_MARKET = {
    "CN": ("stock_code", "ticker"),
    "HK": ("ticker", "stock_code"),
    "EU": ("ticker", "stock_code"),
    "JP": ("ticker", "stock_code", "security_code", "securities_code", "edinet_code"),
    "KR": ("ticker", "stock_code", "corp_code"),
}

CORE_CANONICAL_GROUPS = {
    "balance_sheet": (
        {"total_assets"},
        {"total_equity", "net_assets"},
    ),
    "income_statement": (
        {"net_profit", "parent_net_profit"},
        {"operating_revenue", "total_income", "operating_profit", "total_profit", "profit_before_tax"},
    ),
    "cash_flow_statement": (
        {"operating_cash_flow_net", "cash_generated_from_operations"},
        {"investing_cash_flow_net", "financing_cash_flow_net", "cash_equivalents_net_increase", "cash_equivalents_ending"},
    ),
}

FORMAL_TITLE_TERMS = {
    "balance_sheet": (
        "balance sheet",
        "financial position",
        "资产负债表",
        "財政状態",
        "貸借対照表",
        "재무상태표",
        "재무 상태표",
    ),
    "income_statement": (
        "income statement",
        "statement of income",
        "profit or loss",
        "statement of operations",
        "statements of operations",
        "利润表",
        "損益計算書",
        "包括利益",
        "손익계산서",
        "포괄손익",
    ),
    "cash_flow_statement": (
        "cash flow",
        "cash flows",
        "现金流量表",
        "キャッシュ・フロー",
        "현금흐름",
    ),
}

SUSPICIOUS_TITLE_TERMS = (
    "compared to",
    "discussion",
    "review",
    "analysis",
    "operating results of",
    "results of operations",
    "business review",
    "management report",
)

SPRAWL_ITEM_LIMITS = {
    "balance_sheet": 320,
    "income_statement": 220,
    "cash_flow_statement": 120,
}

UNCLASSIFIED_SOURCE_RATIO_LIMIT = 0.35

NON_ANNUAL_REPORT_TERMS = (
    "quarter",
    "quarterly",
    "q1",
    "q2",
    "q3",
    "interim",
    "half-year",
    "half year",
    "semi-annual",
    "semiannual",
    "三季报",
    "一季报",
    "季报",
    "季度报告",
    "半年报",
    "半年度",
    "中期报告",
)

DEFAULT_BACKTEST_PROFILE = {
    "profile_id": "generic_pdf_wiki_ready_v1",
    "profile_notes": (
        "Common parser result contract and conservative statement quality checks.",
    ),
    "sprawl_item_limits": SPRAWL_ITEM_LIMITS,
    "core_canonical_groups": CORE_CANONICAL_GROUPS,
    "unclassified_source_ratio_limit": None,
    "unclassified_source_min_items": 30,
}

MARKET_BACKTEST_PROFILES = {
    "HK": {
        "profile_id": "hkex_pdf_wiki_ready_v1",
        "profile_notes": (
            "HK annual reports include industrial, bank, insurance, and US-style issuers; core readiness accepts broader HKFRS/IFRS balance-sheet and cash-flow anchors.",
            "A statement with no mapped cash-flow facts remains a blocker.",
        ),
        "core_canonical_groups": {
            "balance_sheet": (
                {
                    "total_assets",
                    "total_liabilities_and_equity",
                    "current_assets",
                    "non_current_assets",
                    "cash_and_cash_equivalents",
                },
                {
                    "total_equity",
                    "net_assets",
                    "parent_equity",
                    "nci_equity",
                    "total_liabilities",
                    "current_liabilities",
                    "non_current_liabilities",
                },
            ),
            "income_statement": (
                {"net_profit", "parent_net_profit", "total_profit", "operating_profit"},
                {"operating_revenue", "total_income", "gross_profit", "finance_costs", "income_tax_expense"},
            ),
            "cash_flow_statement": (
                {
                    "operating_cash_flow_net",
                    "cash_generated_from_operations",
                    "investing_cash_flow_net",
                    "financing_cash_flow_net",
                    "cash_equivalents_net_increase",
                    "cash_equivalents_ending",
                },
            ),
        },
        "sprawl_item_limits": {
            "balance_sheet": 680,
            "income_statement": 380,
            "cash_flow_statement": 140,
        },
        "unclassified_source_ratio_limit": None,
    },
    "EU": {
        "profile_id": "eu_ifrs_pdf_wiki_ready_v1",
        "profile_notes": (
            "EU IFRS issuers vary by industry; readiness uses broad IFRS anchors and keeps empty cash-flow extraction as a blocker.",
            "Small canonical fact sets are expected from the current EU profile and should be improved by extractor work, not by generic A-share thresholds.",
        ),
        "core_canonical_groups": {
            "balance_sheet": (
                {"total_assets", "current_assets", "non_current_assets", "cash_and_cash_equivalents"},
                {
                    "total_equity",
                    "net_assets",
                    "parent_equity",
                    "equity_attributable_parent",
                    "total_liabilities",
                    "current_liabilities",
                    "non_current_liabilities",
                },
            ),
            "income_statement": (
                {"net_profit", "parent_net_profit", "total_profit", "profit_before_tax", "operating_profit"},
                {"operating_revenue", "total_income", "gross_profit", "finance_costs", "income_tax_expense"},
            ),
            "cash_flow_statement": (
                {
                    "operating_cash_flow_net",
                    "cash_generated_from_operations",
                    "investing_cash_flow_net",
                    "financing_cash_flow_net",
                    "cash_equivalents_net_increase",
                    "cash_equivalents_ending",
                    "cash_equivalents_beginning",
                },
            ),
        },
        "sprawl_item_limits": {
            "balance_sheet": 120,
            "income_statement": 120,
            "cash_flow_statement": 80,
        },
        "unclassified_source_ratio_limit": None,
    },
    "JP": {
        "profile_id": "jp_edinet_wiki_ready_v1",
        "profile_notes": (
            "EDINET PDF statements can be split across adjacent tables and pages.",
            "High parsed_financial_table ratio is tracked in quality profile, not treated as note-sprawl by itself.",
        ),
        "sprawl_item_limits": {
            "balance_sheet": 360,
            "income_statement": 380,
            "cash_flow_statement": 120,
        },
        "unclassified_source_ratio_limit": None,
    },
    "KR": {
        "profile_id": "kr_dart_wiki_ready_v1",
        "profile_notes": (
            "DART PDFs contain many note/detail tables; unclassified statement facts are suspicious unless strongly detected.",
            "Larger Korean financial statements are allowed, but extreme item counts remain review warnings.",
        ),
        "sprawl_item_limits": {
            "balance_sheet": 560,
            "income_statement": 500,
            "cash_flow_statement": 140,
        },
        "unclassified_source_ratio_limit": UNCLASSIFIED_SOURCE_RATIO_LIMIT,
    },
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def text_file_chars(path: Path) -> int:
    try:
        return len(path.read_text(encoding="utf-8", errors="ignore"))
    except OSError:
        return 0


def compact_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def normalize_financial_text(value: Any) -> str:
    return re.sub(r"[\s（）()_\-：:、,，;；/]+", "", str(value or "").lower())


def infer_stock_code_from_text(text: str) -> str:
    for pattern in (r"\bCN[_-](\d{6})\b", r"\b(?:SH|SZ|BJ)?[_-]?(\d{6})\b"):
        match = re.search(pattern, text or "", flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return ""


def infer_company_name_from_filename(filename: str) -> str:
    text = Path(str(filename or "")).stem
    text = re.sub(r"[_-](?:CN|SH|SZ|BJ)[_-]\d{6}.*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"[_-]\d{6}.*$", "", text)
    text = re.sub(r"(?:20\d{2}[-年].*)$", "", text)
    text = re.sub(r"(?:集团股份有限公司|股份有限公司)$", "", text)
    parts = [part.strip("_- ：:") for part in re.split(r"[：:_-]+", text) if part.strip("_- ：:")]
    if parts:
        seen: set[str] = set()
        deduped: list[str] = []
        for part in parts:
            normalized = normalize_financial_text(part)
            if normalized and normalized not in seen:
                seen.add(normalized)
                deduped.append(part)
        text = deduped[0] if len(deduped) == 1 else "".join(deduped[:1])
    text = text.strip("_- ：: ")
    return re.sub(r"\s+", "", text)


def load_company_catalog(wiki_root: Path = WIKI_ROOT) -> list[dict[str, Any]]:
    catalog = read_json(wiki_root / "_meta" / "company_catalog.json", {})
    companies = catalog.get("companies") if isinstance(catalog, dict) else None
    return [company for company in companies if isinstance(company, dict)] if isinstance(companies, list) else []


def company_catalog_aliases(company: dict[str, Any]) -> list[str]:
    aliases = [
        company.get("company_id"),
        company.get("stock_code"),
        company.get("company_short_name"),
        company.get("company_full_name"),
    ]
    if isinstance(company.get("aliases"), list):
        aliases.extend(company["aliases"])
    return [str(alias).strip() for alias in aliases if str(alias or "").strip()]


def catalog_company_path(company: dict[str, Any], wiki_root: Path = WIKI_ROOT) -> Path:
    rel_path = company.get("company_path") or company.get("path") or f"companies/{company.get('company_id') or ''}"
    return wiki_root / str(rel_path)


def catalog_company_by_stock_code(stock_code: str, wiki_root: Path = WIKI_ROOT) -> dict[str, Any] | None:
    normalized_code = str(stock_code or "").strip()
    if not normalized_code:
        return None
    for company in load_company_catalog(wiki_root):
        if str(company.get("stock_code") or "").strip() == normalized_code:
            return company
    return None


def catalog_company_by_text(text: str, wiki_root: Path = WIKI_ROOT) -> dict[str, Any] | None:
    haystack = normalize_financial_text(text)
    if not haystack:
        return None

    matches: list[tuple[int, str, dict[str, Any]]] = []
    for company in load_company_catalog(wiki_root):
        matched_aliases: list[str] = []
        for alias in company_catalog_aliases(company):
            normalized = normalize_financial_text(alias)
            if not normalized:
                continue
            if re.fullmatch(r"\d{6}", normalized):
                if normalized in haystack:
                    matched_aliases.append(normalized)
                continue
            if len(normalized) >= 2 and normalized in haystack:
                matched_aliases.append(normalized)
        if matched_aliases:
            best_alias = max(matched_aliases, key=len)
            matches.append((len(best_alias), best_alias, company))

    if not matches:
        return None
    matches.sort(key=lambda item: (-item[0], item[1], str(item[2].get("company_id") or "")))
    top_score = matches[0][0]
    top_matches = [match for match in matches if match[0] == top_score]
    if len(top_matches) != 1:
        return None
    return top_matches[0][2]


def iter_result_dirs(results_dir: Path) -> list[Path]:
    return sorted(path for path in results_dir.iterdir() if path.is_dir())


def market_from_metadata(metadata: dict[str, Any]) -> str:
    return str(metadata.get("market") or "").strip().upper()


def market_backtest_profile(market: str) -> dict[str, Any]:
    profile = dict(DEFAULT_BACKTEST_PROFILE)
    market_profile = MARKET_BACKTEST_PROFILES.get(str(market or "").upper(), {})
    profile.update(market_profile)
    profile["sprawl_item_limits"] = {
        **dict(DEFAULT_BACKTEST_PROFILE["sprawl_item_limits"]),
        **dict(market_profile.get("sprawl_item_limits") or {}),
    }
    profile["core_canonical_groups"] = market_profile.get("core_canonical_groups") or DEFAULT_BACKTEST_PROFILE["core_canonical_groups"]
    return profile


def profile_for_report(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "profile_id": profile.get("profile_id"),
        "sprawl_item_limits": profile.get("sprawl_item_limits") or {},
        "core_canonical_groups": {
            statement_type: [sorted(group) for group in groups]
            for statement_type, groups in (profile.get("core_canonical_groups") or {}).items()
        },
        "unclassified_source_ratio_limit": profile.get("unclassified_source_ratio_limit"),
        "unclassified_source_min_items": profile.get("unclassified_source_min_items"),
        "profile_notes": list(profile.get("profile_notes") or ()),
    }


def first_present(payload: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = payload.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def source_identity_text(metadata: dict[str, Any], result_dir: Path) -> str:
    return "\n".join(
        str(value or "")
        for value in (
            metadata.get("company_name"),
            metadata.get("company_slug"),
            metadata.get("filename"),
            metadata.get("source_file"),
            result_dir.name,
        )
        if str(value or "").strip()
    )


def company_name_from_catalog(company: dict[str, Any]) -> str:
    return str(
        company.get("company_short_name")
        or company.get("company_full_name")
        or (str(company.get("company_id") or "").split("-", 1)[1] if "-" in str(company.get("company_id") or "") else "")
        or ""
    ).strip()


def identity_from_catalog_company(company: dict[str, Any], *, source: str, inferred_name: str = "", inferred_code: str = "") -> dict[str, Any]:
    stock_code = str(company.get("stock_code") or inferred_code or "").strip()
    company_path = catalog_company_path(company)
    company_path_text = str(company_path.relative_to(REPO_ROOT)) if company_path.is_relative_to(REPO_ROOT) else str(company_path)
    return {
        "source": source,
        "confidence": "high",
        "company_name": company_name_from_catalog(company) or inferred_name,
        "ticker": stock_code,
        "stock_code": stock_code,
        "company_wiki_id": company.get("company_id") or stock_code,
        "company_path": company_path_text,
        "inferred_company_name": inferred_name,
        "inferred_stock_code": inferred_code,
        "catalog_match": {
            "company_id": company.get("company_id"),
            "stock_code": company.get("stock_code"),
            "company_short_name": company.get("company_short_name"),
            "company_path": company.get("company_path"),
        },
    }


def resolve_company_identity(metadata: dict[str, Any], financial_data: dict[str, Any], result_dir: Path) -> dict[str, Any]:
    market = market_from_metadata(metadata)
    identity_key = first_present(metadata, IDENTITY_KEYS_BY_MARKET.get(market, ("ticker", "stock_code")))
    company_name = str(metadata.get("company_name") or metadata.get("company_slug") or "").strip()
    source_text = source_identity_text(metadata, result_dir)
    inferred_code = infer_stock_code_from_text(source_text)
    inferred_name = company_name or infer_company_name_from_filename(str(metadata.get("filename") or metadata.get("source_file") or ""))

    if market == "CN":
        code = identity_key or inferred_code
        if code:
            catalog_company = catalog_company_by_stock_code(code)
            if catalog_company:
                return identity_from_catalog_company(
                    catalog_company,
                    source="metadata_stock_code" if identity_key else "filename_stock_code_catalog",
                    inferred_name=inferred_name,
                    inferred_code=inferred_code,
                )
            return {
                "source": "metadata" if identity_key and company_name else "filename_stock_code",
                "confidence": "medium" if inferred_name else "low",
                "company_name": company_name or inferred_name,
                "ticker": code,
                "stock_code": code,
                "company_wiki_id": code,
                "company_path": "",
                "inferred_company_name": inferred_name,
                "inferred_stock_code": inferred_code,
                "catalog_match": None,
            }

        catalog_company = catalog_company_by_text(source_text)
        if catalog_company:
            return identity_from_catalog_company(
                catalog_company,
                source="wiki_catalog_alias",
                inferred_name=inferred_name,
                inferred_code=inferred_code,
            )

    return {
        "source": "metadata" if identity_key and company_name else "unresolved",
        "confidence": "high" if identity_key and company_name else "none",
        "company_name": company_name or inferred_name,
        "ticker": identity_key,
        "stock_code": identity_key if market == "CN" else str(metadata.get("stock_code") or "").strip(),
        "company_wiki_id": identity_key,
        "company_path": "",
        "inferred_company_name": inferred_name,
        "inferred_stock_code": inferred_code,
        "catalog_match": None,
    }


def resolved_metadata(metadata: dict[str, Any], identity_resolution: dict[str, Any]) -> dict[str, Any]:
    resolved = dict(metadata)
    for key in ("company_name", "ticker", "stock_code", "company_wiki_id", "company_path"):
        value = identity_resolution.get(key)
        if value:
            resolved[key] = value
    return resolved


def is_explicit_non_annual_report(metadata: dict[str, Any], financial_data: dict[str, Any]) -> bool:
    text = compact_text(
        " ".join(
            str(value or "")
            for value in (
                metadata.get("report_kind"),
                metadata.get("report_type"),
                metadata.get("filename"),
                metadata.get("source_file"),
                financial_data.get("report_kind"),
                financial_data.get("report_type"),
            )
        )
    )
    return any(term in text for term in NON_ANNUAL_REPORT_TERMS)


def fiscal_year(metadata: dict[str, Any], financial_data: dict[str, Any]) -> int | None:
    for payload in (metadata, financial_data):
        for key in ("fiscal_year", "report_year"):
            value = payload.get(key)
            if isinstance(value, int):
                return value
            if str(value or "").isdigit():
                return int(str(value))
    return None


def statement_by_type(financial_data: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for statement in financial_data.get("statements") or []:
        if isinstance(statement, dict):
            grouped[str(statement.get("statement_type") or "")].append(statement)
    return grouped


def statement_items(statement: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in statement.get("items") or [] if isinstance(item, dict)]


def canonical_names(statements: list[dict[str, Any]]) -> set[str]:
    names: set[str] = set()
    for statement in statements:
        for item in statement_items(statement):
            name = item.get("canonical_name") or item.get("name")
            if name:
                names.add(str(name))
    return names


def has_value(item: dict[str, Any]) -> bool:
    values = item.get("values")
    if isinstance(values, dict) and any(value is not None and str(value) != "" for value in values.values()):
        return True
    return item.get("value") is not None or bool(str(item.get("raw_value") or "").strip())


def item_has_evidence(item: dict[str, Any]) -> bool:
    evidence = item.get("evidence")
    if isinstance(evidence, dict) and (
        evidence.get("page_number")
        or evidence.get("table_index") is not None
        or evidence.get("quote_text")
        or evidence.get("source_id")
    ):
        return True
    sources = item.get("sources")
    if isinstance(sources, dict):
        for source in sources.values():
            if isinstance(source, dict) and (source.get("table_index") is not None or source.get("line") is not None):
                return True
    return False


def item_source_type(item: dict[str, Any]) -> str:
    evidence = item.get("evidence")
    if isinstance(evidence, dict) and evidence.get("source_type"):
        return str(evidence.get("source_type"))
    sources = item.get("sources")
    if isinstance(sources, dict):
        for source in sources.values():
            if isinstance(source, dict) and source.get("source_type"):
                return str(source.get("source_type"))
    return "unknown"


def source_type_counts(statements: list[dict[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for statement in statements:
        for item in statement_items(statement):
            counts[item_source_type(item)] += 1
    return dict(sorted(counts.items()))


def evidence_ratio(statements: list[dict[str, Any]]) -> float:
    items = [item for statement in statements for item in statement_items(statement) if has_value(item)]
    if not items:
        return 0.0
    return sum(1 for item in items if item_has_evidence(item)) / len(items)


def source_table_indexes(statements: list[dict[str, Any]]) -> set[int]:
    indexes: set[int] = set()
    for statement in statements:
        for value in statement.get("table_indexes") or []:
            if isinstance(value, int):
                indexes.add(value)
        for item in statement_items(statement):
            evidence = item.get("evidence")
            if isinstance(evidence, dict) and isinstance(evidence.get("table_index"), int):
                indexes.add(evidence["table_index"])
            sources = item.get("sources")
            if isinstance(sources, dict):
                for source in sources.values():
                    if isinstance(source, dict) and isinstance(source.get("table_index"), int):
                        indexes.add(source["table_index"])
    return indexes


def table_index_by_number(table_index: Any) -> dict[int, dict[str, Any]]:
    if not isinstance(table_index, list):
        return {}
    payload: dict[int, dict[str, Any]] = {}
    for row in table_index:
        if isinstance(row, dict) and isinstance(row.get("table_index"), int):
            payload[int(row["table_index"])] = row
    return payload


def table_signal(row: dict[str, Any]) -> str:
    parts = [
        row.get("heading"),
        row.get("unit"),
        row.get("source_caption"),
        row.get("source_footnote"),
        row.get("preview"),
    ]
    return compact_text(" ".join(json.dumps(part, ensure_ascii=False) if isinstance(part, (list, dict)) else str(part or "") for part in parts))


def has_formal_table_signal(statement_type: str, statements: list[dict[str, Any]], table_by_number: dict[int, dict[str, Any]]) -> bool:
    terms = FORMAL_TITLE_TERMS.get(statement_type) or ()
    for statement in statements:
        title = compact_text(statement.get("title") or statement.get("statement_name"))
        if any(term in title for term in terms):
            return True
    for index in source_table_indexes(statements):
        signal = table_signal(table_by_number.get(index) or {})
        if any(term in signal for term in terms):
            return True
    return False


def suspicious_titles(statement_type: str, statements: list[dict[str, Any]]) -> list[str]:
    titles: list[str] = []
    formal_terms = FORMAL_TITLE_TERMS.get(statement_type) or ()
    for statement in statements:
        title = str(statement.get("title") or statement.get("statement_name") or "").strip()
        normalized = compact_text(title)
        if not normalized:
            continue
        if any(term in normalized for term in SUSPICIOUS_TITLE_TERMS) and not any(term in normalized for term in formal_terms):
            titles.append(title)
    return titles


def core_group_missing(
    statement_type: str,
    statements: list[dict[str, Any]],
    core_canonical_groups: dict[str, tuple[set[str], ...]] | None = None,
) -> list[list[str]]:
    names = canonical_names(statements)
    missing: list[list[str]] = []
    for group in (core_canonical_groups or CORE_CANONICAL_GROUPS).get(statement_type) or ():
        if not names.intersection(group):
            missing.append(sorted(group))
    return missing


def statement_item_count(statements: list[dict[str, Any]]) -> int:
    return sum(len(statement_items(statement)) for statement in statements)


def percentile(values: list[float], pct: float) -> float | int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * pct)))
    value = ordered[index]
    if isinstance(value, float):
        return round(value, 3)
    return value


def build_wiki_payload(metadata: dict[str, Any], financial_data: dict[str, Any]) -> dict[str, Any]:
    market = market_from_metadata(metadata)
    ticker = first_present(metadata, IDENTITY_KEYS_BY_MARKET.get(market, ("ticker", "stock_code")))
    year = fiscal_year(metadata, financial_data)
    report_type = str(metadata.get("report_type") or financial_data.get("report_type") or "annual").strip() or "annual"
    report_id = "-".join(part for part in (str(year or ""), report_type, ticker or metadata.get("task_id") or "") if part)
    return {
        "market": market,
        "company_name": metadata.get("company_name") or metadata.get("company_slug"),
        "ticker": ticker,
        "company_wiki_id": metadata.get("company_wiki_id") or ticker,
        "company_path": metadata.get("company_path"),
        "report_id": report_id,
        "fiscal_year": year,
        "period_end": metadata.get("period_end") or financial_data.get("period_end"),
        "report_kind": metadata.get("report_kind") or financial_data.get("report_kind"),
        "source_file": metadata.get("filename") or metadata.get("source_file"),
    }


def audit_one(result_dir: Path) -> dict[str, Any]:
    metadata = read_json(result_dir / "metadata.json", {})
    artifact_manifest = read_json(result_dir / "artifact_manifest.json", {})
    document_full = read_json(result_dir / "document_full.json", {})
    enhanced = read_json(result_dir / "content_list_enhanced.json", {})
    table_index = read_json(result_dir / "table_index.json", [])
    table_relations = read_json(result_dir / "table_relations.json", {})
    financial_data = read_json(result_dir / "financial_data.json", {})
    financial_checks = read_json(result_dir / "financial_checks.json", {})
    quality_report = read_json(result_dir / "quality_report.json", {})

    market = market_from_metadata(metadata)
    profile = market_backtest_profile(market)
    identity_resolution = resolve_company_identity(metadata, financial_data, result_dir)
    metadata_for_payload = resolved_metadata(metadata, identity_resolution)
    strict_annual_core = not is_explicit_non_annual_report(metadata, financial_data)
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    def block(code: str, detail: Any = None) -> None:
        blockers.append({"code": code, "detail": detail})

    def warn(code: str, detail: Any = None) -> None:
        warnings.append({"code": code, "detail": detail})

    missing = list((artifact_manifest.get("core") or {}).get("missing") or [])
    required_missing = [
        name
        for name in manifests.REQUIRED_ARTIFACTS
        if not ((artifact_manifest.get("artifacts") or {}).get(name) or {}).get("exists")
    ]
    if (artifact_manifest.get("core") or {}).get("ready") is not True or missing or required_missing:
        block("contract_not_ready", sorted(set(missing + required_missing)))

    identity_key = str(identity_resolution.get("ticker") or "").strip()
    if not market or not identity_resolution.get("company_name") or not identity_key:
        block(
            "metadata_identity_incomplete",
            {
                "market": market,
                "company_name": metadata.get("company_name"),
                "identity": first_present(metadata, IDENTITY_KEYS_BY_MARKET.get(market, ("ticker", "stock_code"))),
                "resolved_company_name": identity_resolution.get("company_name"),
                "resolved_identity": identity_key,
                "identity_source": identity_resolution.get("source"),
            },
        )
    if not fiscal_year(metadata, financial_data) and not metadata.get("period_end"):
        block("report_period_missing", None)

    markdown_payload = document_full.get("markdown") if isinstance(document_full.get("markdown"), dict) else {}
    if document_full.get("schema_version") is None or not markdown_payload.get("content"):
        block("document_full_missing_embedded_markdown", None)
    if not (document_full.get("content_list_enhanced") or enhanced):
        block("content_list_enhanced_missing", None)

    table_count = int(enhanced.get("table_count") or len(enhanced.get("tables") or []) or 0) if isinstance(enhanced, dict) else 0
    table_index_count = len(table_index) if isinstance(table_index, list) else 0
    page_count = len(enhanced.get("pages") or []) if isinstance(enhanced, dict) else 0
    if table_count <= 0 or table_index_count <= 0 or page_count <= 0:
        block("enhanced_index_empty", {"tables": table_count, "table_index": table_index_count, "pages": page_count})
    if table_count and table_index_count and abs(table_count - table_index_count) > max(3, int(table_count * 0.03)):
        warn("table_count_mismatch", {"content_list_enhanced": table_count, "table_index": table_index_count})
    if table_relations.get("schema_version") != "document_table_relations_v1":
        block("table_relations_schema_missing", None)
    relation_candidate_count = int(table_relations.get("candidate_table_count") or table_relations.get("physical_table_count") or 0) if isinstance(table_relations, dict) else 0
    if table_count > 0 and relation_candidate_count <= 0:
        block("table_relation_candidates_empty", {"enhanced_tables": table_count})

    grouped = statement_by_type(financial_data)
    missing_statements = [name for name in CORE_STATEMENTS if not grouped.get(name)]
    if missing_statements:
        if strict_annual_core:
            block("core_statements_missing", missing_statements)
        else:
            warn("core_statements_missing_non_annual", missing_statements)
    table_by_number = table_index_by_number(table_index)
    statement_stats: dict[str, Any] = {}
    for statement_type in CORE_STATEMENTS:
        statements = grouped.get(statement_type) or []
        item_count = statement_item_count(statements)
        names = sorted(canonical_names(statements))
        missing_groups = core_group_missing(statement_type, statements, profile.get("core_canonical_groups"))
        ratio = evidence_ratio(statements)
        statement_source_counts = source_type_counts(statements)
        unclassified_count = statement_source_counts.get("parsed_financial_table", 0)
        unclassified_ratio = unclassified_count / item_count if item_count else 0.0
        statement_stats[statement_type] = {
            "statement_count": len(statements),
            "item_count": item_count,
            "canonical_count": len(names),
            "core_missing_groups": missing_groups,
            "evidence_ratio": round(ratio, 3),
            "source_table_count": len(source_table_indexes(statements)),
            "source_type_counts": statement_source_counts,
            "unclassified_source_item_count": unclassified_count,
            "unclassified_source_ratio": round(unclassified_ratio, 3),
            "formal_table_signal": has_formal_table_signal(statement_type, statements, table_by_number),
        }
        if statements and missing_groups:
            detail = {"statement_type": statement_type, "missing_groups": missing_groups}
            if strict_annual_core:
                block("core_statement_canonical_missing", detail)
            else:
                warn("core_statement_canonical_missing_non_annual", detail)
        if statements and ratio < 0.8:
            warn("statement_evidence_ratio_low", {"statement_type": statement_type, "ratio": round(ratio, 3)})
        if statements and not statement_stats[statement_type]["formal_table_signal"]:
            warn("formal_statement_signal_not_found", {"statement_type": statement_type})
        for title in suspicious_titles(statement_type, statements):
            warn("suspicious_statement_title", {"statement_type": statement_type, "title": title})
        limit = (profile.get("sprawl_item_limits") or {}).get(statement_type)
        if limit and item_count > limit:
            warn("statement_item_sprawl", {"statement_type": statement_type, "item_count": item_count, "limit": limit})
        unclassified_limit = profile.get("unclassified_source_ratio_limit")
        unclassified_min_items = int(profile.get("unclassified_source_min_items") or 0)
        if (
            unclassified_limit is not None
            and statements
            and item_count >= unclassified_min_items
            and unclassified_ratio > float(unclassified_limit)
        ):
            warn(
                "unclassified_statement_source_sprawl",
                {
                    "statement_type": statement_type,
                    "item_count": item_count,
                    "parsed_financial_table_items": unclassified_count,
                    "ratio": round(unclassified_ratio, 3),
                    "limit": unclassified_limit,
                },
            )

    checks_summary = financial_checks.get("summary") if isinstance(financial_checks, dict) else {}
    fail_count = int((checks_summary or {}).get("fail") or 0)
    if fail_count:
        block("financial_check_fail", {"fail_count": fail_count})
    elif financial_checks.get("overall_status") == "warning":
        warn("financial_check_warning", checks_summary)
    if quality_report.get("financial_overall_status") == "fail":
        block("quality_financial_fail", quality_report.get("financial_summary"))

    payload = build_wiki_payload(metadata_for_payload, financial_data)
    payload_missing = [key for key in ("market", "company_name", "ticker", "report_id", "fiscal_year", "source_file") if not payload.get(key)]
    if payload_missing:
        block("wiki_payload_minimum_missing", payload_missing)

    return {
        "task_id": result_dir.name,
        "market": market or "UNKNOWN",
        "market_profile": profile.get("profile_id"),
        "wiki_ready": not blockers,
        "blockers": blockers,
        "warnings": warnings,
        "warning_count": len(warnings),
        "result_dir": str(result_dir.relative_to(REPO_ROOT)) if result_dir.is_relative_to(REPO_ROOT) else str(result_dir),
        "metadata": {
            "company_name": metadata.get("company_name"),
            "ticker": identity_key,
            "fiscal_year": fiscal_year(metadata, financial_data),
            "period_end": metadata.get("period_end"),
            "report_kind": metadata.get("report_kind") or financial_data.get("report_kind"),
            "filename": metadata.get("filename") or metadata.get("source_file"),
            "strict_annual_core": strict_annual_core,
        },
        "identity_resolution": identity_resolution,
        "payload_preview": payload,
        "stats": {
            "result_complete_chars": text_file_chars(result_dir / "result_complete.md"),
            "document_full_chars": len((document_full.get("markdown") or {}).get("content") or ""),
            "enhanced_table_count": table_count,
            "table_index_count": table_index_count,
            "enhanced_page_count": page_count,
            "table_relation_count": len(table_relations.get("relations") or []) if isinstance(table_relations, dict) else 0,
            "table_relation_candidate_count": relation_candidate_count,
            "financial_check_fail_count": fail_count,
            "financial_overall_status": financial_checks.get("overall_status") if isinstance(financial_checks, dict) else None,
            "statement_stats": statement_stats,
        },
    }


def summarize(items: list[dict[str, Any]]) -> dict[str, Any]:
    by_market: dict[str, Counter] = defaultdict(Counter)
    blocker_counts: Counter[str] = Counter()
    warning_counts: Counter[str] = Counter()
    for item in items:
        market = item["market"]
        by_market[market]["total"] += 1
        by_market[market]["wiki_ready" if item["wiki_ready"] else "not_ready"] += 1
        by_market[market]["warnings"] += int(item.get("warning_count") or 0)
        for blocker in item.get("blockers") or []:
            code = str(blocker.get("code") or "unknown")
            blocker_counts[code] += 1
            by_market[market][f"blocker:{code}"] += 1
        for warning in item.get("warnings") or []:
            code = str(warning.get("code") or "unknown")
            warning_counts[code] += 1
            by_market[market][f"warning:{code}"] += 1
    return {
        "total": len(items),
        "wiki_ready": sum(1 for item in items if item["wiki_ready"]),
        "not_ready": sum(1 for item in items if not item["wiki_ready"]),
        "blocker_counts": dict(sorted(blocker_counts.items())),
        "warning_counts": dict(sorted(warning_counts.items())),
        "by_market": {market: dict(counter) for market, counter in sorted(by_market.items())},
        "market_profiles": {
            market: profile_for_report(market_backtest_profile(market))
            for market in sorted({item.get("market") or "UNKNOWN" for item in items})
        },
        "quality_profiles": quality_profiles(items),
    }


def quality_profiles(items: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, dict[str, dict[str, list[float]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )
    for item in items:
        market = item.get("market") or "UNKNOWN"
        statement_stats = ((item.get("stats") or {}).get("statement_stats") or {})
        for statement_type, stats in statement_stats.items():
            if not isinstance(stats, dict):
                continue
            for key in ("item_count", "source_table_count", "unclassified_source_ratio"):
                value = stats.get(key)
                if isinstance(value, int | float):
                    grouped[market][statement_type][key].append(value)
    profiles: dict[str, Any] = {}
    for market, statements in sorted(grouped.items()):
        profiles[market] = {}
        for statement_type, values_by_key in sorted(statements.items()):
            profiles[market][statement_type] = {
                key: {
                    "p50": percentile(values, 0.5),
                    "p90": percentile(values, 0.9),
                    "max": max(values) if values else None,
                }
                for key, values in values_by_key.items()
            }
    return profiles


def markdown_report(report: dict[str, Any], *, max_items: int = 80) -> str:
    lines = [
        "# PDF Parser Wiki-Ready Backtest",
        "",
        f"- Generated at: `{report['generated_at']}`",
        f"- Markets: `{', '.join(report['markets'])}`",
        f"- Total: `{report['summary']['total']}`",
        f"- Wiki ready: `{report['summary']['wiki_ready']}`",
        f"- Not ready: `{report['summary']['not_ready']}`",
        "",
        "## By Market",
        "",
        "| Market | Total | Wiki ready | Not ready | Warnings |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for market, row in (report["summary"].get("by_market") or {}).items():
        lines.append(
            f"| {market} | {row.get('total', 0)} | {row.get('wiki_ready', 0)} | {row.get('not_ready', 0)} | {row.get('warnings', 0)} |"
        )
    lines.extend(["", "## Blockers", "", "| Code | Count |", "| --- | ---: |"])
    for code, count in (report["summary"].get("blocker_counts") or {}).items():
        lines.append(f"| `{code}` | {count} |")
    if not report["summary"].get("blocker_counts"):
        lines.append("| _none_ | 0 |")
    lines.extend(["", "## Warnings", "", "| Code | Count |", "| --- | ---: |"])
    for code, count in (report["summary"].get("warning_counts") or {}).items():
        lines.append(f"| `{code}` | {count} |")
    if not report["summary"].get("warning_counts"):
        lines.append("| _none_ | 0 |")

    market_profiles = report["summary"].get("market_profiles") or {}
    if market_profiles:
        lines.extend(["", "## Market Profiles", "", "| Market | Profile | Sprawl limits | Unclassified ratio limit | Notes |", "| --- | --- | --- | ---: | --- |"])
        for market, profile in market_profiles.items():
            limits = ", ".join(f"{key}={value}" for key, value in (profile.get("sprawl_item_limits") or {}).items())
            notes = "<br>".join(profile.get("profile_notes") or [])
            lines.append(
                f"| {market} | `{profile.get('profile_id')}` | {limits} | {profile.get('unclassified_source_ratio_limit')} | {notes} |"
            )

    quality_profiles_payload = report["summary"].get("quality_profiles") or {}
    if quality_profiles_payload:
        lines.extend(
            [
                "",
                "## Statement Quality Profile",
                "",
                "| Market | Statement | Items p50/p90/max | Source tables p50/p90/max | Unclassified source ratio p50/p90/max |",
                "| --- | --- | ---: | ---: | ---: |",
            ]
        )
        for market, statements in quality_profiles_payload.items():
            for statement_type, stats in statements.items():
                item_stats = stats.get("item_count") or {}
                table_stats = stats.get("source_table_count") or {}
                unclassified_stats = stats.get("unclassified_source_ratio") or {}
                lines.append(
                    "| {market} | `{statement}` | {items} | {tables} | {unclassified} |".format(
                        market=market,
                        statement=statement_type,
                        items=f"{item_stats.get('p50')}/{item_stats.get('p90')}/{item_stats.get('max')}",
                        tables=f"{table_stats.get('p50')}/{table_stats.get('p90')}/{table_stats.get('max')}",
                        unclassified=f"{unclassified_stats.get('p50')}/{unclassified_stats.get('p90')}/{unclassified_stats.get('max')}",
                    )
                )

    not_ready = [item for item in report["items"] if not item["wiki_ready"]]
    if not_ready:
        lines.extend(["", "## Not Ready Items", "", "| Market | Task | Company | Blockers |", "| --- | --- | --- | --- |"])
        for item in not_ready[:max_items]:
            blockers = ", ".join(f"`{blocker.get('code')}`" for blocker in item.get("blockers") or [])
            lines.append(
                "| {market} | `{task}` | {company} | {blockers} |".format(
                    market=item["market"],
                    task=item["task_id"],
                    company=(item.get("identity_resolution") or {}).get("company_name") or item.get("metadata", {}).get("company_name") or "",
                    blockers=blockers,
                )
            )

    warned = [item for item in report["items"] if item.get("warnings")]
    if warned:
        lines.extend(["", "## Warning Samples", "", "| Market | Task | Company | Warning samples |", "| --- | --- | --- | --- |"])
        for item in warned[:max_items]:
            warning_text = []
            for warning in (item.get("warnings") or [])[:4]:
                warning_text.append(f"`{warning.get('code')}`: {json.dumps(warning.get('detail'), ensure_ascii=False)[:180]}")
            lines.append(
                "| {market} | `{task}` | {company} | {warnings} |".format(
                    market=item["market"],
                    task=item["task_id"],
                    company=(item.get("identity_resolution") or {}).get("company_name") or item.get("metadata", {}).get("company_name") or "",
                    warnings="<br>".join(warning_text),
                )
            )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", default=str(REPO_ROOT / "data/pdf-parser/results"))
    parser.add_argument("--markets", default="HK,EU,JP,KR", help="Comma-separated markets. Default: HK,EU,JP,KR.")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--json-output", default="")
    parser.add_argument("--markdown-output", default="")
    parser.add_argument("--include-ready-items", action="store_true", help="Keep all ready items in JSON output.")
    args = parser.parse_args(argv)

    market_filter = {item.strip().upper() for item in args.markets.split(",") if item.strip()}
    items: list[dict[str, Any]] = []
    for result_dir in iter_result_dirs(Path(args.results_dir)):
        metadata = read_json(result_dir / "metadata.json", {})
        market = market_from_metadata(metadata)
        if market_filter and market not in market_filter:
            continue
        items.append(audit_one(result_dir))
        if args.limit and len(items) >= args.limit:
            break
    report_items = items if args.include_ready_items else [item for item in items if not item["wiki_ready"] or item.get("warnings")]
    report = {
        "schema_version": "pdf_parser_wiki_ready_backtest_v4",
        "generated_at": now_iso(),
        "results_dir": str(Path(args.results_dir).resolve()),
        "markets": sorted(market_filter),
        "summary": summarize(items),
        "items": report_items,
    }
    if args.json_output:
        output = Path(args.json_output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.markdown_output:
        output = Path(args.markdown_output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(markdown_report(report), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0 if report["summary"]["not_ready"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
