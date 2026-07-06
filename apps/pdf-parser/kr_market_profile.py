# Korea market profile helpers for PDF parser quality and financial views.

from __future__ import annotations

import re
from typing import Any, Iterable

KR_PROFILE_RULE_VERSION = "kr-pdf-profile-v4"

KR_KEY_SECTIONS = [
    "회사의 개요",
    "사업의 내용",
    "이사의 경영진단 및 분석의견",
    "재무에 관한 사항",
    "감사인의 감사의견",
    "임원 및 직원 등에 관한 사항",
    "계열회사 등에 관한 사항",
]

KR_CORE_FINANCIAL_TABLE_NAMES = [
    "요약재무정보",
    "Consolidated Statement of Financial Position",
    "Consolidated Statement of Profit or Loss",
    "Consolidated Statement of Comprehensive Income",
    "Consolidated Statement of Cash Flows",
    "Consolidated Statement of Changes in Equity",
]

KR_INDICATOR_TABLE_NAMES = [
    "Segment Information",
    "Revenue",
    "Operating Profit",
    "Net Income",
    "Total Assets",
    "Basic EPS",
]

KR_KEY_TABLE_DISPLAY_ORDER = KR_CORE_FINANCIAL_TABLE_NAMES + KR_INDICATOR_TABLE_NAMES

_BUSINESS_REPORT_TERMS = (
    "사업보고서",
    "annual report",
    "business report",
    "재무에 관한 사항",
    "연결재무제표",
    "연결 재무제표",
    "개별재무제표",
)

_CANDIDATE_RULES: dict[str, tuple[tuple[str, ...], str]] = {
    "요약재무정보": (
        (
            "요약재무정보",
            "요약 재무정보",
            "요약 연결재무정보",
            "주요재무정보",
            "주요 재무정보",
            "summary financial information",
            "selected financial data",
            "financial highlights",
            "매출액 영업이익 당기순이익 자산총계",
        ),
        "core",
    ),
    "Consolidated Statement of Financial Position": (
        (
            "연결 재무상태표",
            "연결재무상태표",
            "consolidated statement of financial position",
            "consolidated balance sheet",
            "statement of financial position",
        ),
        "core",
    ),
    "Consolidated Statement of Profit or Loss": (
        (
            "연결 손익계산서",
            "연결손익계산서",
            "consolidated statement of profit or loss",
            "consolidated income statement",
        ),
        "core",
    ),
    "Consolidated Statement of Comprehensive Income": (
        (
            "연결 포괄손익계산서",
            "연결포괄손익계산서",
            "consolidated statement of comprehensive income",
            "comprehensive income",
            "총포괄손익",
        ),
        "core",
    ),
    "Consolidated Statement of Cash Flows": (
        (
            "연결 현금흐름표",
            "연결현금흐름표",
            "연결 한금흐를표",
            "연결한금흐를표",
            "현금흐를표",
            "현금초를",
            "현금조율",
            "consolidated statement of cash flows",
            "statement of cash flows",
            "영업활동 현금흐름",
            "영업활동 현금초를",
            "재무활동으로 대한 현금초를",
            "제부활동으로 대한 현금조율",
            "기말현금",
        ),
        "core",
    ),
    "Consolidated Statement of Changes in Equity": (
        (
            "연결 자본변동표",
            "연결자본변동표",
            "consolidated statement of changes in equity",
            "statement of changes in equity",
            "자본금 이익잉여금 자본총계",
        ),
        "core",
    ),
    "Segment Information": (("영업부문", "부문정보", "segment information", "operating segment"), "indicator"),
    "Revenue": (("매출액", "수익", "revenue", "sales"), "indicator"),
    "Operating Profit": (("영업이익", "operating profit", "operating income"), "indicator"),
    "Net Income": (("당기순이익", "net income", "profit for the year"), "indicator"),
    "Total Assets": (("자산총계", "총자산", "total assets"), "indicator"),
    "Basic EPS": (("기본주당이익", "주당이익", "basic earnings per share", "basic eps"), "indicator"),
}


