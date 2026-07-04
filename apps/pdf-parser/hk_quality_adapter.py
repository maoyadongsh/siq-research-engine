from __future__ import annotations

import re
from typing import Any

HK_STATEMENT_LABELS = {
    "balance_sheet": "Statement of Financial Position",
    "income_statement": "Statement of Profit or Loss",
    "cash_flow_statement": "Statement of Cash Flows",
    "equity_statement": "Statement of Changes in Equity",
}

HK_KEY_METRIC_LABELS = {
    "occupancy_rate": "Occupancy Rate",
    "portfolio_valuation": "Portfolio Valuation",
    "net_property_income": "Net Property Income",
    "distribution_per_unit": "Distribution Per Unit",
    "contracted_sales": "Contracted Sales",
    "gross_floor_area": "Gross Floor Area",
    "loan_balance": "Loans and Advances",
    "deposits": "Customer Deposits",
    "net_interest_margin": "Net Interest Margin",
    "npl_ratio": "Non-performing Loan Ratio",
    "gross_written_premiums": "Gross Written Premiums",
    "combined_ratio": "Combined Ratio",
}


def merge_hk_quality_candidates(report: dict[str, Any], financial_data: dict[str, Any]) -> dict[str, Any]:
    merged = dict(report or {})
    merged["market"] = "HK"
    merged["accounting_standard"] = financial_data.get("accounting_standard") or merged.get("accounting_standard") or "HKFRS"
    merged["industry_profile"] = financial_data.get("industry_profile") or merged.get("industry_profile") or "general"
    table_lookup = {
        item.get("table_index"): item
        for item in merged.get("table_index") or []
        if isinstance(item, dict) and item.get("table_index") is not None
    }
    key_table_candidates: dict[str, list[dict[str, Any]]] = {}
    core_candidates: list[dict[str, Any]] = []
    found: list[str] = []
    by_type = {statement.get("statement_type"): statement for statement in financial_data.get("statements") or [] if isinstance(statement, dict)}
    for statement_type, label in HK_STATEMENT_LABELS.items():
        statement = by_type.get(statement_type)
        row = _candidate_from_statement(label, statement, table_lookup, financial_data) if statement else None
        if not row:
            row = _locate_hk_statement_candidate(label, table_lookup.values(), financial_data)
        if row:
            found.append(label)
            key_table_candidates[label] = [row]
            core_candidates.append(row)
        else:
            core_candidates.append({"name": label, "status": "missing", "candidate_group": "core"})
    hk_key_candidates: dict[str, list[dict[str, Any]]] = {}
    for metric in list(financial_data.get("key_metrics") or []) + list(financial_data.get("operating_metrics") or []):
        if not isinstance(metric, dict):
            continue
        label = HK_KEY_METRIC_LABELS.get(metric.get("canonical_name"))
        if not label:
            continue
        candidate = _candidate_from_metric(label, metric, table_lookup, financial_data)
        if candidate:
            hk_key_candidates.setdefault(label, []).append(candidate)
    key_table_candidates.update(hk_key_candidates)
    merged["key_table_candidates"] = key_table_candidates
    merged["hk_key_table_candidates"] = hk_key_candidates
    merged["indicator_table_candidates"] = [
        candidate
        for candidates in hk_key_candidates.values()
        for candidate in candidates
    ]
    merged["core_financial_table_candidates"] = core_candidates
    merged["found_financial_tables"] = found
    merged["report_kind"] = financial_data.get("report_kind") or merged.get("report_kind")
    return merged


def _candidate_from_statement(label: str, statement: dict[str, Any] | None, table_lookup: dict[Any, dict[str, Any]], financial_data: dict[str, Any]) -> dict[str, Any] | None:
    if not statement:
        return None
    indexes = statement.get("table_indexes") or []
    table_index = indexes[0] if indexes else None
    table = table_lookup.get(table_index) or {}
    line_numbers = statement.get("line_numbers") or []
    return _candidate(label, table_index, line_numbers[0] if line_numbers else table.get("line"), table, financial_data, statement.get("unit"))


