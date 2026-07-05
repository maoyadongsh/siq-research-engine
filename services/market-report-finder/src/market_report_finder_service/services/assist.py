from __future__ import annotations

import re
from datetime import date

from market_report_finder_service.data.foreign_aliases import foreign_alias_entry
from market_report_finder_service.models.schemas import (
    Market,
    ReportAssistCandidate,
    ReportAssistCandidateExplanation,
    ReportAssistIntent,
    ReportAssistRequest,
    ReportAssistResponse,
)


class ReportAssistService:
    def assist(self, request: ReportAssistRequest) -> ReportAssistResponse:
        intent = self._parse_intent(request)
        explanations = [self._explain_candidate(candidate, intent) for candidate in request.candidates]
        return ReportAssistResponse(intent=intent, candidate_explanations=explanations)

    def _parse_intent(self, request: ReportAssistRequest) -> ReportAssistIntent:
        prompt = (request.prompt or "").strip()
        joined = prompt.lower()
        inferred_market = self._infer_market(joined)
        market = request.market or inferred_market
        report_year = request.report_year or self._infer_year(joined)
        report_types = request.report_types or self._infer_report_types(joined)
        company_query = request.company_name or self._infer_company_query(prompt, market)
        ticker = request.ticker
        company_id = request.company_id
        notes: list[str] = []

        alias_ticker, alias_name, alias_company_id = self._alias_for_company(company_query or prompt, market)
        if alias_ticker and not ticker:
            ticker = alias_ticker
            notes.append(f"根据常见别名识别为 {alias_name} / {alias_ticker}")
        if alias_company_id and not company_id:
            company_id = alias_company_id
        if alias_name and market in {Market.us, Market.eu}:
            company_query = alias_name

        if not report_types:
            report_types = ["annual"]
            notes.append("未明确报告类型，默认优先年报")
        if not report_year:
            notes.append("未识别年份，可在年份下拉框中确认")

        confidence = 0.35
        if market:
            confidence += 0.2
        if company_query or ticker or company_id:
            confidence += 0.25
        if report_year:
            confidence += 0.1
        if report_types:
            confidence += 0.1

        return ReportAssistIntent(
            market=market,
            company_query=company_query,
            ticker=ticker,
            company_id=company_id,
            cik=request.cik,
            report_year=report_year,
            report_types=report_types,
            confidence=min(confidence, 0.98),
            notes=notes,
        )

    def _explain_candidate(
        self,
        candidate: ReportAssistCandidate,
        intent: ReportAssistIntent,
    ) -> ReportAssistCandidateExplanation:
        title = candidate.title or ""
        report_type = self._normalized_type(candidate.report_type or candidate.form or title)
        report_type_zh = self._report_type_zh(report_type, title)
        title_zh = self._title_zh(title, report_type_zh)
        period_zh = self._period_zh(candidate.report_end)
        warnings = self._warnings(title)
        recommended = self._is_recommended(candidate, report_type, intent, warnings)
        recommendation = self._recommendation(candidate, report_type_zh, intent, recommended, warnings)
        return ReportAssistCandidateExplanation(
            document_url=candidate.document_url,
            title_zh=title_zh,
            report_type_zh=report_type_zh,
            period_zh=period_zh,
            recommendation=recommendation,
            recommended=recommended,
            warnings=warnings,
        )

    @classmethod
    def _infer_market(cls, text: str) -> Market | None:
        if any(token in text for token in ("韩国", "韩股", "dart", "samsung", "hyundai", "005930", "005380", "三星", "三星电子", "现代汽车", "sk海力士")):
            return Market.kr
        if any(token in text for token in ("日本", "日股", "edinet", "toyota", "sony", "kioxia", "sumitomo heavy", "7203", "6758", "285a", "6302", "丰田", "丰田汽车", "索尼", "任天堂", "铠侠", "鎧俠", "住友重工", "住友重机械", "住友重機械")):
            return Market.jp
        if any(token in text for token in ("港股", "hkex", ".hk")):
            return Market.hk
        if any(token in text for token in ("美股", "sec", "10-k", "10-q", "aapl", "msft", "amzn", "苹果", "亚马逊", "微软")):
            return Market.us
        if any(token in text for token in ("欧股", "欧洲", "asml", "siemens", "airbus", "nestle", "ubs", "阿斯麦", "西门子", "空客", "雀巢", "瑞银")):
            return Market.eu
        if any(token in text for token in ("a股", "巨潮", "cninfo")):
            return Market.cn
        for alias_market in (Market.jp, Market.kr, Market.us, Market.eu, Market.hk):
            if foreign_alias_entry(alias_market.value, text):
                return alias_market
        return None

    @staticmethod
    def _infer_year(text: str) -> int | None:
        match = re.search(r"(20\d{2})", text)
        return int(match.group(1)) if match else None

    @staticmethod
    def _infer_report_types(text: str) -> list[str]:
        types: list[str] = []
        if any(token in text for token in ("年报", "年度", "annual", "10-k", "20-f", "사업보고서", "有価証券報告書", "有价证券报告书", "有價證券報告書")):
            types.append("annual")
        if any(token in text for token in ("半年", "半年度", "中报", "半期", "半期報告", "semi", "interim", "반기")):
            types.append("semiannual")
        if any(token in text for token in ("三季", "q3", "3q", "第三季度", "3분기", "第3四半期")):
            types.append("q3")
        elif any(token in text for token in ("一季", "q1", "1q", "第一季度", "1분기", "第1四半期")):
            types.append("q1")
        elif any(token in text for token in ("二季", "q2", "2q", "第二季度", "第2四半期")):
            types.append("q2")
        elif any(token in text for token in ("季报", "季度", "quarter", "분기", "四半期")):
            types.append("quarterly")
        return list(dict.fromkeys(types))

    @classmethod
    def _infer_company_query(cls, prompt: str, market: Market | None) -> str | None:
        text = prompt.strip()
        if not text:
            return None
        cleaned = re.sub(r"20\d{2}年?", " ", text)
        cleaned = re.sub(r"(下载|查找|搜索|帮我|请|财报|报告|有价证券报告书|有價證券報告書|年报|年度|半年报|半年度|中报|季报|季度|三季度|一季度|二季度|q[1-4]|Q[1-4])", " ", cleaned)
        cleaned = re.sub(r"(韩国|韩股|日本|日股|美股|港股|欧股|欧洲|A股|a股)", " ", cleaned)
        cleaned = re.sub(r"\b(年|和|及|与)\b", " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" ，,。.")
        if cleaned:
            return cleaned
        alias = foreign_alias_entry(market.value if market else None, prompt)
        if alias:
            return str(alias.get("canonical_name") or alias.get("aliases", [""])[0] or "").strip() or None
        return None

    @classmethod
    def _alias_for_company(cls, query: str, market: Market | None) -> tuple[str | None, str | None, str | None]:
        alias = foreign_alias_entry(market.value if market else None, query)
        if alias:
            return (
                str(alias.get("ticker") or "") or None,
                str(alias.get("canonical_name") or "") or None,
                str(alias.get("company_id") or "") or None,
            )
        return None, None, None

    @staticmethod
    def _normalized_type(value: str) -> str:
        text = str(value or "").lower()
        if any(token in text for token in ("10-k", "20-f", "annual", "사업보고서", "有価証券報告書")):
            return "annual"
        if any(token in text for token in ("semiannual", "semi-annual", "interim", "half", "반기", "半期")):
            return "semiannual"
        if any(token in text for token in ("quarter", "q1", "q2", "q3", "q4", "분기", "四半期")):
            return "quarterly"
        if "earnings" in text or "results" in text:
            return "earnings"
        return text or "unknown"

    @staticmethod
    def _report_type_zh(report_type: str, title: str) -> str:
        title_lower = title.lower()
        if report_type == "annual":
            return "年度报告"
        if report_type == "semiannual":
            return "半年度/半期报告"
        if report_type == "quarterly":
            if any(token in title_lower for token in ("3분기", "q3", "third", "第3四半期")):
                return "三季度报告"
            if any(token in title_lower for token in ("1분기", "q1", "first", "第1四半期")):
                return "一季度报告"
            if any(token in title_lower for token in ("2분기", "q2", "second", "第2四半期")):
                return "二季度报告"
            return "季度报告"
        if report_type == "earnings":
            return "业绩公告"
        return "披露文件"

    @staticmethod
    def _title_zh(title: str, report_type_zh: str) -> str:
        replacements = {
            "사업보고서": "年度报告",
            "반기보고서": "半年度报告",
            "분기보고서": "季度报告",
            "有価証券報告書": "有价证券报告书",
            "半期報告書": "半期报告书",
            "四半期報告書": "季度报告书",
        }
        translated = title
        for source, target in replacements.items():
            translated = translated.replace(source, target)
        if translated == title:
            translated = f"{report_type_zh}：{title}"
        return translated

    @staticmethod
    def _period_zh(report_end: date | None) -> str:
        if not report_end:
            return "报告期待确认"
        quarter = {
            (3, 31): "一季度",
            (6, 30): "上半年/二季度",
            (9, 30): "三季度",
            (12, 31): "全年/四季度",
        }.get((report_end.month, report_end.day), "")
        suffix = f"，{quarter}" if quarter else ""
        return f"{report_end.isoformat()}{suffix}"

    @staticmethod
    def _warnings(title: str) -> list[str]:
        text = title.lower()
        warnings: list[str] = []
        if any(token in text for token in ("정정", "訂正", "amend", "/a", "更正", "修订")):
            warnings.append("可能是修订/更正版，请确认是否需要替代原始版本")
        if any(token in text for token in ("摘要", "summary")):
            warnings.append("可能是摘要文件，建议优先下载完整报告")
        return warnings

    @staticmethod
    def _is_recommended(
        candidate: ReportAssistCandidate,
        report_type: str,
        intent: ReportAssistIntent,
        warnings: list[str],
    ) -> bool:
        if any("摘要" in warning for warning in warnings):
            return False
        if intent.report_year and candidate.report_end and candidate.report_end.year != intent.report_year:
            return False
        expected = {ReportAssistService._normalized_type(item) for item in intent.report_types}
        if expected and report_type not in expected:
            if not (report_type == "quarterly" and expected & {"q1", "q2", "q3", "q4"}):
                return False
        if "q1" in intent.report_types and candidate.report_end and candidate.report_end.month != 3:
            return False
        if "q2" in intent.report_types and candidate.report_end and candidate.report_end.month != 6:
            return False
        if "q3" in intent.report_types and candidate.report_end and candidate.report_end.month != 9:
            return False
        return True

    @staticmethod
    def _recommendation(
        candidate: ReportAssistCandidate,
        report_type_zh: str,
        intent: ReportAssistIntent,
        recommended: bool,
        warnings: list[str],
    ) -> str:
        if warnings:
            return "需人工确认：" + "；".join(warnings)
        if recommended:
            if intent.report_year:
                return f"推荐：报告类型和 {intent.report_year} 年报告期匹配"
            return f"推荐：官方候选中的{report_type_zh}"
        if intent.report_year and candidate.report_end and candidate.report_end.year != intent.report_year:
            return f"不默认推荐：报告期为 {candidate.report_end.year} 年"
        return "可选：官方候选文件"