def _normalize_text(value: Any) -> str:
    text = str(value or "").replace("\xa0", " ").replace("\u3000", " ")
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def _compact_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").lower())


def is_kr_market(task: dict[str, Any] | None, filename: str | None = None) -> bool:
    task = task or {}
    submit_config = task.get("submit_config") if isinstance(task.get("submit_config"), dict) else {}
    explicit = submit_config.get("market") or task.get("market")
    if explicit:
        return str(explicit).strip().upper() == "KR"
    name = str(filename or task.get("filename") or "")
    lowered = name.lower()
    return bool(re.search(r"(?:^|[_\-])kr(?:[_\-]|$)", lowered) or "dart_public" in lowered)


def detect_kr_report_kind(text: str, filename: str | None = None) -> str:
    source = _normalize_text("\n".join([str(filename or ""), str(text or "")[:120000]]))
    compact = _compact_text(source)
    if any(_normalize_text(term) in source or _compact_text(term) in compact for term in _BUSINESS_REPORT_TERMS):
        return "kr_business_report"
    return "kr_pdf_report"


def kr_candidate_group(name: str) -> str:
    if name in KR_CORE_FINANCIAL_TABLE_NAMES:
        return "core"
    return "indicator"


def _table_signal_text(item: dict[str, Any]) -> str:
    parts = [
        item.get("heading"),
        item.get("source_caption"),
        item.get("source_footnote"),
        item.get("caption"),
        item.get("footnote"),
        item.get("preview"),
        item.get("text_preview"),
        item.get("near_text"),
        item.get("unit"),
    ]
    return _normalize_text(" ".join(str(part or "") for part in parts))


def _score_candidate(signal: str, terms: Iterable[str]) -> float:
    score = 0.0
    compact_signal = _compact_text(signal)
    for term in terms:
        normalized = _normalize_text(term)
        compact = _compact_text(term)
        if normalized and normalized in signal:
            score = max(score, 80.0 + min(len(normalized), 30))
        elif compact and compact in compact_signal:
            score = max(score, 75.0 + min(len(compact), 25))
    return score


def _fallback_rule_score(name: str, signal: str) -> float:
    compact = _compact_text(signal)
    tokens = set(re.findall(r"[a-z]+", signal))
    if _looks_like_kr_contents_table(signal) and name in {"요약재무정보", *_CORE_STATEMENT_NAMES}:
        return 0.0
    if _looks_like_kr_subsidiary_summary_table(signal) and name in {"요약재무정보", *_CORE_STATEMENT_NAMES}:
        return 0.0
    if _looks_like_kr_interest_or_funding_table(signal) and name in _CORE_STATEMENT_NAMES:
        return 0.0
    if _looks_like_kr_capital_composition_table(signal) and name in _CORE_STATEMENT_NAMES:
        return 0.0
    summary_or_segment_context = any(
        marker in compact
        for marker in ("요약재무정보", "영업부문", "부문정보", "사업부문", "segmentinformation", "operatingsegment")
    )
    if summary_or_segment_context and name in {
        "Consolidated Statement of Financial Position",
        "Consolidated Statement of Profit or Loss",
        "Consolidated Statement of Comprehensive Income",
        "Consolidated Statement of Cash Flows",
        "Consolidated Statement of Changes in Equity",
    }:
        return 0.0
    if name == "요약재무정보":
        metric_hits = sum(1 for term in ("매출액", "영업이익", "당기순이익", "자산총계", "부채총계", "자본총계") if term in compact)
        if _has_strong_summary_body(signal):
            return 92.0
        if ("요약" in compact or "summaryfinancialinformation" in compact or "selectedfinancialdata" in compact) and metric_hits >= 3:
            return 90.0
        if metric_hits >= 5:
            return 86.0
    if name == "Consolidated Statement of Financial Position":
        if all(term in compact for term in ("자산총계", "부채총계", "자본총계")):
            return 88.0
        if all(term in compact for term in ("자산층계", "부채계")) and ("자본총계" in compact or "자본계" in compact):
            return 84.0
        if "연결재무제표" in compact and "자산" in compact and ("유동자산" in compact or "비유동자산" in compact):
            return 86.0
        if {"assets", "liabilities"}.issubset(tokens) and ("equity" in tokens or "capital" in tokens):
            return 86.0
    if name == "Consolidated Statement of Profit or Loss":
        if "사업부문" in compact or "영업부문" in compact or "부문정보" in compact:
            return 0.0
        if "연결재무제표" in compact and "매출액" in compact and any(
            term in compact for term in ("매출원가", "매출총이익", "판매비와관리비")
        ):
            return 86.0
        if "매출액" in compact and ("영업이익" in compact or "당기순이익" in compact):
            return 86.0
    if name == "Consolidated Statement of Comprehensive Income":
        if (
            _looks_like_kr_equity_statement_body(signal)
            or _looks_like_kr_retained_earnings_appropriation(signal)
            or _looks_like_kr_compact_investee_income_summary(signal)
        ):
            return 0.0
        if (
            "총포괄손익" in compact
            or "총포괄이익" in compact
            or (("기타포괄손익" in compact or "기타포괄이익" in compact) and "당기순이익" in compact)
        ):
            return 86.0
    if name == "Consolidated Statement of Cash Flows":
        if "영업활동" in compact and "현금흐름" in compact:
            return 86.0
        if "현금" in compact and any(term in compact for term in ("흐를", "초를", "조율")):
            return 84.0
        if ("재무활동" in compact or "제부활동" in compact) and "현금" in compact:
            return 82.0
    if name == "Consolidated Statement of Changes in Equity":
        if _looks_like_kr_retained_earnings_appropriation(signal):
            return 0.0
        if _looks_like_kr_interest_or_funding_table(signal):
            return 0.0
        if _looks_like_kr_capital_composition_table(signal):
            return 0.0
        if "자본금" in compact and ("이익잉여금" in compact or "자본총계" in compact):
            return 84.0
    return 0.0