def _candidate_from_metric(label: str, metric: dict[str, Any], table_lookup: dict[Any, dict[str, Any]], financial_data: dict[str, Any]) -> dict[str, Any] | None:
    evidence = metric.get("evidence") if isinstance(metric.get("evidence"), dict) else {}
    table_index = evidence.get("table_index")
    table = table_lookup.get(table_index) or {}
    return _candidate(label, table_index, evidence.get("line") or table.get("line"), table, financial_data, metric.get("unit"))


def _candidate(label: str, table_index: Any, line: Any, table: dict[str, Any], financial_data: dict[str, Any], unit: Any) -> dict[str, Any] | None:
    if not table_index and not line:
        return None
    return {
        "name": label,
        "status": "found",
        "table_index": table_index,
        "line": line,
        "pdf_page_number": table.get("pdf_page_number") or table.get("page_number"),
        "pdf_page_source": table.get("pdf_page_source"),
        "pdf_page_inference_reason": table.get("pdf_page_inference_reason"),
        "bbox": table.get("bbox") or [],
        "rows": table.get("rows"),
        "cells": table.get("cells"),
        "empty_ratio": table.get("empty_ratio"),
        "numeric_ratio": table.get("numeric_ratio"),
        "heading": table.get("heading") or table.get("title") or label,
        "unit": unit or table.get("unit") or "",
        "table_type": table.get("table_type") or "fact",
        "year_binding_required": True,
        "report_year": financial_data.get("report_year"),
        "candidate_group": "core" if label in HK_STATEMENT_LABELS.values() else "indicator",
        "candidate_score": 99.0,
        "confidence": "high",
        "preview": table.get("preview") or label,
        "is_primary": True,
        "_source": "financial_data",
    }


def _locate_hk_statement_candidate(label: str, tables: Any, financial_data: dict[str, Any]) -> dict[str, Any] | None:
    candidates: list[tuple[float, dict[str, Any]]] = []
    for table in tables:
        if not isinstance(table, dict):
            continue
        score = _hk_statement_score(label, table)
        if score <= 0:
            continue
        row = _candidate(label, table.get("table_index"), table.get("line"), table, financial_data, table.get("unit"))
        if not row:
            continue
        row["candidate_score"] = score
        row["confidence"] = "high" if score >= 85 else "medium"
        row["_source"] = "hk_table_locator"
        candidates.append((score, row))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-(item[0]), item[1].get("table_index") or 10**9))
    return candidates[0][1]


