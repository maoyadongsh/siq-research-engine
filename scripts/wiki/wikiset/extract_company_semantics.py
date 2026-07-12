#!/usr/bin/env python3
"""Generate company-level semantic layers for the rebuilt wiki.

The extractor is intentionally rule-first. It creates auditable segments,
facts, relations, claims, and retrieval indexes from existing wiki artifacts
without asking a model to invent summaries.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from market_semantic_profiles import (
    classify_segment_title as profile_classify_segment_title,
    metric_aliases_for_market,
    profile_for_market,
    title_boost_keywords_for_market,
    topic_aliases_for_market as profile_topic_aliases_for_market,
)

RULE_VERSION = "single_company_subject_rules_v1"
REQUIRED_RULE_OUTPUTS = (
    "subject_profile.json",
    "segments.json",
    "facts.json",
    "relations.json",
    "claims.json",
    "retrieval_index.json",
    "note_links.json",
    "document_links.json",
    "evidence_semantic.json",
    "extraction_log.json",
)

CHINESE_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}


def chinese_note_number(text: str) -> int | None:
    """Parse compact Chinese note numbers such as 十九 or 二十一."""
    value = str(text or "").strip()
    if not value:
        return None
    if value == "十":
        return 10
    if "十" in value:
        left, _, right = value.partition("十")
        tens = CHINESE_DIGITS.get(left, 1 if left == "" else None)
        ones = CHINESE_DIGITS.get(right, 0 if right == "" else None)
        if tens is None or ones is None:
            return None
        return tens * 10 + ones
    if len(value) == 1:
        return CHINESE_DIGITS.get(value)
    return None


def leading_note_number(title: str) -> int | None:
    """Return a leading financial-note number, excluding years and prose headings."""
    text = re.sub(r"^#+\s*", "", str(title or "")).strip()
    match = re.match(r"^[（(]\s*(\d{1,3})\s*[）)]", text)
    if match:
        return int(match.group(1))
    match = re.match(r"^[（(]\s*([零〇一二两三四五六七八九十]{1,4})\s*[）)]", text)
    if match:
        return chinese_note_number(match.group(1))
    match = re.match(r"^(\d{1,3})(?:\s*[、.．]\s*|\s+)(?!年|年度)", text)
    if match:
        return int(match.group(1))
    return None

CORE_KEY_METRICS = {
    "operating_revenue",
    "parent_net_profit",
    "deducted_parent_net_profit",
    "operating_cash_flow_net",
    "basic_eps",
    "diluted_eps",
    "weighted_avg_roe",
    "total_assets",
    "equity_attributable_parent",
}

CORE_THREE_STATEMENT_METRICS = {
    "total_assets",
    "total_liabilities",
    "total_equity",
    "current_assets",
    "non_current_assets",
    "current_liabilities",
    "non_current_liabilities",
    "operating_revenue",
    "operating_profit",
    "total_profit",
    "net_profit",
    "parent_net_profit",
    "operating_cash_flow_net",
    "investing_cash_flow_net",
    "financing_cash_flow_net",
    "cash_and_cash_equivalents_end",
}

TOPIC_ALIASES = {
    "company_profile": ["公司简介", "公司信息", "主体身份", "基本情况"],
    "key_financials": ["主要财务指标", "关键指标", "财务摘要", "主要会计数据"],
    "management_discussion": ["管理层讨论", "经营分析", "讨论与分析"],
    "business_overview": ["主营业务", "业务结构", "经营模式", "主要业务"],
    "industry_analysis": ["行业情况", "行业分析", "监管环境", "竞争格局"],
    "segment_performance": ["分部表现", "业务板块", "收入构成", "主营业务分析"],
    "product_service": ["产品", "服务", "品牌", "技术路线"],
    "region_market": ["地区", "区域", "海外", "全球布局", "市场"],
    "customer_supplier": ["客户", "供应商", "采购", "销售"],
    "rd_innovation": ["研发", "创新", "专利", "技术"],
    "capex_projects": ["项目", "产能", "基地", "在建工程", "资本开支"],
    "risk_factors": ["风险", "不确定性", "风险因素"],
    "corporate_governance": ["公司治理", "董事", "高管", "内控", "审计"],
    "shareholders": ["股东", "股份", "股本", "持股"],
    "dividend": ["分红", "利润分配", "股息", "派息"],
    "major_events": ["重要事项", "重大事项", "诉讼", "担保", "关联交易", "收购"],
    "financial_statements": ["财务报表", "资产负债表", "利润表", "现金流量表"],
    "notes_to_financials": ["财务报表附注", "附注"],
    "financial_note_links": ["附注对应关系", "报表项目附注", "附注索引", "附注回溯"],
    "esg_social_responsibility": ["ESG", "环境", "社会责任", "绿色发展"],
}

GENERIC_TOPIC_ALIASES = {
    "company_profile": ["company profile", "about", "group profile", "basf group", "company information"],
    "key_financials": ["at a glance", "key figures", "selected financial data", "financial highlights", "sales and employees"],
    "management_discussion": ["management report", "management's report", "management’s report", "chairman", "outlook", "forecast", "results of operations", "financial position"],
    "business_overview": ["business", "strategy", "portfolio", "business model", "winning ways", "operations"],
    "industry_analysis": ["market", "macroeconomic", "industry", "competition", "regulatory"],
    "segment_performance": ["segment", "chemicals", "materials", "industrial solutions", "surface technologies", "nutrition", "agricultural solutions"],
    "product_service": ["products", "services", "brands", "solutions"],
    "region_market": ["region", "north america", "europe", "asia pacific", "south america", "global"],
    "customer_supplier": ["customers", "suppliers", "procurement", "sales"],
    "rd_innovation": ["research", "development", "innovation", "technology", "patents"],
    "capex_projects": ["investment", "capital expenditures", "projects", "site", "zhanjiang", "ludwigshafen", "geismar"],
    "risk_factors": ["risk", "risks", "opportunities", "uncertainties", "risk report"],
    "corporate_governance": ["corporate governance", "supervisory board", "board of executive", "compliance", "audit"],
    "shareholders": ["shareholders", "share", "shares", "stock"],
    "dividend": ["dividend", "distribution", "payout"],
    "major_events": ["events", "litigation", "legal", "acquisition", "divestiture", "transaction"],
    "financial_statements": ["financial statements", "income statement", "balance sheet", "cash flows", "statement of cash", "equity"],
    "notes_to_financials": ["notes", "accounting policies", "consolidated financial statements"],
    "esg_social_responsibility": ["sustainability", "esg", "climate", "environment", "employees", "safety", "emissions", "responsibility", "esrs"],
}

MARKET_TOPIC_ALIASES = {
    "HK": {
        **GENERIC_TOPIC_ALIASES,
        "company_profile": GENERIC_TOPIC_ALIASES["company_profile"] + ["公司資料", "集團簡介", "公司簡介", "公司資料及架構", "who we are", "our values"],
        "key_financials": GENERIC_TOPIC_ALIASES["key_financials"] + ["財務摘要", "主要財務數據", "five year summary", "financial performance indicators"],
        "management_discussion": GENERIC_TOPIC_ALIASES["management_discussion"] + ["管理層討論", "管理層討論及分析", "financial review", "strategic report", "ceo's review", "chairman's statement"],
        "business_overview": GENERIC_TOPIC_ALIASES["business_overview"] + ["業務回顧", "業務概覽", "our business", "our strategy", "our priorities"],
        "industry_analysis": GENERIC_TOPIC_ALIASES["industry_analysis"] + ["市場環境", "行業", "competition", "regulation"],
        "segment_performance": GENERIC_TOPIC_ALIASES["segment_performance"] + ["分部", "segmental analysis", "business review", "operating segments"],
        "product_service": GENERIC_TOPIC_ALIASES["product_service"] + ["產品", "服務", "brands"],
        "region_market": GENERIC_TOPIC_ALIASES["region_market"] + ["香港", "中國內地", "大中華", "asia", "hong kong", "mainland china"],
        "customer_supplier": GENERIC_TOPIC_ALIASES["customer_supplier"] + ["客戶", "供應商", "sales channels"],
        "rd_innovation": GENERIC_TOPIC_ALIASES["rd_innovation"] + ["研發", "創新", "technology", "digital"],
        "capex_projects": GENERIC_TOPIC_ALIASES["capex_projects"] + ["資本開支", "投資", "projects"],
        "risk_factors": GENERIC_TOPIC_ALIASES["risk_factors"] + ["風險", "risk review", "risk management", "principal risks"],
        "corporate_governance": GENERIC_TOPIC_ALIASES["corporate_governance"] + ["企業管治", "公司治理", "board", "audit committee"],
        "shareholders": GENERIC_TOPIC_ALIASES["shareholders"] + ["股東", "股份", "share capital"],
        "dividend": GENERIC_TOPIC_ALIASES["dividend"] + ["股息", "分紅", "dividend per share"],
        "major_events": GENERIC_TOPIC_ALIASES["major_events"] + ["重大事項", "收購", "出售", "litigation", "strategic transactions"],
        "financial_statements": GENERIC_TOPIC_ALIASES["financial_statements"] + ["財務報表", "consolidated financial statements"],
        "notes_to_financials": GENERIC_TOPIC_ALIASES["notes_to_financials"] + ["附註", "notes to the financial statements"],
    },
    "EU": {
        **GENERIC_TOPIC_ALIASES,
        "company_profile": GENERIC_TOPIC_ALIASES["company_profile"] + ["our business", "our brands", "who we are", "at a glance"],
        "key_financials": GENERIC_TOPIC_ALIASES["key_financials"] + ["group highlights", "financial highlights", "key performance indicators", "kpis"],
        "management_discussion": GENERIC_TOPIC_ALIASES["management_discussion"] + ["strategic report", "management report", "financial review", "year in review", "outlook"],
        "business_overview": GENERIC_TOPIC_ALIASES["business_overview"] + ["business model", "our strategy", "strategy", "portfolio", "value creation"],
        "segment_performance": GENERIC_TOPIC_ALIASES["segment_performance"] + ["operating segments", "segment information", "divisions", "business units"],
        "risk_factors": GENERIC_TOPIC_ALIASES["risk_factors"] + ["principal risks", "risk management", "risk report", "opportunities and risks"],
        "corporate_governance": GENERIC_TOPIC_ALIASES["corporate_governance"] + ["governance report", "supervisory board", "management board", "audit committee", "remuneration"],
        "financial_statements": GENERIC_TOPIC_ALIASES["financial_statements"] + ["consolidated statements", "consolidated income statement", "statement of financial position"],
        "notes_to_financials": GENERIC_TOPIC_ALIASES["notes_to_financials"] + ["notes to the consolidated financial statements", "accounting policies"],
        "esg_social_responsibility": GENERIC_TOPIC_ALIASES["esg_social_responsibility"] + ["sustainability statement", "esrs", "csrd", "climate action", "taxonomy"],
    },
    "KR": {
        "risk_factors": ["위험", "위험관리", "주요위험", "리스크", "시장위험", "신용위험", "유동성위험"],
        "company_profile": ["회사의 개요", "회사 개요", "회사의개요", "기업개요", "회사 현황", "법적·상업적 명칭"],
        "key_financials": ["주요 재무정보", "요약재무정보", "재무정보", "주요경영지표", "재무에 관한 사항"],
        "management_discussion": ["이사의 경영진단", "경영진단", "경영성과", "재무상태", "영업실적", "경영실적", "향후 전망"],
        "business_overview": ["사업의 내용", "사업의 개요", "사업 내용", "주요 사업", "사업목적"],
        "industry_analysis": ["시장", "산업", "경쟁", "성장성", "시장점유율"],
        "segment_performance": ["부문", "사업부문", "매출", "지역별 매출", "제품별 매출", "매출 및 수주상황"],
        "product_service": ["주요 제품", "제품 및 서비스", "서비스", "가격변동"],
        "region_market": ["해외", "국내", "지역", "시장"],
        "customer_supplier": ["주요 고객", "판매경로", "판매방법", "원재료", "공급", "매입", "수주"],
        "rd_innovation": ["연구개발", "개발활동", "특허", "기술"],
        "capex_projects": ["생산설비", "투자", "생산능력", "생산실적", "설비"],
        "corporate_governance": ["이사회", "감사", "임원", "지배구조", "내부통제"],
        "shareholders": ["주식", "주주", "최대주주", "자본금"],
        "dividend": ["배당", "이익배당"],
        "major_events": ["중요한 사항", "주요사항", "소송", "거래", "합병", "양수도"],
        "financial_statements": ["재무제표", "연결재무제표", "손익계산서", "재무상태표", "현금흐름표"],
        "notes_to_financials": ["주석", "재무제표 주석"],
        "esg_social_responsibility": ["지속가능", "esg", "환경", "사회", "책임"],
    },
    "JP": {
        "risk_factors": ["事業等のリスク", "リスク", "リスク管理"],
        "company_profile": ["企業の概況", "会社の概況", "提出会社の状況", "会社情報", "企業情報"],
        "key_financials": ["主要な経営指標", "経営指標等の推移", "財務ハイライト"],
        "management_discussion": ["経営者による財政状態", "経営成績", "キャッシュ・フロー", "経営方針", "対処すべき課題", "経営環境"],
        "business_overview": ["事業の内容", "事業の概況", "事業内容"],
        "industry_analysis": ["市場", "業界", "競争", "事業環境"],
        "segment_performance": ["セグメント", "事業別", "地域別", "売上", "部門"],
        "product_service": ["製品", "サービス", "商品"],
        "region_market": ["地域", "海外", "国内", "北米", "欧州", "アジア"],
        "customer_supplier": ["顧客", "取引先", "仕入", "販売", "供給"],
        "rd_innovation": ["研究開発", "技術", "イノベーション", "知的財産"],
        "capex_projects": ["設備投資", "生産設備", "投資", "資本的支出"],
        "corporate_governance": ["コーポレートガバナンス", "ガバナンス", "役員", "取締役", "監査"],
        "shareholders": ["株式", "株主", "所有者別状況"],
        "dividend": ["配当", "剰余金"],
        "major_events": ["重要な契約", "重要な後発事象", "訴訟", "買収", "組織再編"],
        "financial_statements": ["連結財務諸表", "財務諸表", "貸借対照表", "損益計算書", "キャッシュ・フロー"],
        "notes_to_financials": ["注記事項", "注記", "連結附属明細表"],
        "esg_social_responsibility": ["サステナビリティ", "esg", "環境", "社会", "人的資本", "気候変動"],
    },
}

FINANCIAL_NOTE_TERMS = (
    "商誉",
    "应收账款",
    "其他应收款",
    "预付款项",
    "存货",
    "合同资产",
    "固定资产",
    "在建工程",
    "无形资产",
    "开发支出",
    "长期股权投资",
    "投资性房地产",
    "递延所得税",
    "短期借款",
    "长期借款",
    "应付账款",
    "合同负债",
    "预计负债",
    "营业收入",
    "营业成本",
    "销售费用",
    "管理费用",
    "研发费用",
    "财务费用",
    "资产减值损失",
    "信用减值损失",
    "长期待摊费用",
    "其他非流动资产",
    "使用权受到限制的资产",
)


def compact_text(text) -> str:
    return re.sub(r"[\s（）()_\-：:、,，;；/]+", "", str(text or "").lower())


def target_conflicts_statement_item(statement_item: str, target_title: str | None, target_preview: str | None) -> bool:
    """Detect direct statement->table edges that point at a sibling financial item."""
    source_norm = compact_text(statement_item)
    if not source_norm:
        return False
    target_title_norm = compact_text(target_title)
    target_text_norm = compact_text(f"{target_title or ''} {target_preview or ''}")
    if source_norm in target_text_norm:
        return False
    for term in FINANCIAL_NOTE_TERMS:
        term_norm = compact_text(term)
        if not term_norm or term_norm == source_norm:
            continue
        if source_norm in term_norm or term_norm in source_norm:
            continue
        if term_norm in target_title_norm:
            return True
    return False


def period_candidates(report_year: str) -> list[str]:
    """Return preferred comparison periods for year-over-year claims."""
    if not report_year.isdigit():
        return []
    prior = str(int(report_year) - 1)
    return [prior, f"{prior}_adjusted", f"{prior}_unadjusted", f"{prior}-12-31"]


def close_amount(a, b, rel_tol: float = 1e-4, abs_tol: float = 1.0) -> bool:
    if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
        return False
    return abs(float(a) - float(b)) <= max(abs(float(a)) * rel_tol, abs_tol)


def secondary_amount_check(amount_check: dict) -> dict | None:
    """Recover matches when upstream normalization scale differs but raw values align."""
    statements = amount_check.get("statement_values") or []
    candidates = amount_check.get("note_candidates") or []
    for statement in statements:
        statement_values = [
            ("normalized_value", statement.get("normalized_value")),
            ("value", statement.get("value")),
        ]
        for candidate in candidates:
            candidate_values = [
                ("normalized_value", candidate.get("normalized_value")),
                ("value", candidate.get("value")),
            ]
            for s_kind, s_value in statement_values:
                for c_kind, c_value in candidate_values:
                    if close_amount(s_value, c_value):
                        return {
                            "status": "verified",
                            "confidence": "medium",
                            "method": f"secondary_{s_kind}_to_{c_kind}_match",
                            "matched": {
                                "statement": statement,
                                "note": candidate,
                                "difference": abs(float(s_value) - float(c_value)),
                                "tolerance": max(abs(float(s_value)) * 1e-4, 1.0),
                            },
                        }
    return None


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path, default=None):
    try:
        with path.open("r", encoding="utf-8") as infile:
            return json.load(infile)
    except Exception:
        return default


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as outfile:
        json.dump(payload, outfile, ensure_ascii=False, indent=2)
        outfile.write("\n")


def sha256_file(path: Path) -> str | None:
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as infile:
        for chunk in iter(lambda: infile.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def slug(text: str, fallback: str = "item") -> str:
    value = re.sub(r"[^0-9A-Za-z_\-\u4e00-\u9fff]+", "_", str(text or "")).strip("_")
    return value[:48] or fallback


def clean_text(line: str) -> str:
    text = re.sub(r"<[^>]+>", " ", line)
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def trim_preview(value, max_chars: int = 360) -> str | None:
    text = clean_text(str(value or ""))
    return text[:max_chars] if text else None


def read_markdown_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8", errors="ignore").splitlines()


def build_page_for_line(lines: list[str]) -> list[int | None]:
    page_for_line: list[int | None] = [None] * (len(lines) + 1)
    current_page = None
    for index, line in enumerate(lines, start=1):
        match = re.match(r"\[PDF_PAGE:\s*(\d+)\]", line.strip())
        if match:
            current_page = int(match.group(1))
        page_for_line[index] = current_page
    return page_for_line


def line_window_quote(lines: list[str], start: int, end: int, max_chars: int = 180) -> str:
    pieces = []
    in_details = False
    for raw in lines[max(0, start - 1): min(len(lines), end)]:
        line = raw.strip()
        if not line:
            continue
        if line.startswith("<details"):
            in_details = True
            continue
        if line.startswith("</details"):
            in_details = False
            continue
        if in_details:
            continue
        if line.startswith("[PDF_PAGE"):
            continue
        if line.startswith("#"):
            continue
        if line.startswith("<table"):
            continue
        text = clean_text(line)
        if text:
            pieces.append(text)
        if sum(len(p) for p in pieces) >= max_chars:
            break
    quote = " ".join(pieces)
    return quote[:max_chars]


def classify_segment(title: str) -> str:
    text = re.sub(r"\s+", "", str(title or "")).lower()
    checks = [
        ("company_profile", ["公司简介", "公司信息", "基本情况简介", "股票上市", "注册变更", "联系人"]),
        ("key_financials", ["主要会计数据", "主要财务指标", "财务概要", "财务摘要", "分季度主要财务指标", "非经常性损益", "会计准则"]),
        ("management_discussion", ["管理层讨论", "经营分析", "讨论与分析", "董事长致辞", "行长致辞", "首席执行官致辞"]),
        ("business_overview", ["主要业务", "业务概要", "业务概况", "经营模式", "从事的主要业务"]),
        ("industry_analysis", ["行业情况", "行业分析", "监管环境", "市场环境", "竞争格局"]),
        ("segment_performance", ["主营业务分析", "分行业", "分产品", "分地区", "业务板块", "分部", "营业收入构成"]),
        ("product_service", ["产品", "服务", "品牌", "车型", "药品", "产品线"]),
        ("region_market", ["地区", "区域", "海外", "全球布局", "境外", "市场拓展"]),
        ("customer_supplier", ["客户", "供应商", "采购", "销售客户"]),
        ("rd_innovation", ["研发", "技术", "创新", "专利", "核心技术"]),
        ("capex_projects", ["产能", "生产基地", "建设项目", "在建工程", "募投项目", "资本开支", "重大项目"]),
        ("risk_factors", ["风险", "不确定性", "重大风险"]),
        ("corporate_governance", ["公司治理", "董事", "监事", "高级管理人员", "内控", "内部控制", "审计"]),
        ("shareholders", ["股东", "股份变动", "股本", "持股"]),
        ("dividend", ["分红", "利润分配", "股息", "派息"]),
        ("major_events", ["重要事项", "重大事项", "诉讼", "担保", "关联交易", "收购", "处罚"]),
        ("financial_statements", ["财务报表", "资产负债表", "利润表", "现金流量表", "所有者权益"]),
        ("notes_to_financials", ["附注", "财务报表附注"]),
        ("esg_social_responsibility", ["esg", "环境", "社会责任", "绿色发展", "可持续"]),
    ]
    for segment_type, keywords in checks:
        if any(keyword.lower() in text for keyword in keywords):
            return segment_type
    return "other"


def classify_generic_segment(title: str) -> str:
    text = re.sub(r"\s+", " ", str(title or "")).strip().lower()
    if not text:
        return "other"
    for segment_type, keywords in GENERIC_TOPIC_ALIASES.items():
        if any(keyword in text for keyword in keywords):
            return segment_type
    return "other"


def topic_aliases_for_market(market: str | None, generic_route: bool = False) -> dict[str, list[str]]:
    market_code = str(market or "").upper()
    if market_code in {"HK", "KR", "JP", "EU", "US"} or generic_route:
        return profile_topic_aliases_for_market(market_code, generic_route)
    return GENERIC_TOPIC_ALIASES if generic_route else TOPIC_ALIASES


def classify_market_segment(title: str, market: str | None, generic_route: bool = False) -> str:
    if not market and not generic_route:
        return classify_segment(title)
    market_code = str(market or "").upper()
    if market_code in {"HK", "KR", "JP", "EU", "US"} or generic_route:
        return profile_classify_segment_title(title, market_code, generic_route)
    aliases = topic_aliases_for_market(market, generic_route)
    text_no_space = re.sub(r"\s+", "", str(title or "")).lower()
    text_space = re.sub(r"\s+", " ", str(title or "")).strip().lower()
    if not text_space:
        return "other"
    for segment_type, keywords in aliases.items():
        for keyword in keywords:
            key_space = str(keyword or "").strip().lower()
            key_no_space = re.sub(r"\s+", "", key_space)
            if key_space and (key_space in text_space or key_no_space in text_no_space):
                return segment_type
    return "other"


def importance_for(segment_type: str, title: str, market: str | None = None) -> str:
    if segment_type in {"company_profile", "key_financials", "business_overview", "management_discussion", "risk_factors"}:
        return "high"
    if segment_type in {"financial_statements", "notes_to_financials", "segment_performance", "rd_innovation"}:
        return "high"
    if "重大" in title or "重要" in title:
        return "high"
    if market:
        haystack = str(title or "").lower()
        if any(str(keyword).lower() in haystack for keyword in title_boost_keywords_for_market(market)):
            return "high"
    return "medium"


def make_url(template: str | None, task_id: str, key: str, value) -> str | None:
    if not template or value in (None, ""):
        return None
    return template.replace("{task_id}", str(task_id)).replace("{" + key + "}", str(value))


def to_int(value) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


class CompanyExtractor:
    def __init__(self, company_dir: Path):
        self.company_dir = company_dir
        self.company = read_json(company_dir / "company.json", {})
        self.company_id = self.company.get("company_id") or company_dir.name
        self.primary_report = self.company.get("primary_report_id") or "2025-annual"
        if not (company_dir / "reports" / self.primary_report).is_dir():
            report_dirs = sorted(path for path in (company_dir / "reports").glob("*") if path.is_dir())
            if report_dirs:
                self.primary_report = report_dirs[0].name
        self.report_dir = company_dir / "reports" / self.primary_report
        self.report_json_path = self.report_dir / "report.json"
        self.report_md_path = self.report_dir / "report.md"
        self.document_full_path = self.report_dir / "document_full.json"
        self.artifact_manifest_path = self.report_dir / "artifact_manifest.json"
        if not self.artifact_manifest_path.is_file() and (self.report_dir / "manifest.json").is_file():
            self.artifact_manifest_path = self.report_dir / "manifest.json"
        self.report = read_json(self.report_json_path, {})
        if not self.report:
            sec_manifest = read_json(self.report_dir / "manifest.json", {})
            if sec_manifest:
                self.report = {
                    "report": {
                        "report_id": self.primary_report,
                        "report_year": sec_manifest.get("fiscal_year"),
                        "report_kind": sec_manifest.get("form") or sec_manifest.get("report_type"),
                        "source_filename": sec_manifest.get("source_url") or sec_manifest.get("accession_number"),
                    },
                    "source": {
                        "task_id": sec_manifest.get("filing_id") or sec_manifest.get("accession_number") or self.primary_report,
                        "source_url": sec_manifest.get("source_url"),
                    },
                    "quality_summary": sec_manifest.get("quality_summary") or {},
                    "tables": [],
                }
        self.document = read_json(self.document_full_path, {})
        self.lines = read_markdown_lines(self.report_md_path)
        if not self.lines and (self.report_dir / "README.md").is_file():
            self.lines = read_markdown_lines(self.report_dir / "README.md")
        self.page_for_line = build_page_for_line(self.lines)
        self.semantic_dir = company_dir / "semantic"
        self.generated_at = now_iso()
        self.evidence: list[dict] = []
        self.evidence_cache: dict[tuple, str] = {}
        self.fact_ids_by_topic: dict[str, list[str]] = defaultdict(list)
        self.segment_ids_by_type: dict[str, list[str]] = defaultdict(list)
        self.claim_ids_by_topic: dict[str, list[str]] = defaultdict(list)
        self.task_id = ((self.report.get("source") or {}).get("task_id")
                        or ((self.company.get("reports") or [{}])[0]).get("task_id")
                        or "")
        self.pdf_template = (self.report.get("source") or {}).get("pdf_page_url_template")
        self.source_page_template = (self.report.get("source") or {}).get("source_page_url_template")
        self.source_table_template = (self.report.get("source") or {}).get("source_table_url_template")
        self.table_by_index = {
            int(t.get("table_index")): t
            for t in (self.report.get("tables") or [])
            if t.get("table_index") is not None
        }

    def page_for_markdown_line(self, line: int | None, fallback=None, prefer_fallback: bool = False) -> int | None:
        line_no = to_int(line)
        fallback_page = to_int(fallback)
        if prefer_fallback and fallback_page is not None:
            return fallback_page
        if line_no is not None and 0 < line_no < len(self.page_for_line):
            page = self.page_for_line[line_no]
            if page is not None:
                return page
        return fallback_page

    def markdown_anchor_page(self, line: int | None) -> int | None:
        line_no = to_int(line)
        if line_no is not None and 0 < line_no < len(self.page_for_line):
            return self.page_for_line[line_no]
        return None

    def table_page_for_index(self, table_index: int | None) -> int | None:
        table_no = to_int(table_index)
        if table_no is None:
            return None
        table = self.table_by_index.get(table_no) or {}
        return to_int(table.get("pdf_page_number"))

    def table_index_for_page(self, table_index: int | None, page: int | None) -> int | None:
        return to_int(table_index)

    def table_conflict_extra(self, table_index: int | None, page: int | None) -> dict:
        table_no = to_int(table_index)
        table_page = self.table_page_for_index(table_no)
        if table_no is None or table_page is None or page is None or table_page == int(page):
            return {}
        return {
            "table_pdf_page": table_page,
            "table_index_conflict": {
                "table_pdf_page": table_page,
                "anchor_pdf_page": int(page),
                "resolution": "table_url_kept_for_structured_table",
            },
        }

    def metric_payload(self, filename: str) -> dict:
        candidates = [
            self.company_dir / "metrics" / filename,
            self.company_dir / "metrics" / "reports" / self.primary_report / filename,
            self.company_dir / "metrics" / "latest" / filename,
            self.report_dir / "metrics" / filename,
        ]
        if str(self.company.get("market") or "").upper() == "US" and filename in {"key_metrics.json", "three_statements.json"}:
            candidates.extend([
                self.report_dir / "metrics" / "normalized_metrics.json",
                self.company_dir / "metrics" / "latest" / "normalized_metrics.json",
            ])
        for path in candidates:
            payload = read_json(path, None)
            if isinstance(payload, dict):
                payload["_semantic_source_file"] = str(path.relative_to(self.company_dir))
                return payload
        return {}

    def metric_items(self, payload: dict) -> list[dict]:
        data = payload.get("data")
        if isinstance(data, dict) and isinstance(data.get("metrics"), list):
            return data.get("metrics") or []
        if isinstance(data, list):
            return data
        if isinstance(payload.get("metrics"), list):
            return payload.get("metrics") or []
        return []

    def metric_source_file(self, payload: dict, fallback: str) -> str:
        return str(payload.get("_semantic_source_file") or fallback)

    def metric_key(self, item: dict) -> str:
        return str(item.get("canonical_name") or item.get("metric_key") or item.get("name") or item.get("label") or "").strip()

    def metric_period(self, item: dict) -> str | None:
        for key in ("period", "period_key", "period_end", "fiscal_year"):
            value = item.get(key)
            if value not in (None, ""):
                return str(value)
        source = item.get("source") or item.get("evidence") or {}
        for key in ("period", "period_key", "period_end", "fiscal_year"):
            value = source.get(key)
            if value not in (None, ""):
                return str(value)
        return (self.report.get("report") or {}).get("report_year")

    def metric_value(self, item: dict):
        value = item.get("value")
        if value in (None, ""):
            value = item.get("normalized_value")
        if isinstance(value, str):
            text = value.replace(",", "").strip()
            try:
                return float(text) if "." in text else int(text)
            except ValueError:
                return value
        return value

    def metric_source(self, metric: dict) -> dict:
        source = metric.get("source") or metric.get("evidence") or {}
        if not isinstance(source, dict):
            source = {}
        raw = source.get("raw") if isinstance(source.get("raw"), dict) else {}
        table = raw.get("table") if isinstance(raw.get("table"), dict) else {}
        merged = dict(source)
        for key in ("table_index", "line", "md_line", "pdf_page", "pdf_page_number", "page_number", "quote_text"):
            if merged.get(key) in (None, "") and table.get(key) not in (None, ""):
                merged[key] = table.get(key)
        if merged.get("line") in (None, "") and table.get("line") not in (None, ""):
            merged["line"] = table.get("line")
        if merged.get("quote_text") in (None, "") and metric.get("label"):
            merged["quote_text"] = f"{metric.get('label')}: {metric.get('raw_value') or metric.get('value')}"
        return merged

    def add_evidence(
        self,
        source_type: str,
        source_file: str,
        md_line_start: int | None = None,
        md_line_end: int | None = None,
        pdf_page_number: int | None = None,
        table_index: int | None = None,
        image_path: str | None = None,
        bbox=None,
        quote: str | None = None,
        confidence: str = "medium",
        needs_review: bool = False,
        extra: dict | None = None,
    ) -> str:
        cache_key = (
            source_type,
            source_file,
            md_line_start,
            md_line_end,
            pdf_page_number,
            table_index,
            image_path,
            quote,
        )
        if cache_key in self.evidence_cache:
            return self.evidence_cache[cache_key]
        evidence_id = f"ev_{self.company_id.split('-', 1)[0]}_{self.primary_report.replace('-', '_')}_{len(self.evidence) + 1:06d}"
        field_page = to_int(pdf_page_number)
        anchor_page = self.markdown_anchor_page(md_line_start)
        anchored_page = field_page or anchor_page
        anchored_table_index = self.table_index_for_page(table_index, anchored_page)
        conflict_extra = self.table_conflict_extra(table_index, anchored_page)
        if field_page is not None and anchor_page is not None and field_page != anchor_page:
            conflict_extra["pdf_page_conflict"] = {
                "field_pdf_page": field_page,
                "markdown_anchor_pdf_page": anchor_page,
                "resolution": "structured_page_preferred",
            }
        payload = {
            "evidence_id": evidence_id,
            "company_id": self.company_id,
            "report_id": self.primary_report,
            "source_type": source_type,
            "source_file": source_file,
            "md_line_start": md_line_start,
            "md_line_end": md_line_end,
            "pdf_page_number": anchored_page,
            "table_index": anchored_table_index,
            "image_path": image_path,
            "bbox": bbox,
            "quote": quote,
            "open_pdf_page_url": make_url(self.pdf_template, self.task_id, "page_number", anchored_page),
            "open_source_page_url": make_url(self.source_page_template, self.task_id, "page_number", anchored_page),
            "open_source_table_url": make_url(self.source_table_template, self.task_id, "table_index", anchored_table_index),
            "confidence": confidence,
            "needs_review": bool(needs_review),
        }
        if conflict_extra:
            payload.update(conflict_extra)
        if extra:
            payload.update(extra)
        self.evidence.append(payload)
        self.evidence_cache[cache_key] = evidence_id
        return evidence_id

    def table_evidence(self, table_index: int | None, fallback_line: int | None = None) -> str | None:
        if table_index is None:
            return None
        table = self.table_by_index.get(int(table_index), {})
        line = table.get("line") or fallback_line
        page = self.page_for_markdown_line(line, table.get("pdf_page_number"), prefer_fallback=True)
        quote = table.get("preview")
        return self.add_evidence(
            "table",
            f"reports/{self.primary_report}/report.md",
            md_line_start=line,
            md_line_end=line,
            pdf_page_number=page,
            table_index=int(table_index),
            bbox=table.get("bbox"),
            quote=(quote[:180] if isinstance(quote, str) else None),
            confidence=table.get("source_confidence") or "medium",
            needs_review=page is None,
            extra={"heading": table.get("heading"), "unit": table.get("unit")},
        )

    def metric_evidence(self, metric: dict, metric_key: str) -> str | None:
        source = self.metric_source(metric)
        table_index = source.get("table_index")
        line = source.get("md_line") or source.get("line")
        page = source.get("pdf_page") or source.get("pdf_page_number") or source.get("page_number")
        if page is None and table_index is not None:
            page = (self.table_by_index.get(int(table_index), {}) or {}).get("pdf_page_number")
        page = self.page_for_markdown_line(line, page, prefer_fallback=True)
        quote = source.get("quote_text") or f"{metric_key}={metric.get('raw_value') or metric.get('value') or metric.get('normalized_value')}"
        return self.add_evidence(
            "metric",
            "metrics/three_statements.json",
            md_line_start=line,
            md_line_end=line,
            pdf_page_number=page,
            table_index=table_index,
            quote=quote,
            confidence="high" if page and table_index else "medium",
            needs_review=not (page and table_index),
            extra={
                "metric_key": metric_key,
                "statement_type": metric.get("statement_type"),
                "raw_value": metric.get("raw_value") or metric.get("value"),
                "normalized_value": metric.get("normalized_value") or self.metric_value(metric),
                "normalized_unit": metric.get("unit") or metric.get("currency") or "亿元",
                "source_evidence_id": metric.get("evidence_id") or source.get("evidence_id"),
                "xbrl_concept": metric.get("concept") or source.get("xbrl_tag"),
            },
        )

    def key_metric_evidence(self, item: dict, period: str) -> str | None:
        source = (item.get("sources") or {}).get(str(period)) or self.metric_source(item)
        table_index = source.get("table_index")
        line = source.get("line") or source.get("md_line")
        page = source.get("pdf_page") or source.get("pdf_page_number") or source.get("page_number")
        if table_index is not None:
            page = page or (self.table_by_index.get(int(table_index), {}) or {}).get("pdf_page_number")
        page = self.page_for_markdown_line(line, page, prefer_fallback=True)
        raw_value = (item.get("raw_values") or {}).get(str(period))
        if raw_value in (None, ""):
            raw_value = item.get("raw_value") or item.get("value")
        return self.add_evidence(
            "metric",
            "metrics/key_metrics.json",
            md_line_start=line,
            md_line_end=line,
            pdf_page_number=page,
            table_index=table_index,
            quote=source.get("quote_text") or f"{item.get('name') or item.get('label')} {period}: {raw_value}",
            confidence="high" if table_index or item.get("evidence_id") else "medium",
            needs_review=not (table_index or item.get("evidence_id")),
            extra={
                "metric_key": self.metric_key(item),
                "metric_name": item.get("name") or item.get("label") or item.get("local_name"),
                "period": str(period),
                "source_evidence_id": item.get("evidence_id") or source.get("evidence_id"),
                "xbrl_concept": item.get("concept") or source.get("xbrl_tag"),
            },
        )

    def source_map_entries(self) -> list[dict]:
        payload = read_json(self.report_dir / "qa" / "source_map.json", {}) or {}
        entries = payload.get("entries")
        return entries if isinstance(entries, list) else []

    def build_us_sec_segments(self) -> list[dict]:
        sections_payload = read_json(self.report_dir / "sections.json", {}) or {}
        sections = sections_payload.get("sections") if isinstance(sections_payload.get("sections"), list) else []
        if not sections:
            return []
        profile = profile_for_market("US")
        source_entries = self.source_map_entries()
        source_by_section = {
            str(entry.get("section_id") or ""): entry
            for entry in source_entries
            if entry.get("section_id")
        }
        source_by_path = {
            str(entry.get("local_path") or ""): entry
            for entry in source_entries
            if entry.get("local_path")
        }
        segments: list[dict] = []
        for section in sorted(sections, key=lambda item: int(item.get("section_order") or 0)):
            section_id = str(section.get("section_id") or "").strip()
            filename = str(section.get("file") or "").strip()
            title = str(section.get("section_title") or section_id or filename).strip()
            segment_type = (profile.sec_item_map or {}).get(section_id)
            if not segment_type:
                segment_type = profile_classify_segment_title(f"{section_id} {title}", "US")
            if segment_type == "other":
                continue
            rel_file = f"sections/{filename}" if filename else ""
            section_path = self.report_dir / rel_file if rel_file else None
            section_lines = read_markdown_lines(section_path) if section_path else []
            quote = line_window_quote(section_lines, 1, min(len(section_lines), 80), 320)
            if not quote:
                quote = title[:320]
            source_entry = source_by_section.get(section_id) or source_by_path.get(rel_file) or {}
            evidence_id = self.add_evidence(
                "sec_section",
                f"reports/{self.primary_report}/{rel_file}" if rel_file else f"reports/{self.primary_report}/sections.json",
                md_line_start=1 if section_lines else None,
                md_line_end=min(len(section_lines), 80) if section_lines else None,
                quote=quote,
                confidence="high",
                needs_review=False,
                extra={
                    "section_id": section_id,
                    "sec_item": section_id,
                    "html_anchor": section.get("html_anchor") or source_entry.get("html_anchor"),
                    "source_url": source_entry.get("source_url"),
                    "source_target": source_entry.get("target"),
                    "source_evidence_id": source_entry.get("evidence_id"),
                    "text_hash": section.get("text_hash"),
                    "text_length": section.get("text_length"),
                },
            )
            segment_id = f"seg_{self.company_id.split('-', 1)[0].replace(':', '_')}_{self.primary_report.replace('-', '_')}_{len(segments) + 1:04d}"
            segment = {
                "segment_id": segment_id,
                "segment_type": segment_type,
                "title": title,
                "summary": quote,
                "keywords": profile.topic_aliases.get(segment_type, [])[:8],
                "md_line_start": 1 if section_lines else None,
                "md_line_end": min(len(section_lines), 80) if section_lines else None,
                "pdf_page_start": None,
                "pdf_page_end": None,
                "tables": [],
                "images": [],
                "evidence_ids": [evidence_id],
                "importance": "high" if segment_type in {"business_overview", "risk_factors", "management_discussion", "financial_statements"} else "medium",
                "sec": {
                    "section_id": section_id,
                    "file": filename,
                    "html_anchor": section.get("html_anchor"),
                    "char_start": section.get("char_start"),
                    "char_end": section.get("char_end"),
                },
            }
            segments.append(segment)
            self.segment_ids_by_type[segment_type].append(segment_id)
        return segments

    def build_segments(self) -> list[dict]:
        generic_route = self.company.get("identity_route") == "generic_non_a_share_wiki_import"
        market = self.company.get("market")
        if str(market or "").upper() == "US":
            sec_segments = self.build_us_sec_segments()
            if sec_segments:
                return sec_segments
        topic_aliases = topic_aliases_for_market(market, generic_route)
        enhanced = (self.document.get("content_list_enhanced") or {})
        toc = enhanced.get("toc") or {}
        headings = toc.get("headings") or []
        if not headings:
            headings = self.scan_headings_from_markdown()
        headings = sorted(
            [h for h in headings if h.get("line") and str(h.get("title") or "").strip()],
            key=lambda h: int(h.get("line") or 0),
        )
        segments: list[dict] = []
        seen_ranges = set()
        for index, heading in enumerate(headings):
            title = str(heading.get("title") or "").strip()
            if len(title) > 90:
                continue
            segment_type = classify_market_segment(title, market, generic_route)
            if segment_type == "other":
                continue
            start = int(heading.get("line") or 1)
            next_line = len(self.lines)
            for later in headings[index + 1:]:
                later_line = int(later.get("line") or 0)
                if later_line > start:
                    next_line = later_line - 1
                    break
            if next_line <= start:
                next_line = min(len(self.lines), start + 20)
            if (start, next_line) in seen_ranges:
                continue
            seen_ranges.add((start, next_line))
            table_indexes = [
                int(t.get("table_index"))
                for t in (self.report.get("tables") or [])
                if t.get("table_index") is not None and start <= int(t.get("line") or 0) <= next_line
            ][:40]
            quote = line_window_quote(self.lines, start, next_line, 240)
            if not quote and table_indexes:
                previews = []
                for table_index in table_indexes[:3]:
                    preview = (self.table_by_index.get(table_index, {}) or {}).get("preview")
                    if preview:
                        previews.append(str(preview)[:120])
                quote = " ".join(previews)[:240]
            if not quote and next_line - start < 2:
                continue
            page_start = heading.get("pdf_page_number")
            if page_start is None and start < len(self.page_for_line):
                page_start = self.page_for_line[start]
            page_end = self.page_for_line[next_line] if next_line < len(self.page_for_line) else page_start
            image_paths = []
            for image in (enhanced.get("image_semantic_blocks") or []):
                line = image.get("markdown_line")
                if line and start <= int(line) <= next_line and image.get("image_path"):
                    image_paths.append(image.get("image_path"))
            evidence_id = self.add_evidence(
                "text",
                f"reports/{self.primary_report}/report.md",
                md_line_start=start,
                md_line_end=next_line,
                pdf_page_number=page_start,
                quote=quote,
                confidence="high" if page_start else "medium",
                needs_review=page_start is None,
            )
            segment_id = f"seg_{self.company_id.split('-', 1)[0]}_{self.primary_report.replace('-', '_')}_{len(segments) + 1:04d}"
            segment = {
                "segment_id": segment_id,
                "segment_type": segment_type,
                "title": title,
                "summary": quote,
                "keywords": topic_aliases.get(segment_type, [])[:6],
                "md_line_start": start,
                "md_line_end": next_line,
                "pdf_page_start": page_start,
                "pdf_page_end": page_end,
                "tables": table_indexes,
                "images": image_paths[:20],
                "evidence_ids": [evidence_id],
                "importance": importance_for(segment_type, title, market),
            }
            segments.append(segment)
            self.segment_ids_by_type[segment_type].append(segment_id)
            if len(segments) >= 240:
                break
        return segments

    def scan_headings_from_markdown(self) -> list[dict]:
        headings = []
        for line_no, raw in enumerate(self.lines, start=1):
            line = raw.strip()
            if not line.startswith("#"):
                continue
            title = re.sub(r"^#+\s*", "", line).strip()
            headings.append({"title": title, "line": line_no, "level": len(line) - len(line.lstrip("#")), "pdf_page_number": self.page_for_line[line_no]})
        return headings

    def build_subject_profile(self, segments: list[dict]) -> dict:
        profile = profile_for_market(
            self.company.get("market"),
            self.company.get("identity_route") == "generic_non_a_share_wiki_import",
        )
        identity = {
            "company_id": self.company_id,
            "market": self.company.get("market") or profile.market,
            "stock_code": self.company.get("stock_code"),
            "ticker": self.company.get("ticker"),
            "exchange": self.company.get("exchange"),
            "company_short_name": self.company.get("company_short_name"),
            "company_full_name": self.company.get("company_full_name"),
            "company_name": self.company.get("company_name"),
            "aliases": self.company.get("aliases") or [],
        }
        report_info = ((self.company.get("reports") or [{}])[0]) | (self.report.get("report") or {})
        by_type = {segment["segment_type"]: segment for segment in segments}
        profile = {
            "schema_version": 1,
            "rule_version": RULE_VERSION,
            "generated_at": self.generated_at,
            "identity": identity,
            "semantic_profile": {
                "market": profile.market,
                "source_language": profile.source_language,
                "output_language": profile.output_language,
                "llm_focus": profile.llm_focus,
                "guardrails": profile.llm_guardrails,
            },
            "primary_report": {
                "report_id": self.primary_report,
                "report_year": report_info.get("report_year"),
                "report_kind": report_info.get("report_kind"),
                "industry_profile": report_info.get("industry_profile"),
                "source_filename": report_info.get("source_filename"),
                "task_id": self.task_id,
            },
            "business_scope": {
                "summary": (by_type.get("business_overview") or {}).get("summary"),
                "segment_ids": self.segment_ids_by_type.get("business_overview", [])[:10],
            },
            "industry_context": {
                "summary": (by_type.get("industry_analysis") or {}).get("summary"),
                "segment_ids": self.segment_ids_by_type.get("industry_analysis", [])[:10],
            },
            "strategy": {
                "summary": (by_type.get("management_discussion") or {}).get("summary"),
                "segment_ids": self.segment_ids_by_type.get("management_discussion", [])[:10],
            },
            "organization": {
                "segment_ids": self.segment_ids_by_type.get("company_profile", [])[:10],
            },
            "capital_market": {
                "segment_ids": (self.segment_ids_by_type.get("shareholders", [])[:10]
                                + self.segment_ids_by_type.get("dividend", [])[:10]),
            },
            "audit_and_governance": {
                "segment_ids": self.segment_ids_by_type.get("corporate_governance", [])[:10],
            },
            "quality": {
                "financial_overall_status": (self.report.get("quality_summary") or {}).get("financial_overall_status"),
                "table_count": (self.report.get("quality_summary") or {}).get("table_count"),
                "image_ref_count": (self.report.get("quality_summary") or {}).get("image_ref_count"),
            },
        }
        return profile

    def build_facts_and_relations(self) -> tuple[list[dict], list[dict]]:
        facts: list[dict] = []
        relations: list[dict] = []

        def add_fact(fact_type, subject, predicate, obj, evidence_ids, value=None, unit=None, period=None, dimensions=None, confidence="high", needs_review=False):
            fact_id = f"fact_{self.company_id.split('-', 1)[0]}_{self.primary_report.replace('-', '_')}_{len(facts) + 1:06d}"
            fact = {
                "fact_id": fact_id,
                "fact_type": fact_type,
                "subject": subject,
                "predicate": predicate,
                "object": obj,
                "value": value,
                "unit": unit,
                "period": str(period) if period is not None else None,
                "dimensions": dimensions or {},
                "evidence_ids": [eid for eid in evidence_ids if eid],
                "confidence": confidence,
                "needs_review": bool(needs_review or not evidence_ids),
            }
            facts.append(fact)
            self.fact_ids_by_topic[fact_type].append(fact_id)
            return fact_id

        def add_relation(relation_type, source_name, target_name, evidence_ids, period=None, properties=None, confidence="high", needs_review=False):
            relation_id = f"rel_{self.company_id.split('-', 1)[0]}_{self.primary_report.replace('-', '_')}_{len(relations) + 1:06d}"
            relations.append({
                "relation_id": relation_id,
                "relation_type": relation_type,
                "source_entity_id": f"company:{self.company.get('stock_code')}",
                "source_entity_name": source_name,
                "target_entity_id": slug(target_name),
                "target_entity_name": target_name,
                "period": str(period) if period is not None else None,
                "properties": properties or {},
                "evidence_ids": [eid for eid in evidence_ids if eid],
                "confidence": confidence,
                "needs_review": bool(needs_review or not evidence_ids),
            })

        company_subject = {
            "type": "company",
            "id": f"company:{self.company.get('stock_code')}",
            "name": self.company.get("company_full_name") or self.company.get("company_short_name") or self.company_id,
        }
        identity_evidence = self.add_evidence(
            "text",
            "company.json",
            quote=f"{self.company.get('stock_code')} {self.company.get('company_short_name')} {self.company.get('company_full_name')}",
            confidence="high",
        )
        for key, value in [
            ("stock_code", self.company.get("stock_code")),
            ("exchange", self.company.get("exchange")),
            ("company_short_name", self.company.get("company_short_name")),
            ("company_full_name", self.company.get("company_full_name")),
        ]:
            if value:
                add_fact(
                    "identity_fact",
                    company_subject,
                    "has_identity_attribute",
                    {"type": "text", "name": key},
                    [identity_evidence],
                    value=value,
                    period=(self.report.get("report") or {}).get("report_year"),
                )

        key_metrics = self.metric_payload("key_metrics.json")
        for item in self.metric_items(key_metrics):
            metric_key = self.metric_key(item)
            if metric_key not in CORE_KEY_METRICS:
                continue
            values = item.get("values") or {}
            if not values:
                period = self.metric_period(item)
                if period:
                    values = {period: self.metric_value(item)}
            for period, value in sorted(values.items(), reverse=True):
                evidence_id = self.key_metric_evidence(item, str(period))
                raw_value = (item.get("raw_values") or {}).get(str(period))
                if raw_value in (None, ""):
                    raw_value = item.get("raw_value") or item.get("value")
                fact_id = add_fact(
                    "financial_metric_fact",
                    company_subject,
                    "reported",
                    {"type": "number", "name": item.get("name") or item.get("label") or item.get("local_name"), "metric_key": metric_key, "raw_value": raw_value},
                    [evidence_id],
                    value=value,
                    unit=item.get("unit") or item.get("currency") or None,
                    period=period,
                    dimensions={
                        "source": self.metric_source_file(key_metrics, "metrics/key_metrics.json"),
                        "scale": item.get("scale"),
                        "statement_type": item.get("statement_type"),
                        "concept": item.get("concept"),
                    },
                    confidence="high" if evidence_id else "medium",
                    needs_review=evidence_id is None,
                )
                self.fact_ids_by_topic[metric_key].append(fact_id)
                add_relation(
                    "company_reported_metric",
                    company_subject["name"],
                    item.get("name") or item.get("label") or item.get("local_name") or metric_key,
                    [evidence_id],
                    period=period,
                    properties={"metric_key": metric_key, "value": value, "raw_value": raw_value},
                    needs_review=evidence_id is None,
                )

        three = self.metric_payload("three_statements.json")
        for metric in self.metric_items(three):
            metric_key = self.metric_key(metric)
            if metric_key not in CORE_THREE_STATEMENT_METRICS:
                continue
            evidence_id = self.metric_evidence(metric, metric_key)
            fact_id = add_fact(
                "financial_metric_fact",
                company_subject,
                "reported",
                {"type": "number", "name": metric.get("name") or metric.get("label") or metric_key, "metric_key": metric_key, "raw_value": metric.get("raw_value") or metric.get("value")},
                [evidence_id],
                value=metric.get("normalized_value") if metric.get("normalized_value") not in (None, "") else self.metric_value(metric),
                unit=metric.get("unit") or metric.get("currency") or "亿元",
                period=metric.get("period") or metric.get("period_key") or metric.get("period_end") or (metric.get("source") or {}).get("period") or (self.report.get("report") or {}).get("report_year"),
                dimensions={
                    "source": self.metric_source_file(three, "metrics/three_statements.json"),
                    "statement_type": metric.get("statement_type"),
                    "base_scale": metric.get("base_scale"),
                    "unit_hint": metric.get("unit_hint"),
                    "source_kind": (metric.get("source") or {}).get("source_kind"),
                    "concept": metric.get("concept"),
                },
                confidence="high" if evidence_id else "medium",
                needs_review=evidence_id is None,
            )
            self.fact_ids_by_topic[metric_key].append(fact_id)

        return facts, relations

    def build_claims(self, facts: list[dict]) -> list[dict]:
        claims: list[dict] = []
        by_metric_period: dict[tuple[str, str], dict] = {}
        for fact in facts:
            obj = fact.get("object") or {}
            metric_key = obj.get("metric_key")
            period = fact.get("period")
            if metric_key and period and (metric_key, str(period)) not in by_metric_period:
                by_metric_period[(metric_key, str(period))] = fact

        key_metrics = self.metric_payload("key_metrics.json")
        report_year = str((self.report.get("report") or {}).get("report_year") or "")
        prior_periods = period_candidates(report_year)
        for item in self.metric_items(key_metrics):
            metric_key = self.metric_key(item)
            if metric_key not in CORE_KEY_METRICS:
                continue
            values = item.get("values") or {}
            if not values:
                period = self.metric_period(item)
                if period:
                    values = {str(period): self.metric_value(item)}
            prior_period = next((period for period in prior_periods if period in values), "")
            if report_year not in values or not prior_period:
                continue
            current = values.get(report_year)
            previous = values.get(prior_period)
            if not isinstance(current, (int, float)) or not isinstance(previous, (int, float)) or previous == 0:
                continue
            change = (current - previous) / abs(previous)
            direction = "增长" if change > 0 else "下降" if change < 0 else "持平"
            current_fact = by_metric_period.get((metric_key, report_year))
            previous_fact = by_metric_period.get((metric_key, prior_period))
            evidence_ids = []
            supporting = []
            for fact in (current_fact, previous_fact):
                if fact:
                    supporting.append(fact["fact_id"])
                    evidence_ids.extend(fact.get("evidence_ids") or [])
            if len(supporting) < 2:
                continue
            claim_id = f"claim_{self.company_id.split('-', 1)[0]}_{self.primary_report.replace('-', '_')}_{len(claims) + 1:06d}"
            claims.append({
                "claim_id": claim_id,
                "claim_type": "performance_claim",
                "statement": f"{item.get('name') or item.get('label') or metric_key}在{report_year}年较{prior_period}年{direction}{abs(change) * 100:.2f}%。",
                "stance": "positive" if change > 0 else "negative" if change < 0 else "neutral",
                "strength": "strong" if abs(change) >= 0.2 else "moderate" if abs(change) >= 0.05 else "weak",
                "supporting_facts": supporting,
                "contradicting_facts": [],
                "evidence_ids": sorted(set(evidence_ids)),
                "calculation": {
                    "formula": "(current - previous) / abs(previous)",
                    "inputs": [
                        {"period": report_year, "value": current},
                        {"period": prior_period, "value": previous},
                    ],
                },
                "confidence": "high",
                "needs_review": False,
            })
            self.claim_ids_by_topic[metric_key].append(claim_id)
            self.claim_ids_by_topic["key_financials"].append(claim_id)
        return claims

    def build_retrieval_index(self, segments: list[dict], facts: list[dict], claims: list[dict]) -> dict:
        topics = []
        aliases_by_topic = topic_aliases_for_market(self.company.get("market"), self.company.get("identity_route") == "generic_non_a_share_wiki_import")
        for segment_type, aliases in aliases_by_topic.items():
            segment_ids = self.segment_ids_by_type.get(segment_type, [])
            fact_ids = []
            if segment_type == "key_financials":
                fact_ids = self.fact_ids_by_topic.get("financial_metric_fact", [])[:80]
            claim_ids = self.claim_ids_by_topic.get(segment_type, [])[:40]
            if not segment_ids and not fact_ids and not claim_ids:
                continue
            topics.append({
                "topic": aliases[0],
                "topic_type": segment_type,
                "query_aliases": aliases,
                "priority_files": [
                    "semantic/subject_profile.json",
                    "semantic/segments.json",
                    "semantic/facts.json",
                    "semantic/relations.json",
                    "semantic/claims.json",
                    "metrics/key_metrics.json",
                    "evidence/evidence_index.json",
                ],
                "segment_ids": segment_ids[:30],
                "fact_ids": fact_ids,
                "claim_ids": claim_ids,
                "evidence_ids": sorted({
                    eid
                    for segment in segments
                    if segment["segment_id"] in segment_ids[:30]
                    for eid in (segment.get("evidence_ids") or [])
                })[:80],
            })
        topics.append({
            "topic": "LLM语义增强候选",
            "topic_type": "llm_semantic",
            "query_aliases": [
                "战略归纳",
                "经营变化",
                "风险归纳",
                "重大事项",
                "业务画像",
                "LLM semantic enrichment",
                "business profile",
                "risk summary",
            ],
            "priority_files": [
                f"semantic/llm/{self.primary_report}/business_profile.json",
                f"semantic/llm/{self.primary_report}/risks.json",
                f"semantic/llm/{self.primary_report}/events.json",
                f"semantic/llm/{self.primary_report}/claims.json",
                f"semantic/llm/{self.primary_report}/review_queue.json",
                "semantic/retrieval_index.json",
                "semantic/segments.json",
                "semantic/evidence_semantic.json",
                f"reports/{self.primary_report}/report.md",
            ],
            "usage_policy": "LLM 层只作为召回和分析候选；正式回答必须回链 segment/evidence/report.md，不得以 LLM 层作为财务数值来源。",
        })
        metric_aliases = metric_aliases_for_market(self.company.get("market"))
        return {
            "schema_version": 1,
            "rule_version": RULE_VERSION,
            "generated_at": self.generated_at,
            "company_id": self.company_id,
            "market": self.company.get("market") or "CN",
            "primary_report_id": self.primary_report,
            "topics": topics,
            "metric_aliases": metric_aliases,
            "audit_entrypoints": {
                "report_md": f"reports/{self.primary_report}/report.md",
                "report_json": f"reports/{self.primary_report}/report.json",
                "document_full": f"reports/{self.primary_report}/document_full.json",
                "pdf_page_url_template": self.pdf_template,
                "source_page_url_template": self.source_page_template,
                "source_table_url_template": self.source_table_template,
            },
            "recommended_read_order": [
                "semantic/retrieval_index.json",
                "semantic/subject_profile.json",
                f"semantic/llm/{self.primary_report}/business_profile.json",
                f"semantic/llm/{self.primary_report}/risks.json",
                f"semantic/llm/{self.primary_report}/events.json",
                f"semantic/llm/{self.primary_report}/claims.json",
                "semantic/facts.json",
                "semantic/relations.json",
                "semantic/claims.json",
                "semantic/segments.json",
                "evidence/evidence_index.json",
                f"reports/{self.primary_report}/report.md",
            ],
        }

    def build_image_evidence(self) -> list[dict]:
        enhanced = self.document.get("content_list_enhanced") or {}
        image_manifest = []
        for image in enhanced.get("image_semantic_blocks") or []:
            path = image.get("image_path")
            if not path:
                continue
            image_page = self.page_for_markdown_line(
                image.get("markdown_line"),
                image.get("pdf_page_number"),
                prefer_fallback=True,
            )
            evidence_id = self.add_evidence(
                "image",
                f"reports/{self.primary_report}/{path}",
                md_line_start=image.get("markdown_line"),
                md_line_end=image.get("markdown_line"),
                pdf_page_number=image_page,
                image_path=path,
                bbox=image.get("bbox"),
                quote=image.get("display_preview") or image.get("recognized_preview") or None,
                confidence=image.get("confidence") or "medium",
                needs_review=(image.get("actionability") in {"needs_ocr", "needs_vlm"}),
                extra={
                    "semantic_kind": image.get("semantic_kind"),
                    "actionability": image.get("actionability"),
                    "caption": image.get("caption"),
                },
            )
            image_manifest.append({
                "image_path": path,
                "evidence_id": evidence_id,
                "pdf_page_number": image_page,
                "semantic_kind": image.get("semantic_kind"),
                "actionability": image.get("actionability"),
                "needs_review": image.get("actionability") in {"needs_ocr", "needs_vlm"},
            })
        return image_manifest

    def input_fingerprints(self) -> dict:
        return {
            "company_json_sha256": sha256_file(self.company_dir / "company.json"),
            "report_md_sha256": sha256_file(self.report_md_path),
            "report_json_sha256": sha256_file(self.report_json_path),
            "document_full_sha256": sha256_file(self.document_full_path),
            "artifact_manifest_sha256": sha256_file(self.artifact_manifest_path),
        }

    def fresh_existing_result(self) -> dict | None:
        missing = [name for name in REQUIRED_RULE_OUTPUTS if not (self.semantic_dir / name).is_file()]
        if missing:
            return None
        log = read_json(self.semantic_dir / "extraction_log.json", {}) or {}
        inputs = log.get("inputs") if isinstance(log.get("inputs"), dict) else {}
        counts = log.get("counts") if isinstance(log.get("counts"), dict) else {}
        if not inputs or int(counts.get("segments") or 0) <= 0 or int(counts.get("evidence") or 0) <= 0:
            return None
        current_inputs = self.input_fingerprints()
        if any(inputs.get(key) != value for key, value in current_inputs.items()):
            return None
        return {
            "company_id": self.company_id,
            "status": "skipped",
            "skipped_existing": True,
            "counts": counts,
            "quality": log.get("quality") or {},
        }

    def build_note_links(self) -> dict:
        enhanced = self.document.get("content_list_enhanced") or {}
        financial_note_links = (enhanced.get("financial_note_links") or {})
        source_links = financial_note_links.get("links") or []
        links = []
        by_statement_item: dict[str, list[str]] = defaultdict(list)
        by_note_ref: dict[str, list[str]] = defaultdict(list)
        by_note_title: dict[str, list[str]] = defaultdict(list)
        amount_check_summary = defaultdict(int)
        confidence_summary = defaultdict(int)
        manual_review_required = []

        for index, source in enumerate(source_links, start=1):
            statement_item = source.get("statement_item") or source.get("statement_alias") or ""
            note_title = re.sub(r"^#+\s*", "", str(source.get("note_title") or "")).strip()
            note_ref = source.get("note_ref") or source.get("statement_note_ref")
            confidence = source.get("confidence") or "medium"
            amount_check = dict(source.get("amount_check") or {})
            amount_status = (amount_check.get("status") or "unknown")
            secondary_check = None
            if amount_status == "unverified":
                secondary_check = secondary_amount_check(amount_check)
                if secondary_check:
                    amount_status = secondary_check["status"]
                    amount_check["status"] = amount_status
                    amount_check["confidence"] = secondary_check["confidence"]
                    amount_check["matched"] = secondary_check["matched"]
            needs_review = confidence == "low" or amount_status in {"mismatch", "failed"}
            statement_page = source.get("statement_page_number")
            note_page = source.get("note_page_number")
            statement_table_index = source.get("statement_table_index")

            statement_evidence_id = self.add_evidence(
                "table" if statement_table_index else "text",
                f"reports/{self.primary_report}/report.md",
                md_line_start=source.get("statement_line"),
                md_line_end=source.get("statement_line"),
                pdf_page_number=statement_page,
                table_index=statement_table_index,
                quote=f"报表项目：{statement_item}" if statement_item else None,
                confidence=confidence,
                needs_review=statement_page is None,
                extra={
                    "note_link_role": "statement_item",
                    "statement_item": statement_item,
                    "statement_note_ref": source.get("statement_note_ref"),
                },
            )
            note_evidence_id = self.add_evidence(
                "text",
                f"reports/{self.primary_report}/report.md",
                md_line_start=source.get("note_line"),
                md_line_end=source.get("note_line"),
                pdf_page_number=note_page,
                quote=f"附注：{note_title or note_ref or statement_item}",
                confidence=confidence,
                needs_review=note_page is None,
                extra={
                    "note_link_role": "note",
                    "note_ref": note_ref,
                    "note_title": note_title,
                    "note_scope": source.get("note_scope"),
                },
            )
            note_link_id = f"note_{self.company_id.split('-', 1)[0]}_{self.primary_report.replace('-', '_')}_{index:05d}"
            link = {
                "note_link_id": note_link_id,
                "company_id": self.company_id,
                "report_id": self.primary_report,
                "statement": {
                    "item": statement_item,
                    "alias": source.get("statement_alias"),
                    "line": source.get("statement_line"),
                    "table_index": statement_table_index,
                    "note_ref": source.get("statement_note_ref"),
                    "note_ref_raw": source.get("statement_note_ref_raw"),
                    "pdf_page_number": statement_page,
                    "open_pdf_page_url": make_url(self.pdf_template, self.task_id, "page_number", statement_page),
                    "open_source_page_url": make_url(self.source_page_template, self.task_id, "page_number", statement_page),
                    "open_source_table_url": make_url(self.source_table_template, self.task_id, "table_index", statement_table_index),
                },
                "note": {
                    "title": note_title,
                    "alias": source.get("note_alias"),
                    "ref": note_ref,
                    "scope": source.get("note_scope"),
                    "line": source.get("note_line"),
                    "pdf_page_number": note_page,
                    "open_pdf_page_url": make_url(self.pdf_template, self.task_id, "page_number", note_page),
                    "open_source_page_url": make_url(self.source_page_template, self.task_id, "page_number", note_page),
                },
                "linkage": {
                    "method": source.get("method"),
                    "confidence": confidence,
                    "precision_level": source.get("precision_level"),
                    "evidence": source.get("evidence") or [],
                    "amount_check": {
                        "status": amount_status,
                        "confidence": amount_check.get("confidence"),
                        "matched": amount_check.get("matched"),
                        "secondary_check": secondary_check,
                        "statement_value_count": len(amount_check.get("statement_values") or []),
                        "note_candidate_count": len(amount_check.get("note_candidates") or []),
                    },
                },
                "evidence_ids": [statement_evidence_id, note_evidence_id],
                "needs_review": bool(needs_review),
            }
            links.append(link)
            if statement_item:
                by_statement_item[statement_item].append(note_link_id)
            if note_ref:
                by_note_ref[str(note_ref)].append(note_link_id)
            if note_title:
                by_note_title[note_title].append(note_link_id)
            amount_check_summary[amount_status] += 1
            confidence_summary[confidence] += 1
            if needs_review:
                manual_review_required.append(note_link_id)

        return {
            "schema_version": 1,
            "rule_version": RULE_VERSION,
            "generated_at": self.generated_at,
            "company_id": self.company_id,
            "report_id": self.primary_report,
            "source": "document_full.content_list_enhanced.financial_note_links.links",
            "note_link_count": len(links),
            "links": links,
            "indexes": {
                "by_statement_item": dict(by_statement_item),
                "by_note_ref": dict(by_note_ref),
                "by_note_title": dict(by_note_title),
            },
            "summary": {
                "amount_check": dict(amount_check_summary),
                "confidence": dict(confidence_summary),
                "manual_review_required": manual_review_required,
            },
        }

    def _node_with_urls(
        self,
        *,
        kind: str,
        name: str | None = None,
        title: str | None = None,
        line: int | None = None,
        pdf_page_number: int | None = None,
        table_index: int | None = None,
        note_ref: str | None = None,
        note_title: str | None = None,
        heading: str | None = None,
        preview: str | None = None,
        unit: str | None = None,
        extra: dict | None = None,
    ) -> dict:
        node = {
            "kind": kind,
            "name": name,
            "title": title,
            "note_ref": note_ref,
            "note_title": note_title,
            "line": line,
            "md_line": line,
            "pdf_page_number": pdf_page_number,
            "table_index": table_index,
            "heading": heading,
            "preview": trim_preview(preview),
            "unit": unit,
            "open_pdf_page_url": make_url(self.pdf_template, self.task_id, "page_number", pdf_page_number),
            "open_source_page_url": make_url(self.source_page_template, self.task_id, "page_number", pdf_page_number),
            "open_source_table_url": make_url(self.source_table_template, self.task_id, "table_index", table_index),
        }
        if extra:
            node.update(extra)
        return {key: value for key, value in node.items() if value not in (None, "", [])}

    def _nearest_heading_before(self, line: int | None, lower_bound: int | None = None) -> dict:
        if not line:
            return {}
        start = max(1, min(int(line), len(self.lines)))
        lower = max(1, int(lower_bound or 1))
        for line_no in range(start, lower - 1, -1):
            raw = self.lines[line_no - 1].strip()
            if not raw.startswith("#"):
                continue
            title = re.sub(r"^#+\s*", "", raw).strip()
            if title:
                return {
                    "title": title,
                    "line": line_no,
                    "pdf_page_number": self.page_for_line[line_no] if line_no < len(self.page_for_line) else None,
                }
        return {}

    def _note_section_tables(self, note_line: int | None, next_note_line: int | None = None) -> list[dict]:
        if not note_line:
            return []
        heading_upper = self._next_numbered_note_heading_line(note_line)
        candidates = [value for value in (next_note_line, heading_upper) if value and int(value) > int(note_line)]
        upper = min(int(value) for value in candidates) if candidates else min(len(self.lines), int(note_line) + 180)
        tables = []
        for table in self.table_by_index.values():
            table_line = table.get("line")
            if table_line is None:
                continue
            table_line = int(table_line)
            if int(note_line) < table_line < upper:
                tables.append(table)
        tables.sort(key=lambda item: (int(item.get("line") or 0), int(item.get("table_index") or 0)))
        return tables[:24]

    def _next_numbered_note_heading_line(self, note_line: int | None) -> int | None:
        if not note_line:
            return None
        current_number = leading_note_number(self.lines[int(note_line) - 1])
        for line_no in range(int(note_line) + 1, len(self.lines) + 1):
            raw = self.lines[line_no - 1].strip()
            if not raw.startswith("#"):
                continue
            title = re.sub(r"^#+\s*", "", raw).strip()
            candidate_number = leading_note_number(title)
            if current_number is not None:
                if candidate_number is not None and candidate_number > current_number:
                    return line_no
                continue
            if candidate_number is not None or re.match(r"^\d+[、.．]\s*", title):
                return line_no
        return None

    def _semantic_relation(self, statement_item: str, title: str | None, preview: str | None) -> str:
        text = f"{statement_item} {title or ''} {preview or ''}"
        if "减值准备" in text or "减值损失" in text:
            return "impairment_detail"
        if "账面原值" in text or "被投资单位名称" in text or "构成" in text:
            return "composition_detail"
        if any(keyword in text for keyword in ("期初余额", "本期增加", "本期减少", "期末余额")):
            return "movement_detail"
        return "detail_disclosure"

    def build_document_links(self, note_links: dict) -> dict:
        links = note_links.get("links") or []
        note_lines = sorted({
            int((link.get("note") or {}).get("line"))
            for link in links
            if (link.get("note") or {}).get("line")
        })

        def next_note_line(line: int | None) -> int | None:
            if not line:
                return None
            return next((candidate for candidate in note_lines if candidate > int(line)), None)

        document_links = []
        indexes = {
            "by_source_name": defaultdict(list),
            "by_target_title": defaultdict(list),
            "by_note_link_id": defaultdict(list),
            "by_link_type": defaultdict(list),
        }
        seen: set[tuple] = set()

        def append_link(link_type: str, source_node: dict, target_node: dict, note_link: dict, relation: dict) -> None:
            key = (
                link_type,
                source_node.get("kind"),
                source_node.get("name") or source_node.get("title"),
                source_node.get("line"),
                source_node.get("table_index"),
                target_node.get("kind"),
                target_node.get("title") or target_node.get("name"),
                target_node.get("line"),
                target_node.get("table_index"),
            )
            if key in seen:
                return
            seen.add(key)
            document_link_id = f"doclink_{self.company_id.split('-', 1)[0]}_{self.primary_report.replace('-', '_')}_{len(document_links) + 1:06d}"
            confidence = relation.get("confidence") or "medium"
            payload = {
                "document_link_id": document_link_id,
                "company_id": self.company_id,
                "report_id": self.primary_report,
                "link_type": link_type,
                "source_layer": "rule",
                "source": source_node,
                "target": target_node,
                "relation": relation,
                "evidence_ids": note_link.get("evidence_ids") or [],
                "confidence": confidence,
                "needs_review": bool(note_link.get("needs_review")) or confidence == "low",
            }
            document_links.append(payload)
            source_name = source_node.get("name") or source_node.get("title")
            target_title = target_node.get("title") or target_node.get("name") or target_node.get("note_title")
            note_link_id = note_link.get("note_link_id")
            if source_name:
                indexes["by_source_name"][source_name].append(document_link_id)
            if target_title:
                indexes["by_target_title"][target_title].append(document_link_id)
            if note_link_id:
                indexes["by_note_link_id"][note_link_id].append(document_link_id)
            indexes["by_link_type"][link_type].append(document_link_id)

        for note_link in links:
            statement = note_link.get("statement") or {}
            note = note_link.get("note") or {}
            linkage = note_link.get("linkage") or {}
            amount_check = linkage.get("amount_check") or {}
            statement_item = str(statement.get("item") or statement.get("alias") or "").strip()
            note_title = str(note.get("title") or note.get("alias") or statement_item).strip()
            note_line = note.get("line")

            statement_node = self._node_with_urls(
                kind="statement_item",
                name=statement_item,
                title=statement_item,
                line=statement.get("line"),
                pdf_page_number=statement.get("pdf_page_number"),
                table_index=statement.get("table_index"),
                note_ref=statement.get("note_ref"),
                extra={"note_ref_raw": statement.get("note_ref_raw")},
            )
            note_node = self._node_with_urls(
                kind="note",
                name=note_title,
                title=note_title,
                line=note_line,
                pdf_page_number=note.get("pdf_page_number"),
                note_ref=note.get("ref"),
                note_title=note_title,
                extra={"note_scope": note.get("scope")},
            )
            base_relation = {
                "method": linkage.get("method") or "rule_note_link",
                "source_note_link_id": note_link.get("note_link_id"),
                "confidence": linkage.get("confidence") or "medium",
                "precision_level": linkage.get("precision_level"),
                "amount_check_status": amount_check.get("status"),
                "amount_check_confidence": amount_check.get("confidence"),
                "semantic_relation": "main_statement_to_note",
                "llm_allowed": True,
            }
            append_link("statement_item_to_note", statement_node, note_node, note_link, base_relation)

            for table in self._note_section_tables(note_line, next_note_line(note_line)):
                table_index = table.get("table_index")
                table_line = table.get("line")
                heading = self._nearest_heading_before(table_line, note_line)
                table_title = heading.get("title") or note_title
                table_node = self._node_with_urls(
                    kind="note_table",
                    name=table_title,
                    title=table_title,
                    line=table_line,
                    pdf_page_number=table.get("pdf_page_number"),
                    table_index=table_index,
                    note_ref=note.get("ref"),
                    note_title=note_title,
                    heading=table.get("heading"),
                    preview=table.get("preview"),
                    unit=table.get("unit"),
                    extra={
                        "heading_line": heading.get("line"),
                        "source_confidence": table.get("source_confidence"),
                    },
                )
                semantic_relation = self._semantic_relation(statement_item, table_title, table.get("preview"))
                note_to_table_relation = {
                    "method": "rule_note_section_table_window",
                    "source_note_link_id": note_link.get("note_link_id"),
                    "confidence": "high" if table.get("pdf_page_number") == note.get("pdf_page_number") else "medium",
                    "semantic_relation": semantic_relation,
                    "llm_allowed": True,
                }
                append_link("note_to_table", note_node, table_node, note_link, note_to_table_relation)
                direct_relation = dict(note_to_table_relation)
                direct_relation["method"] = "rule_statement_to_note_table_via_note_link"
                if not target_conflicts_statement_item(statement_item, table_title, table.get("preview")):
                    append_link("statement_item_to_note_table", statement_node, table_node, note_link, direct_relation)

        return {
            "schema_version": 1,
            "rule_version": RULE_VERSION,
            "generated_at": self.generated_at,
            "company_id": self.company_id,
            "report_id": self.primary_report,
            "source": "semantic/note_links.json + reports/<report_id>/report.json tables",
            "design_note": "This layer stores document navigation edges only. It does not extract or normalize numeric financial data.",
            "document_link_count": len(document_links),
            "links": document_links,
            "indexes": {key: dict(value) for key, value in indexes.items()},
            "summary": {
                "by_link_type": {key: len(value) for key, value in indexes["by_link_type"].items()},
                "needs_review": [link["document_link_id"] for link in document_links if link.get("needs_review")],
            },
        }

    def attach_document_links_to_retrieval_index(self, retrieval_index: dict, document_links: dict) -> None:
        if not document_links.get("document_link_count"):
            return
        retrieval_index.setdefault("topics", []).append({
            "topic": "文档跳转关系",
            "topic_type": "document_links",
            "query_aliases": ["文档跳转", "证据跳转", "附注明细", "报表项目明细", "主表到附注", "note table"],
            "priority_files": [
                "semantic/document_links.json",
                "semantic/note_links.json",
                "semantic/retrieval_index.json",
                "semantic/evidence_semantic.json",
                f"reports/{self.primary_report}/report.md",
                f"reports/{self.primary_report}/report.json",
            ],
            "document_link_ids": [link["document_link_id"] for link in document_links.get("links", [])[:120]],
            "evidence_ids": sorted({
                eid
                for link in document_links.get("links", [])[:120]
                for eid in link.get("evidence_ids", [])
            }),
        })
        order = retrieval_index.setdefault("recommended_read_order", [])
        for path in ("semantic/document_links.json", "semantic/note_links.json"):
            if path not in order:
                insert_at = 1 if path == "semantic/document_links.json" else min(6, len(order))
                order.insert(insert_at, path)

    def extraction_log(self, segments, facts, relations, claims, note_links, document_links=None) -> dict:
        evidence_count = len(self.evidence)
        evidence_ids = {ev["evidence_id"] for ev in self.evidence}
        facts_with_evidence = sum(1 for fact in facts if set(fact.get("evidence_ids") or []) & evidence_ids)
        claims_with_evidence = sum(1 for claim in claims if set(claim.get("evidence_ids") or []) & evidence_ids)
        numeric_facts = [fact for fact in facts if fact.get("fact_type") == "financial_metric_fact"]
        numeric_with_metric = sum(
            1 for fact in numeric_facts
            if any((next((ev for ev in self.evidence if ev["evidence_id"] == eid), {}) or {}).get("source_type") == "metric"
                   for eid in fact.get("evidence_ids") or [])
        )
        return {
            "schema_version": 1,
            "rule_version": RULE_VERSION,
            "generated_at": self.generated_at,
            "inputs": self.input_fingerprints(),
            "counts": {
                "segments": len(segments),
                "facts": len(facts),
                "relations": len(relations),
                "claims": len(claims),
                "note_links": note_links.get("note_link_count", 0),
                "document_links": (document_links or {}).get("document_link_count", 0),
                "evidence": evidence_count,
            },
            "quality": {
                "facts_with_evidence_ratio": round(facts_with_evidence / len(facts), 6) if facts else 1.0,
                "claims_with_evidence_ratio": round(claims_with_evidence / len(claims), 6) if claims else 1.0,
                "numeric_facts_with_metric_source_ratio": round(numeric_with_metric / len(numeric_facts), 6) if numeric_facts else 1.0,
            },
            "warnings": [] if note_links.get("note_link_count", 0) else ["未发现可结构化的附注对应关系。"],
            "manual_review_required": [
                ev["evidence_id"] for ev in self.evidence if ev.get("needs_review")
            ][:200],
        }

    def run(self) -> dict:
        self.semantic_dir.mkdir(parents=True, exist_ok=True)
        segments = self.build_segments()
        subject_profile = self.build_subject_profile(segments)
        facts, relations = self.build_facts_and_relations()
        image_manifest = self.build_image_evidence()
        note_links = self.build_note_links()
        document_links = self.build_document_links(note_links)
        claims = self.build_claims(facts)
        retrieval_index = self.build_retrieval_index(segments, facts, claims)
        if note_links.get("note_link_count"):
            retrieval_index.setdefault("topics", []).append({
                "topic": "附注对应关系",
                "topic_type": "financial_note_links",
                "query_aliases": topic_aliases_for_market(
                    self.company.get("market"),
                    self.company.get("identity_route") == "generic_non_a_share_wiki_import",
                ).get("financial_note_links", TOPIC_ALIASES["financial_note_links"]),
                "priority_files": [
                    "semantic/note_links.json",
                    "semantic/retrieval_index.json",
                    "semantic/facts.json",
                    "semantic/evidence_semantic.json",
                    f"reports/{self.primary_report}/report.md",
                    f"reports/{self.primary_report}/document_full.json",
                ],
                "note_link_ids": [link["note_link_id"] for link in note_links.get("links", [])[:80]],
                "evidence_ids": sorted({
                    eid
                    for link in note_links.get("links", [])[:80]
                    for eid in link.get("evidence_ids", [])
                }),
            })
            if "semantic/note_links.json" not in retrieval_index.get("recommended_read_order", []):
                retrieval_index["recommended_read_order"].insert(5, "semantic/note_links.json")
        self.attach_document_links_to_retrieval_index(retrieval_index, document_links)
        evidence_index = {
            "schema_version": 1,
            "rule_version": RULE_VERSION,
            "generated_at": self.generated_at,
            "company_id": self.company_id,
            "report_id": self.primary_report,
            "evidence_count": len(self.evidence),
            "evidence": self.evidence,
        }
        extraction_log = self.extraction_log(segments, facts, relations, claims, note_links, document_links)
        write_json(self.semantic_dir / "subject_profile.json", subject_profile)
        write_json(self.semantic_dir / "segments.json", {
            "schema_version": 1,
            "rule_version": RULE_VERSION,
            "generated_at": self.generated_at,
            "company_id": self.company_id,
            "report_id": self.primary_report,
            "segments": segments,
        })
        write_json(self.semantic_dir / "facts.json", {
            "schema_version": 1,
            "rule_version": RULE_VERSION,
            "generated_at": self.generated_at,
            "company_id": self.company_id,
            "report_id": self.primary_report,
            "facts": facts,
        })
        write_json(self.semantic_dir / "relations.json", {
            "schema_version": 1,
            "rule_version": RULE_VERSION,
            "generated_at": self.generated_at,
            "company_id": self.company_id,
            "report_id": self.primary_report,
            "relations": relations,
        })
        write_json(self.semantic_dir / "claims.json", {
            "schema_version": 1,
            "rule_version": RULE_VERSION,
            "generated_at": self.generated_at,
            "company_id": self.company_id,
            "report_id": self.primary_report,
            "claims": claims,
        })
        write_json(self.semantic_dir / "retrieval_index.json", retrieval_index)
        write_json(self.semantic_dir / "note_links.json", note_links)
        write_json(self.semantic_dir / "document_links.json", document_links)
        write_json(self.semantic_dir / "evidence_semantic.json", evidence_index)
        write_json(self.semantic_dir / "image_semantic_manifest.json", {
            "schema_version": 1,
            "rule_version": RULE_VERSION,
            "generated_at": self.generated_at,
            "company_id": self.company_id,
            "report_id": self.primary_report,
            "images": image_manifest,
        })
        write_json(self.semantic_dir / "extraction_log.json", extraction_log)
        return {
            "company_id": self.company_id,
            "status": "ok",
            "counts": extraction_log["counts"],
            "quality": extraction_log["quality"],
        }


def build_manifest(wiki_root: Path, results: list[dict], failures: list[dict]) -> None:
    totals = defaultdict(int)
    quality_min = {
        "facts_with_evidence_ratio": 1.0,
        "claims_with_evidence_ratio": 1.0,
        "numeric_facts_with_metric_source_ratio": 1.0,
    }
    for result in results:
        for key, value in (result.get("counts") or {}).items():
            totals[key] += int(value or 0)
        for key, value in (result.get("quality") or {}).items():
            if value is not None:
                quality_min[key] = min(quality_min.get(key, 1.0), float(value))
    manifest = {
        "schema_version": 1,
        "rule_version": RULE_VERSION,
        "generated_at": now_iso(),
        "company_count": len(results),
        "failure_count": len(failures),
        "totals": dict(totals),
        "quality_min": quality_min,
        "results": results,
        "failures": failures,
    }
    write_json(wiki_root / "_meta" / "semantic_extraction_manifest.json", manifest)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wiki-root", default="/home/maoyd/wiki")
    parser.add_argument("--company", default="", help="Optional company_id, e.g. 002594-比亚迪")
    parser.add_argument("--skip-existing", action="store_true", help="Skip companies whose semantic rule layer matches current input hashes")
    args = parser.parse_args()

    wiki_root = Path(args.wiki_root)
    companies_root = wiki_root / "companies"
    if args.company:
        company_dirs = [companies_root / args.company]
    else:
        company_dirs = sorted([path for path in companies_root.iterdir() if path.is_dir()])

    results = []
    failures = []
    for company_dir in company_dirs:
        try:
            extractor = CompanyExtractor(company_dir)
            result = extractor.fresh_existing_result() if args.skip_existing else None
            if result is None:
                result = extractor.run()
            results.append(result)
            suffix = " skipped" if result.get("skipped_existing") else ""
            print(f"{result['company_id']}: {result['counts']}{suffix}")
        except Exception as exc:  # noqa: BLE001 - batch job should continue.
            failure = {"company_id": company_dir.name, "error": repr(exc)}
            failures.append(failure)
            print(f"{company_dir.name}: FAILED {exc}")
    build_manifest(wiki_root, results, failures)
    print(json.dumps({
        "companies": len(results),
        "failures": len(failures),
        "manifest": str(wiki_root / "_meta" / "semantic_extraction_manifest.json"),
    }, ensure_ascii=False))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