_CORE_STATEMENT_NAMES = {
    "Consolidated Statement of Financial Position",
    "Consolidated Statement of Profit or Loss",
    "Consolidated Statement of Comprehensive Income",
    "Consolidated Statement of Cash Flows",
    "Consolidated Statement of Changes in Equity",
}


def _context_adjusted_score(name: str, signal: str, score: float) -> float:
    if score <= 0:
        return 0.0
    if name in {"요약재무정보", *_CORE_STATEMENT_NAMES} and _looks_like_kr_contents_table(signal):
        return 0.0
    if name in {"요약재무정보", *_CORE_STATEMENT_NAMES} and _looks_like_kr_subsidiary_summary_table(signal):
        return 0.0
    if name in _CORE_STATEMENT_NAMES and _looks_like_kr_interest_or_funding_table(signal):
        return 0.0
    if name in _CORE_STATEMENT_NAMES and _looks_like_kr_capital_composition_table(signal):
        return 0.0
    if name == "Consolidated Statement of Comprehensive Income" and (
        _looks_like_kr_equity_statement_body(signal)
        or _looks_like_kr_retained_earnings_appropriation(signal)
        or _looks_like_kr_compact_investee_income_summary(signal)
    ):
        return 0.0
    if name not in _CORE_STATEMENT_NAMES:
        return score
    compact = _compact_text(signal)
    if any(
        marker in compact
        for marker in (
            "별도재무제표",
            "법인또는단체의명칭",
            "단일사업부문",
            "사업부문별",
            "사업부문",
            "주요제품",
            "회사명자산총액부채총액",
        )
    ):
        score -= 45.0
    if "연결재무제표" in compact or "연결재무제표입니다" in compact or "연결재무제표주석" in compact:
        score += 18.0
    return max(score, 0.0)


def _looks_like_kr_contents_table(signal: str) -> bool:
    compact = _compact_text(signal)
    core_hits = sum(
        1
        for marker in (
            "연결재무상태표",
            "연결손익계산서",
            "연결포괄손익계산서",
            "연결자본변동표",
            "연결현금흐름표",
            "연결한금흐를표",
            "현금흐를표",
            "재무제표에대한주석",
        )
        if marker in compact
    )
    if core_hits < 4:
        return False
    if any(marker in compact for marker in ("유동자산", "비유동자산", "매출액", "영업이익", "영업활동", "투자활동", "재무활동", "자본금")):
        return False
    return True