def _hk_statement_score(label: str, table: dict[str, Any]) -> float:
    signal = _table_signal(table)
    compact = _compact(signal)
    if not compact:
        return 0.0
    if label == "Statement of Financial Position":
        if any(term in compact for term in ("statementoffinancialposition", "consolidatedstatementoffinancialposition")):
            return 96.0
        hits = _hits(compact, ("totalassets", "totalliabilities", "netassets", "totalequity", "equityandliabilities"))
        if hits >= 3:
            return 88.0
        asset_section_hits = _hits(compact, ("noncurrentassets", "currentassets", "propertyplantandequipment", "rightofuseassets", "goodwill", "intangibleassets"))
        if asset_section_hits >= 3 and any(term in compact for term in ("atdecember31", "31december", "於12月31日")):
            return 86.0
        if any(term in compact for term in ("財務狀況表", "财务状况表", "資產負債表", "资产负债表")):
            return 92.0
    if label == "Statement of Profit or Loss":
        if _looks_like_cash_flow_table(compact) or _looks_like_non_statement_profit_table(compact):
            return 0.0
        if any(
            term in compact
            for term in (
                "statementofprofitorloss",
                "consolidatedstatementofprofitorloss",
                "statementofprofitorlossandothercomprehensiveincome",
            )
        ):
            return 96.0
        hits = _hits(
            compact,
            (
                "revenue",
                "turnover",
                "grossprofit",
                "profitbeforetax",
                "profitlossbeforetax",
                "profitfortheyear",
                "profitlossfortheyear",
                "rawmaterialsandconsumablesused",
                "directcosts",
                "costofsales",
                "operatingprofit",
                "administrativeexpenses",
                "sellingandmarketingexpenses",
                "otherincome",
                "othernetincome",
                "financecosts",
                "incometaxexpense",
                "incometax",
                "earningspershare",
                "收入",
                "收益",
                "營業額",
                "营业额",
                "毛利",
                "除稅前",
                "除税前",
                "年內溢利",
                "年内溢利",
            ),
        )
        if hits >= 3:
            return 88.0
        if (
            any(term in compact for term in ("netprofitfortheyear", "profitfortheyear", "profitbeforeincometax"))
            and any(term in compact for term in ("othercomprehensiveincome", "totalcomprehensiveincome", "comprehensiveincome"))
            and any(term in compact for term in ("fortheyearended", "截至", "yearended", "notes2025", "notes2024"))
        ):
            return 90.0
        if (
            any(term in compact for term in ("othercomprehensiveexpenseincomefortheyear", "othercomprehensiveincomeaftertaxnet", "itemsthatmaybereclassified"))
            and any(term in compact for term in ("fortheyearended", "截至", "yearended"))
        ):
            return 86.0
        if any(term in compact for term in ("損益表", "损益表", "利潤表", "利润表", "全面收益表")):
            return 90.0
    if label == "Statement of Cash Flows":
        if _looks_like_cash_flow_note(compact):
            return 0.0
        if any(term in compact for term in ("statementofcashflows", "consolidatedstatementofcashflows", "cashflowstatement")):
            return 96.0
        activity_hits = _hits(
            compact,
            (
                "cashflowsfromoperatingactivities",
                "cashflowsfrominvestingactivities",
                "cashflowsfromfinancingactivities",
                "netcashgeneratedfromoperatingactivities",
                "netcashusedinoperatingactivities",
                "cashgeneratedfromoperations",
                "operatingactivities",
                "經營活動",
                "经营活动",
            ),
        )
        if activity_hits >= 1 and not _looks_like_cash_flow_note(compact):
            if "cashflowsfromoperatingactivities" in compact or "netcash" in compact:
                return 90.0
            if any(term in compact for term in ("profitbeforetax", "除稅前", "除税前", "adjustmentsfor", "就以下各項作出調整")):
                return 88.0
            return 84.0
        if any(term in compact for term in ("現金流量表", "现金流量表", "經營活動所得現金", "经营活动所得现金")):
            return 90.0
    if label == "Statement of Changes in Equity":
        if _looks_like_financial_position_table(compact):
            return 0.0
        if any(
            term in compact
            for term in (
                "statementofchangesinequity",
                "consolidatedstatementofchangesinequity",
                "changesinequity",
                "變動表",
                "变动表",
            )
        ):
            return 96.0
        if _looks_like_equity_note(compact):
            return 0.0
        column_hits = _hits(
            compact,
            (
                "sharecapital",
                "sharepremium",
                "treasuryshares",
                "capitalreserve",
                "fairvaluereserve",
                "issuedcapital",
                "retainedprofits",
                "retainedearnings",
                "revenuereserve",
                "exchangereserve",
                "cashflowhedgingreserve",
                "earningsretainedforreserveadjustments",
                "reserves",
                "totalequity",
                "unitholdersequity",
                "proposeddeclareddividend",
                "noncontrollinginterests",
                "attributabletoownersoftheparent",
                "attributabletoownersofthecompany",
                "equityattributabletoownersofthecompany",
                "attributabletoequityholdersoftheparentcompany",
                "attributabletoshareholdersofthecompany",
                "attributabletocompanysshareholders",
                "本公司擁有人應佔",
                "本公司拥有人应占",
                "歸屬於母公司股東權益",
                "归属于母公司股东权益",
                "股本",
                "儲備",
                "储备",
            ),
        )
        movement_hits = _hits(
            compact,
            (
                "at1january",
                "at1april",
                "at1july",
                "at31december",
                "balanceat1january",
                "changesinequityfor",
                "profitfortheyear",
                "totalcomprehensiveincome",
                "othercomprehensiveincome",
                "dividends",
                "transactionswithowners",
            ),
        )
        if column_hits >= 3 and movement_hits >= 2:
            return 90.0
        if column_hits >= 4 and any(
            term in compact
            for term in (
                "attributabletoownersoftheparent",
                "attributabletoownersofthecompany",
                "equityattributabletoownersofthecompany",
                "attributabletoequityholdersoftheparentcompany",
                "attributabletoshareholdersofthecompany",
                "attributabletocompanysshareholders",
                "本公司擁有人應佔",
                "本公司拥有人应占",
                "歸屬於母公司股東權益",
                "归属于母公司股东权益",
                "unitholdersequity",
            )
        ):
            return 88.0
        if column_hits >= 3 and any(term in compact for term in ("movementinthecompanysreserves", "movementsinthecompanysreserves", "unitholdersequity")):
            return 88.0
        if any(term in compact for term in ("權益變動", "权益变动", "股本", "儲備", "储备")) and movement_hits >= 1:
            return 86.0
    return 0.0


