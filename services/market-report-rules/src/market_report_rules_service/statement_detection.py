from __future__ import annotations

import re
from typing import Any

from .models import ParsedTable, StatementType
from .normalization import compact_label, normalize_label


STATEMENT_TITLE_ALIASES: dict[StatementType, tuple[str, ...]] = {
    StatementType.BALANCE_SHEET: (
        "consolidated statement of financial position",
        "consolidated statements of financial position",
        "statement of financial position",
        "statements of financial position",
        "consolidated balance sheet",
        "consolidated balance sheets",
        "balance sheet",
        "balance sheets",
        "assets and liabilities",
        "资产负债表",
        "資產負債表",
        "财务状况表",
        "財務狀況表",
        "综合财务状况表",
        "綜合財務狀況表",
        "連結財政状態計算書",
        "連結貸借対照表",
        "貸借対照表",
        "재무상태표",
        "연결재무상태표",
    ),
    StatementType.INCOME_STATEMENT: (
        "consolidated statement of profit or loss",
        "consolidated statements of profit or loss",
        "statement of profit or loss",
        "consolidated income statement",
        "consolidated income statements",
        "consolidated statement of operations",
        "consolidated statements of operations",
        "consolidated statement of income",
        "consolidated statements of income",
        "income statement",
        "statement of operations",
        "statements of operations",
        "statement of income",
        "statements of income",
        "statement of comprehensive income",
        "statements of comprehensive income",
        "consolidated statements of comprehensive income",
        "statement of profit or loss and other comprehensive income",
        "profit or loss and other comprehensive income",
        "损益表",
        "損益表",
        "利润表",
        "利潤表",
        "综合收益表",
        "綜合收益表",
        "全面收益表",
        "综合损益及其他全面收益表",
        "綜合損益及其他全面收益表",
        "連結損益計算書",
        "連結包括利益計算書",
        "損益計算書",
        "손익계산서",
        "포괄손익계산서",
        "연결손익계산서",
    ),
    StatementType.CASH_FLOW_STATEMENT: (
        "consolidated statement of cash flows",
        "consolidated statements of cash flows",
        "statement of cash flows",
        "statements of cash flows",
        "cash flow statement",
        "cash flow statements",
        "cash flows",
        "现金流量表",
        "現金流量表",
        "综合现金流量表",
        "綜合現金流量表",
        "連結キャッシュフロー計算書",
        "連結キャッシュ・フロー計算書",
        "キャッシュフロー計算書",
        "キャッシュ・フロー計算書",
        "현금흐름표",
        "연결현금흐름표",
        "현금흐를표",
        "연결한금흐를표",
        "현금초를",
        "현금조율",
    ),
}


STATEMENT_ROW_SIGNALS: dict[StatementType, tuple[str, ...]] = {
    StatementType.BALANCE_SHEET: (
        "total assets",
        "total liabilities",
        "total equity",
        "total shareholders' equity",
        "total shareholders’ equity",
        "shareholders' equity",
        "shareholders’ equity",
        "total liabilities and shareholders' equity",
        "total liabilities and shareholders’ equity",
        "current assets",
        "non-current assets",
        "资产总额",
        "資產總額",
        "负债总额",
        "負債總額",
        "权益总额",
        "權益總額",
        "資産合計",
        "負債合計",
        "純資産合計",
        "자산총계",
        "부채총계",
        "자본총계",
    ),
    StatementType.INCOME_STATEMENT: (
        "revenue",
        "gross profit",
        "profit before tax",
        "profit for the year",
        "profit for the period",
        "loss for the year",
        "loss for the period",
        "(loss)/profit for the year",
        "net income",
        "net loss",
        "net sales",
        "total net sales",
        "income before income taxes",
        "income before income tax",
        "earnings per share",
        "收益",
        "收入",
        "毛利",
        "除税前利润",
        "除稅前利潤",
        "年度利润",
        "年度利潤",
        "每股盈利",
        "売上収益",
        "営業利益",
        "当期利益",
        "매출액",
        "영업이익",
        "당기순이익",
    ),
    StatementType.CASH_FLOW_STATEMENT: (
        "net cash generated from operating activities",
        "cash generated from operations",
        "net cash provided by operating activities",
        "net cash provided by (used in) operating activities",
        "net cash provided by used in operating activities",
        "net cash provided by/(used in) operating activities",
        "net cash used in operating activities",
        "net cash flows (used in)/generated from operating activities",
        "cash flows from operating activities",
        "net cash provided by (used in) investing activities",
        "net cash used in investing activities",
        "net cash provided by (used in) financing activities",
        "net cash used in financing activities",
        "cash and cash equivalents at end",
        "经营活动所得现金净额",
        "經營活動所得現金淨額",
        "投资活动现金流量",
        "投資活動現金流量",
        "融资活动现金流量",
        "融資活動現金流量",
        "期末现金及现金等价物",
        "期末現金及現金等價物",
        "営業活動によるキャッシュフロー",
        "営業活動によるキャッシュ・フロー",
        "投資活動によるキャッシュフロー",
        "投資活動によるキャッシュ・フロー",
        "財務活動によるキャッシュフロー",
        "財務活動によるキャッシュ・フロー",
        "영업활동현금흐름",
        "영업활동으로부터의 현금흐름",
        "영업활동현금초를",
        "영업활동으로대한현금초를",
        "투자활동현금흐름",
        "투자활동으로부터의 현금흐름",
        "투자활동현금초를",
        "재무활동현금흐름",
        "재무활동으로부터의 현금흐름",
        "재무활동으로대한현금초를",
        "제부활동으로대한현금조율",
        "기말현금및현금성자산",
        "기말현금",
    ),
}