def _looks_like_kr_subsidiary_summary_table(signal: str) -> bool:
    compact = _compact_text(signal)
    if any(
        marker in compact
        for marker in (
            "종속기업명",
            "종속기업의요약재무정보",
            "종속기업요약재무정보",
            "관계기업의요약재무정보",
            "관계기업의조정된요약재무정보",
            "공동기업의요약재무정보",
            "공동기업의조정된요약재무정보",
            "피투자기업의요약재무정보",
            "조정된요약재무정보",
            "관계기업및공동기업",
            "요약포괄손익계산서",
            "최대주주",
            "법인또는단체의명칭",
            "각종속기업",
            "보장매수자",
            "주요종속회사",
            "상호설립일주소주요사업",
            "정정사항",
            "정정전정정후",
            "합병전합병후",
            "존속회사",
            "소멸회사",
            "합병등종료보고서",
            "구분자산부채자본영업수익당기순손익총포괄손익",
            "구분자산부채자본매출액당기순손익총포괄손익",
            "구분매출계속영업손익중단영업손익기타포괄손익총포괄손익",
            "감가상각비무형자산상각비이자수익",
            "회사명당기",
            "회사명전기",
            "회사명자산부채",
            "구분자산부채자본당기순이익총포괄손익변경전",
        )
    ):
        return True
    if "요약재무상태표" in compact and ("당기" in compact or "전기" in compact) and not re.search(r"(?:제\d+기|20\d{2})", compact):
        return True
    if "연결에포함된회사수" in compact and not any(
        marker in compact for marker in ("[자산]", "유동자산", "자산총계", "매출액", "영업이익", "당기순이익")
    ):
        return True
    has_entity_axis = any(marker in compact for marker in ("회사명", "법인명", "기업명", "종속기업", "관계기업", "공동기업"))
    has_summary_columns = all(marker in compact for marker in ("자산", "부채", "자본")) and any(
        marker in compact for marker in ("매출액", "영업수익", "총수익", "당기순손익", "총포괄손익", "총포괄이익")
    )
    return has_entity_axis and has_summary_columns


def _looks_like_kr_interest_or_funding_table(signal: str) -> bool:
    compact = _compact_text(signal)
    return any(marker in compact for marker in ("평균잔액", "이자율", "조달항목", "이자수익", "이자비용")) and any(
        marker in compact for marker in ("비중", "소계", "자본잉여금", "이익잉여금")
    )


def _looks_like_kr_capital_composition_table(signal: str) -> bool:
    compact = _compact_text(signal)
    return any(marker in compact for marker in ("기말잔액구성비", "기말잔액비중", "자기자본")) and any(
        marker in compact for marker in ("자본금", "이익잉여금", "기타포괄손익누계액", "기타자본구성요소")
    )


def _looks_like_kr_compact_investee_income_summary(signal: str) -> bool:
    compact = _compact_text(signal)
    if any(
        marker in compact
        for marker in (
            "구분2025년말2024년말2023년말영업수익",
            "구분당기매출액당기순손익총포괄손익",
            "구분매출계속영업손익중단영업손익기타포괄손익총포괄손익",
            "회사명자산부채자본총수익당기순이익",
            "회사명당기영업수익당기순손익",
            "회사명전기영업수익당기순손익",
            "회사명자산부채매출",
            "구분자산부채자본당기순이익총포괄손익변경전",
        )
    ):
        return True
    if "증감률" in compact and ("매출액" in compact or "영업수익" in compact) and "영업이익" in compact:
        return True
    return False