def _table_signal(table: dict[str, Any]) -> str:
    parts = [
        table.get("heading"),
        table.get("title"),
        table.get("source_caption"),
        table.get("caption"),
        table.get("preview"),
        table.get("signal_preview"),
        table.get("text_preview"),
        table.get("near_text"),
        table.get("source_footnote"),
        table.get("footnote"),
    ]
    return " ".join(str(part or "") for part in parts)


def _compact(value: Any) -> str:
    text = str(value or "").replace("&amp;", "&").replace("&#x27;", "'").replace("’", "'")
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", text).lower()


def _hits(compact: str, terms: tuple[str, ...]) -> int:
    return sum(1 for term in terms if _compact(term) in compact)


def _looks_like_cash_flow_note(compact: str) -> bool:
    return any(
        term in compact
        for term in (
            "changesinliabilitiesarisingfromfinancingactivities",
            "notestotheconsolidatedstatementofcashflows",
            "notestostatementofcashflows",
            "supplementaryinformationtothecashflowstatement",
            "explanationsonpresentationofcashflows",
            "totalcashoutflowforleases",
            "netoutflowofcashandequivalentsincludedincashflow",
        )
    )


def _looks_like_financial_position_table(compact: str) -> bool:
    if "statementoffinancialposition" in compact or "balance sheets" in compact or "balancesheets" in compact:
        return True
    return (
        any(term in compact for term in ("noncurrentassets", "currentassets", "totalassets", "assets"))
        and any(term in compact for term in ("noncurrentliabilities", "currentliabilities", "totalliabilities", "liabilities"))
        and any(term in compact for term in ("equityattributable", "totalequity", "netassets"))
    )


def _looks_like_cash_flow_table(compact: str) -> bool:
    return any(
        term in compact
        for term in (
            "cashflowsfromoperatingactivities",
            "cashflowsfrominvestingactivities",
            "cashflowsfromfinancingactivities",
            "netcashgeneratedfromoperatingactivities",
            "netcashusedinoperatingactivities",
            "operatingactivities經營活動",
            "operatingactivities经营活动",
        )
    )


def _looks_like_non_statement_profit_table(compact: str) -> bool:
    return any(
        term in compact
        for term in (
            "fiveyear",
            "5year",
            "financialhighlights",
            "keyfinancial",
            "coreoperatingprofit",
            "nonifrs",
            "adjustednetprofit",
            "adjustedprofit",
            "geographical",
            "segment",
            "分拆客戶合約收入",
            "客户合约收入",
            "核心經營利潤",
            "核心经营利润",
            "非國際財務報告準則",
            "非国际财务报告准则",
        )
    )


def _looks_like_equity_note(compact: str) -> bool:
    return any(
        term in compact
        for term in (
            "percentageofequity",
            "equityinterestsheld",
            "fairvaluehierarchy",
            "fairvalueusingquotedprices",
            "foreigncurrencyrisk",
            "sensitivityanalysis",
            "name of subsidiaries",
            "nameofsubsidiaries",
            "placeofincorporation",
            "proportionownershipinterest",
            "principalactivities",
        )
    )