def detect_statement_type_from_title(title: str | None) -> StatementType | None:
    normalized = normalize_label(title)
    compact = compact_label(title)
    if not normalized:
        return None
    scores = {
        statement_type: sum(1 for alias in aliases if normalize_label(alias) in normalized or compact_label(alias) in compact)
        for statement_type, aliases in STATEMENT_TITLE_ALIASES.items()
    }
    return _best_score(scores)


def detect_statement_type_from_rows(rows: list[list[Any]]) -> StatementType | None:
    joined = "\n".join(" ".join(str(cell or "") for cell in row[:2]) for row in rows[:80])
    compact = compact_label(joined)
    scores = {
        statement_type: sum(1 for signal in signals if compact_label(signal) in compact)
        for statement_type, signals in STATEMENT_ROW_SIGNALS.items()
    }
    return _best_score(scores, minimum=2)


def detect_table_statement_type(table: ParsedTable) -> StatementType | None:
    raw_type = None
    raw = table.raw if isinstance(table.raw, dict) else {}
    if isinstance(table.raw, dict):
        raw_type = table.raw.get("statement_type") or table.raw.get("financial_statement_type")
    if raw_type:
        try:
            return StatementType(str(raw_type))
        except ValueError:
            pass
    title_candidates = [table.title, raw.get("heading"), raw.get("title")]
    captions = raw.get("source_caption")
    if isinstance(captions, list):
        title_candidates.append(" ".join(str(item or "") for item in captions))
    elif captions:
        title_candidates.append(str(captions))
    title_detected = False
    for title in title_candidates:
        detected = detect_statement_type_from_title(title)
        if detected:
            title_detected = True
            return detected
    if any(_looks_like_summary_title(title) for title in title_candidates):
        return None
    detected_from_rows = detect_statement_type_from_rows(table.rows)
    if detected_from_rows and not title_detected and _looks_like_multi_year_summary_table(table):
        return None
    return detected_from_rows


def _looks_like_multi_year_summary_table(table: ParsedTable) -> bool:
    max_width = max((len(row) for row in table.rows[:8]), default=0)
    if max_width < 5:
        return False
    head = " ".join(" ".join(str(cell or "") for cell in row[:8]) for row in table.rows[:4])
    compact = compact_label(head)
    year_hits = len(set(re.findall(r"(?:20\d{2}|fy\d{2,4})", head, flags=re.I)))
    fiscal_label = any(token in compact for token in ("事業年度", "年度", "fiscalyear", "yearsended"))
    return fiscal_label and year_hits >= 4


def _looks_like_summary_title(title: Any) -> bool:
    normalized = normalize_label(title)
    if not normalized:
        return False
    return any(
        token in normalized
        for token in (
            "financial summary",
            "financial highlights",
            "selected financial data",
            "summary financial information",
            "key figures",
            "key consolidated financial data",
            "요약재무정보",
            "主要な経営指標",
            "連結経営指標",
        )
    )


def _best_score(scores: dict[StatementType, int], minimum: int = 1) -> StatementType | None:
    best_type = None
    best_score = minimum - 1
    tie = False
    for statement_type, score in scores.items():
        if score > best_score:
            best_type = statement_type
            best_score = score
            tie = False
        elif score == best_score and score >= minimum:
            tie = True
    if tie or best_score < minimum:
        return None
    return best_type