def _has_strong_summary_body(signal: str) -> bool:
    compact = _compact_text(signal)
    if _looks_like_kr_subsidiary_summary_table(signal):
        return False
    has_summary_title = any(
        marker in compact
        for marker in (
            "요약재무정보",
            "요약연결재무정보",
            "연결요약재무정보",
            "요약재무상태표",
            "summaryfinancialinformation",
            "selectedfinancialdata",
            "financialhighlights",
        )
    )
    if not has_summary_title:
        return False
    if any(
        marker in compact
        for marker in ("상기요약연결재무정보", "요약연결재무정보", "연결요약재무정보", "요약재무정보별도재무제표기준")
    ) and any(
        marker in compact for marker in ("[자산]", "유동자산", "자산총계")
    ):
        return True
    if "요약재무정보" in compact and "별도재무제표기준" in compact and any(
        marker in compact for marker in ("[자산]", "유동자산", "자산총계")
    ):
        return True
    if "한국채택국제회계기준" in compact and any(marker in compact for marker in ("[유동자산]", "유동자산", "자산총계")):
        return True
    balance_blocks = (
        any(marker in compact for marker in ("[자산]", "자산총계", "유동자산"))
        and any(marker in compact for marker in ("[부채]", "부채총계", "유동부채"))
        and any(marker in compact for marker in ("[자본]", "자본총계", "자본금"))
    )
    income_blocks = any(marker in compact for marker in ("매출액", "영업수익", "영업이익", "당기순이익"))
    return balance_blocks and income_blocks


def _looks_like_kr_equity_statement_body(signal: str) -> bool:
    compact = _compact_text(signal)
    component_hits = sum(
        1
        for term in (
            "자본금",
            "납입자본",
            "주식발행초과금",
            "자본잉여금",
            "신종자본증권",
            "기타자본",
            "기타자본구성요소",
            "기타포괄손익누계액",
            "이익잉여금",
            "비지배지분",
            "자본합계",
        )
        if term in compact
    )
    movement_hits = sum(
        1
        for term in (
            "기초자본",
            "기초잔액",
            "당기순이익",
            "총포괄손익",
            "총포괄이익",
            "배당",
            "기말자본",
            "기말잔액",
        )
        if term in compact
    )
    if component_hits >= 4 and movement_hits >= 2:
        return True
    return component_hits >= 4 and movement_hits >= 1 and any(
        marker in compact for marker in ("지배기업의소유주", "지배주주지분", "자본합계")
    )


def _looks_like_kr_retained_earnings_appropriation(signal: str) -> bool:
    compact = _compact_text(signal)
    if "자본을구성하는각적립금" in compact:
        return True
    if "미처분이익잉여금" in compact and "전기이월미처분이익잉여금" in compact:
        return True
    return any(marker in compact for marker in ("미처분이익잉여금", "이익잉여금처분액", "이익준비금")) and any(
        marker in compact for marker in ("배당금", "처분액", "전기이월이익잉여금")
    )


def _has_strong_statement_body(name: str, signal: str) -> bool:
    compact = _compact_text(signal)
    if _looks_like_kr_subsidiary_summary_table(signal):
        return False
    if name == "Consolidated Statement of Financial Position":
        return all(term in compact for term in ("자산총계", "부채총계", "자본총계"))
    if name == "Consolidated Statement of Profit or Loss":
        return "매출액" in compact and "영업이익" in compact and ("당기순이익" in compact or "법인세" in compact)
    if name == "Consolidated Statement of Comprehensive Income":
        if _looks_like_kr_retained_earnings_appropriation(signal):
            return False
        if "당기순이익" in compact and ("당기총포괄이익" in compact or "당기총포괄손익" in compact):
            return True
        if ("매출" in compact or "영업이익" in compact or "영업수익" in compact) and "당기순이익" in compact and (
            "총포괄이익" in compact or "총포괄손익" in compact
        ):
            return True
        return "당기순이익" in compact and (
            ("기타포괄손익" in compact or "기타포괄이익" in compact)
            and ("총포괄손익" in compact or "총포괄이익" in compact or "당기총포괄이익" in compact or "당기총포괄손익" in compact or "재분류" in compact)
        )
    if name == "Consolidated Statement of Cash Flows":
        activity_hits = sum(1 for term in ("영업활동", "투자활동", "재무활동") if term in compact)
        if activity_hits >= 3 and "현금" in compact:
            return True
        return "영업활동" in compact and "현금흐름" in compact and any(
            term in compact for term in ("영업에서창출된현금", "이자의수취", "이자의지급", "법인세")
        )
    if name == "Consolidated Statement of Changes in Equity":
        return _looks_like_kr_equity_statement_body(signal)
    return False


def _has_explicit_statement_title(name: str, signal: str) -> bool:
    title_terms = {
        "Consolidated Statement of Financial Position": (
            "연결 재무상태표",
            "연결재무상태표",
            "consolidated statement of financial position",
            "consolidated balance sheet",
        ),
        "Consolidated Statement of Profit or Loss": (
            "연결 손익계산서",
            "연결손익계산서",
            "consolidated statement of profit or loss",
            "consolidated income statement",
        ),
        "Consolidated Statement of Comprehensive Income": (
            "연결 포괄손익계산서",
            "연결포괄손익계산서",
            "consolidated statement of comprehensive income",
        ),
        "Consolidated Statement of Cash Flows": (
            "연결 현금흐름표",
            "연결현금흐름표",
            "연결 한금흐를표",
            "연결한금흐를표",
            "consolidated statement of cash flows",
            "statement of cash flows",
        ),
        "Consolidated Statement of Changes in Equity": (
            "연결 자본변동표",
            "연결자본변동표",
            "consolidated statement of changes in equity",
            "statement of changes in equity",
        ),
    }
    compact_signal = _compact_text(signal)
    return any(term in signal or _compact_text(term) in compact_signal for term in title_terms.get(name, ()))


def _confidence_for_candidate(name: str, signal: str, score: float) -> str:
    if score >= 90:
        return "high"
    if name == "요약재무정보":
        compact = _compact_text(signal)
        metric_hits = sum(1 for term in ("매출액", "영업이익", "당기순이익", "자산총계", "부채총계", "자본총계") if term in compact)
        has_summary_title = any(
            marker in compact
            for marker in (
                "요약재무정보",
                "요약연결재무정보",
                "연결요약재무정보",
                "주요재무정보",
                "summaryfinancialinformation",
                "selectedfinancialdata",
                "financialhighlights",
            )
        )
        if has_summary_title and (metric_hits >= 3 or _has_strong_summary_body(signal)):
            return "high"
        return "medium"
    if name in _CORE_STATEMENT_NAMES and _has_explicit_statement_title(name, signal):
        return "high"
    if name in _CORE_STATEMENT_NAMES and _has_strong_statement_body(name, signal):
        return "high"
    return "medium"


def group_kr_key_table_candidates(table_index: list[dict[str, Any]] | None) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in table_index or []:
        if not isinstance(item, dict):
            continue
        signal = _table_signal_text(item)
        if not signal:
            continue
        for name, (terms, group) in _CANDIDATE_RULES.items():
            score = max(_score_candidate(signal, terms), _fallback_rule_score(name, signal))
            score = _context_adjusted_score(name, signal, score)
            if score <= 0:
                continue
            row = dict(item)
            row.update(
                {
                    "name": name,
                    "status": "found",
                    "candidate_group": group,
                    "candidate_score": score,
                    "confidence": _confidence_for_candidate(name, signal, score),
                    "_source": "kr_market_profile",
                }
            )
            grouped.setdefault(name, []).append(row)

    ordered: dict[str, list[dict[str, Any]]] = {}
    for name in KR_KEY_TABLE_DISPLAY_ORDER:
        rows = grouped.get(name) or []
        if not rows:
            continue
        rows = sorted(rows, key=lambda row: (-(row.get("candidate_score") or 0), row.get("table_index") or 0))[:5]
        for index, row in enumerate(rows):
            row["is_primary"] = index == 0
        ordered[name] = rows
    return ordered


def candidate_summary_list(key_table_candidates: dict[str, list[dict[str, Any]]], names: list[str]) -> list[dict[str, Any]]:
    summary = []
    for name in names:
        rows = key_table_candidates.get(name) or []
        if not rows:
            if name == "요약재무정보":
                summary.append(
                    {
                        "name": name,
                        "status": "not_applicable",
                        "candidate_group": kr_candidate_group(name),
                        "confidence": "not_applicable",
                        "reason": "kr_summary_not_separately_presented",
                        "display_note": "未单独定位到公司层面 요약재무정보；已排除附注、股东、子公司摘要。",
                    }
                )
                continue
            summary.append({"name": name, "status": "missing", "candidate_group": kr_candidate_group(name)})
            continue
        primary = dict(rows[0])
        primary["name"] = name
        primary["status"] = "found"
        primary["candidate_count"] = len(rows)
        summary.append(primary)
    return summary


def found_sections(markdown: str, table_index: list[dict[str, Any]] | None = None) -> list[str]:
    text = _normalize_text(markdown)
    table_text = _normalize_text("\n".join(_table_signal_text(item) for item in table_index or [] if isinstance(item, dict)))
    source = "\n".join([text, table_text])
    compact_source = _compact_text(source)
    section_terms = {
        "회사의 개요": ("회사의 개요", "company overview"),
        "사업의 내용": ("사업의 내용", "business overview"),
        "이사의 경영진단 및 분석의견": ("이사의 경영진단 및 분석의견", "management discussion and analysis"),
        "재무에 관한 사항": ("재무에 관한 사항", "financial matters", "financial statements"),
        "감사인의 감사의견": ("감사인의 감사의견", "auditor's opinion"),
        "임원 및 직원 등에 관한 사항": ("임원 및 직원 등에 관한 사항", "executives and employees"),
        "계열회사 등에 관한 사항": ("계열회사 등에 관한 사항", "affiliates"),
    }
    found = []
    for name in KR_KEY_SECTIONS:
        terms = section_terms[name]
        if any(_normalize_text(term) in source or _compact_text(term) in compact_source for term in terms):
            found.append(name)
    return found


def kr_quality_report_messages(
    *,
    table_count: int,
    single_row_table_count: int,
    image_ref_count: int,
    found_core_table_count: int,
    suspicious_table_count: int,
) -> tuple[list[str], list[str]]:
    warnings: list[str] = []
    info_messages: list[str] = []
    if table_count:
        single_row_ratio = single_row_table_count / table_count
        if single_row_ratio > 0.2:
            warnings.append("单行/空壳表格比例偏高，建议复核 KR PDF 表格识别质量。")
    if image_ref_count:
        info_messages.append("Markdown 包含图片引用，images 目录将作为 PDF 视觉元素与截图证据来源。")
    if found_core_table_count < 3:
        warnings.append("KR Annual Report 核心财务表候选偏少，建议复核 재무에 관한 사항 / 연결재무제표 附近页面。")
    if suspicious_table_count:
        warnings.append(f"发现 {suspicious_table_count} 张可疑表样本，建议逐项打开可视化溯源。")
    return warnings, info_messages


def build_kr_financial_checks(data: dict[str, Any]) -> dict[str, Any]:
    from financial_extractor import FINANCIAL_CHECKS_SCHEMA_VERSION, FINANCIAL_RULE_VERSION, _now_iso

    warnings = [
        item
        for item in list(data.get("warnings") or [])
        if "合并三大表" not in str(item) and "资产负债表、现金流量表、利润表" not in str(item)
    ]
    if not data.get("statements"):
        warnings.append("KR PDF 未确认完整结构化连接财务报表，已按候选识别模式处理；完整数值勾稽建议结合 DART/XBRL 或原文表格复核。")
    return {
        "schema_version": FINANCIAL_CHECKS_SCHEMA_VERSION,
        "rule_version": FINANCIAL_RULE_VERSION,
        "task_id": data.get("task_id"),
        "filename": data.get("filename"),
        "market": "KR",
        "report_kind": data.get("report_kind"),
        "report_year": data.get("report_year"),
        "industry_profile": data.get("industry_profile"),
        "overall_status": "skipped",
        "summary": {"total": 0, "pass": 0, "fail": 0, "warning": 0, "skipped": 0},
        "checks": [],
        "warnings": warnings,
        "generated_at": _now_iso(),
    }
