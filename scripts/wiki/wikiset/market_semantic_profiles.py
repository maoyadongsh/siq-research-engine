"""Market profiles for rule-first Wiki semantic extraction.

The A-share extractor remains the reference implementation. This module keeps
market-specific headings, aliases, and LLM guardrails out of the extractor so
HK/KR/JP/EU/US can evolve without weakening the CN rules.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

TARGET_SEGMENT_TYPES = {
    "business_overview",
    "management_discussion",
    "industry_analysis",
    "segment_performance",
    "product_service",
    "region_market",
    "customer_supplier",
    "rd_innovation",
    "capex_projects",
    "risk_factors",
    "major_events",
    "corporate_governance",
    "esg_social_responsibility",
    "financial_statements",
    "notes_to_financials",
}


A_SHARE_TOPIC_ALIASES = {
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
    "company_profile": ["company profile", "about", "group profile", "company information", "who we are"],
    "key_financials": ["at a glance", "key figures", "selected financial data", "financial highlights", "kpis"],
    "management_discussion": [
        "management report",
        "management discussion",
        "management's discussion",
        "management’s discussion",
        "financial review",
        "strategic report",
        "outlook",
        "results of operations",
        "financial position",
    ],
    "business_overview": ["business", "strategy", "portfolio", "business model", "operations", "value creation"],
    "industry_analysis": ["market", "macroeconomic", "industry", "competition", "regulatory"],
    "segment_performance": ["segment", "operating segments", "segment information", "division", "business unit"],
    "product_service": ["products", "services", "brands", "solutions"],
    "region_market": ["region", "geographic", "north america", "europe", "asia pacific", "global"],
    "customer_supplier": ["customers", "suppliers", "procurement", "sales channels"],
    "rd_innovation": ["research", "development", "innovation", "technology", "patents"],
    "capex_projects": ["investment", "capital expenditures", "projects", "site", "plant"],
    "risk_factors": ["risk", "risks", "opportunities", "uncertainties", "risk report", "principal risks"],
    "corporate_governance": ["corporate governance", "board", "committee", "compliance", "audit", "internal control"],
    "shareholders": ["shareholders", "share", "shares", "stock", "capital stock"],
    "dividend": ["dividend", "distribution", "payout"],
    "major_events": ["events", "litigation", "legal", "acquisition", "divestiture", "transaction"],
    "financial_statements": [
        "financial statements",
        "income statement",
        "balance sheet",
        "cash flows",
        "statement of cash",
        "statement of financial position",
    ],
    "notes_to_financials": ["notes", "accounting policies", "notes to consolidated financial statements"],
    "esg_social_responsibility": [
        "sustainability",
        "esg",
        "climate",
        "environment",
        "employees",
        "safety",
        "emissions",
        "responsibility",
        "esrs",
    ],
}


MARKET_TOPIC_ALIASES = {
    "HK": {
        **GENERIC_TOPIC_ALIASES,
        "company_profile": GENERIC_TOPIC_ALIASES["company_profile"] + ["公司資料", "集團簡介", "公司簡介", "company information"],
        "key_financials": GENERIC_TOPIC_ALIASES["key_financials"] + ["財務摘要", "主要財務數據", "five year summary"],
        "management_discussion": GENERIC_TOPIC_ALIASES["management_discussion"] + [
            "管理層討論",
            "管理層討論及分析",
            "業務回顧",
            "business review",
            "financial review",
            "chairman's statement",
            "ceo review",
        ],
        "business_overview": GENERIC_TOPIC_ALIASES["business_overview"] + ["業務概覽", "主要業務", "our business", "our strategy"],
        "industry_analysis": GENERIC_TOPIC_ALIASES["industry_analysis"] + ["市場環境", "行業", "competition", "regulation"],
        "segment_performance": GENERIC_TOPIC_ALIASES["segment_performance"] + ["分部", "segmental analysis", "business segments"],
        "product_service": GENERIC_TOPIC_ALIASES["product_service"] + ["產品", "服務", "brands"],
        "region_market": GENERIC_TOPIC_ALIASES["region_market"] + ["香港", "中國內地", "大中華", "hong kong", "mainland china"],
        "customer_supplier": GENERIC_TOPIC_ALIASES["customer_supplier"] + ["客戶", "供應商"],
        "rd_innovation": GENERIC_TOPIC_ALIASES["rd_innovation"] + ["研發", "創新", "digital"],
        "capex_projects": GENERIC_TOPIC_ALIASES["capex_projects"] + ["資本開支", "投資", "projects"],
        "risk_factors": GENERIC_TOPIC_ALIASES["risk_factors"] + ["風險", "risk review", "risk management"],
        "corporate_governance": GENERIC_TOPIC_ALIASES["corporate_governance"] + ["企業管治", "公司治理", "audit committee"],
        "shareholders": GENERIC_TOPIC_ALIASES["shareholders"] + ["股東", "股份", "share capital"],
        "dividend": GENERIC_TOPIC_ALIASES["dividend"] + ["股息", "分紅", "dividend per share"],
        "major_events": GENERIC_TOPIC_ALIASES["major_events"] + ["重大事項", "收購", "出售"],
        "financial_statements": GENERIC_TOPIC_ALIASES["financial_statements"] + ["財務報表", "consolidated financial statements"],
        "notes_to_financials": GENERIC_TOPIC_ALIASES["notes_to_financials"] + ["附註", "notes to the financial statements"],
    },
    "KR": {
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
        "risk_factors": ["위험", "위험관리", "주요위험", "리스크", "시장위험", "신용위험", "유동성위험"],
        "corporate_governance": ["이사회", "감사", "임원", "지배구조", "내부통제"],
        "shareholders": ["주식", "주주", "최대주주", "자본금"],
        "dividend": ["배당", "이익배당"],
        "major_events": ["중요한 사항", "주요사항", "소송", "거래", "합병", "양수도"],
        "financial_statements": ["재무제표", "연결재무제표", "손익계산서", "재무상태표", "현금흐름표"],
        "notes_to_financials": ["주석", "재무제표 주석"],
        "esg_social_responsibility": ["지속가능", "esg", "환경", "사회", "책임"],
    },
    "JP": {
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
        "risk_factors": ["事業等のリスク", "リスク", "リスク管理"],
        "corporate_governance": ["コーポレートガバナンス", "ガバナンス", "役員", "取締役", "監査"],
        "shareholders": ["株式", "株主", "所有者別状況"],
        "dividend": ["配当", "剰余金"],
        "major_events": ["重要な契約", "重要な後発事象", "訴訟", "買収", "組織再編"],
        "financial_statements": ["連結財務諸表", "財務諸表", "貸借対照表", "損益計算書", "キャッシュ・フロー"],
        "notes_to_financials": ["注記事項", "注記", "連結附属明細表"],
        "esg_social_responsibility": ["サステナビリティ", "esg", "環境", "社会", "人的資本", "気候変動"],
    },
    "EU": {
        **GENERIC_TOPIC_ALIASES,
        "company_profile": GENERIC_TOPIC_ALIASES["company_profile"] + ["our business", "our brands", "lagebericht", "rapport de gestion", "bestuursverslag"],
        "key_financials": GENERIC_TOPIC_ALIASES["key_financials"] + ["group highlights", "financial highlights", "key performance indicators"],
        "management_discussion": GENERIC_TOPIC_ALIASES["management_discussion"] + [
            "strategic report",
            "management report",
            "financial review",
            "year in review",
            "outlook",
            "lagebericht",
            "rapport de gestion",
        ],
        "business_overview": GENERIC_TOPIC_ALIASES["business_overview"] + ["business model", "our strategy", "portfolio", "value creation"],
        "segment_performance": GENERIC_TOPIC_ALIASES["segment_performance"] + ["operating segments", "segment information", "divisions", "business units"],
        "risk_factors": GENERIC_TOPIC_ALIASES["risk_factors"] + ["principal risks", "risk management", "risk report", "risques", "opportunities and risks"],
        "corporate_governance": GENERIC_TOPIC_ALIASES["corporate_governance"] + ["governance report", "supervisory board", "management board", "remuneration"],
        "financial_statements": GENERIC_TOPIC_ALIASES["financial_statements"] + ["consolidated statements", "consolidated income statement"],
        "notes_to_financials": GENERIC_TOPIC_ALIASES["notes_to_financials"] + ["notes to the consolidated financial statements"],
        "esg_social_responsibility": GENERIC_TOPIC_ALIASES["esg_social_responsibility"] + ["sustainability statement", "esrs", "csrd", "taxonomy"],
    },
    "US": {
        **GENERIC_TOPIC_ALIASES,
        "business_overview": GENERIC_TOPIC_ALIASES["business_overview"] + ["item 1", "item 1. business", "business"],
        "risk_factors": GENERIC_TOPIC_ALIASES["risk_factors"] + ["item 1a", "item 1a. risk factors", "risk factors"],
        "management_discussion": GENERIC_TOPIC_ALIASES["management_discussion"] + [
            "item 7",
            "management's discussion and analysis",
            "management’s discussion and analysis",
            "mda",
            "md&a",
        ],
        "industry_analysis": GENERIC_TOPIC_ALIASES["industry_analysis"] + ["item 7a", "market risk", "quantitative and qualitative disclosures"],
        "financial_statements": GENERIC_TOPIC_ALIASES["financial_statements"] + ["item 8", "financial statements and supplementary data"],
        "major_events": GENERIC_TOPIC_ALIASES["major_events"] + ["item 3", "legal proceedings"],
        "capex_projects": GENERIC_TOPIC_ALIASES["capex_projects"] + ["item 2", "properties"],
        "corporate_governance": GENERIC_TOPIC_ALIASES["corporate_governance"] + ["item 9a", "controls and procedures"],
    },
}


MARKET_TITLE_BOOSTS = {
    "CN": ["业务", "经营", "行业", "风险", "研发", "客户", "供应商", "产品", "项目", "产能", "海外", "战略", "重大"],
    "HK": ["business", "review", "strategy", "risk", "segment", "region", "esg", "governance", "業務", "風險", "分部", "策略"],
    "KR": ["사업", "위험", "경영", "재무", "매출", "연구개발", "제품", "시장", "부문"],
    "JP": ["事業", "リスク", "経営", "財政状態", "研究開発", "セグメント", "市場", "製品"],
    "EU": ["strategic", "management", "risk", "segment", "sustainability", "esrs", "region", "financial review"],
    "US": ["item 1", "item 1a", "item 7", "item 7a", "item 8", "risk", "mda", "business", "controls"],
}


METRIC_ALIASES = {
    "CN": {
        "营业收入": ["operating_revenue", "收入", "营收"],
        "归母净利润": ["parent_net_profit", "净利润", "归属于上市公司股东的净利润"],
        "扣非归母净利润": ["deducted_parent_net_profit", "扣非净利润"],
        "经营现金流": ["operating_cash_flow_net", "经营活动现金流"],
        "总资产": ["total_assets", "资产总计"],
        "总负债": ["total_liabilities", "负债合计"],
        "所有者权益": ["total_equity", "股东权益", "净资产"],
        "净资产收益率": ["weighted_avg_roe", "ROE"],
    },
    "US": {
        "收入": ["operating_revenue", "revenue", "sales", "net sales"],
        "净利润": ["net_profit", "net income", "net earnings"],
        "总资产": ["total_assets", "assets"],
        "总负债": ["total_liabilities", "liabilities"],
        "股东权益": ["total_equity", "stockholders equity"],
        "经营现金流": ["operating_cash_flow_net", "cash provided by operating activities"],
    },
}

for _market in ("HK", "KR", "JP", "EU"):
    METRIC_ALIASES[_market] = {
        "收入": ["operating_revenue", "revenue", "sales", "売上収益", "매출액", "收益", "營業收入"],
        "净利润": ["net_profit", "parent_net_profit", "profit for the year", "net income", "当期利益", "순이익"],
        "总资产": ["total_assets", "assets", "資産合計", "자산총계", "資產總額"],
        "总负债": ["total_liabilities", "liabilities", "負債合計", "부채총계", "負債總額"],
        "所有者权益": ["total_equity", "equity", "資本合計", "자본총계", "權益總額"],
        "经营现金流": ["operating_cash_flow_net", "cash flows from operating activities", "営業活動によるキャッシュ・フロー", "영업활동 현금흐름"],
    }


@dataclass(frozen=True)
class MarketSemanticProfile:
    market: str
    source_language: str
    output_language: str
    topic_aliases: dict[str, list[str]]
    title_boost_keywords: list[str]
    llm_focus: list[str]
    llm_guardrails: list[str]
    sec_item_map: dict[str, str] | None = None


def normalize_market(market: str | None) -> str:
    code = str(market or "").strip().upper()
    if code in {"CN", "A", "A_SHARE", "ASHARE", "A股"}:
        return "CN"
    return code


def market_from_company(company: dict[str, Any] | None, default: str = "CN") -> str:
    company = company or {}
    for key in ("market", "market_profile"):
        code = normalize_market(company.get(key))
        if code:
            return code
    return default


def topic_aliases_for_market(market: str | None, generic_route: bool = False) -> dict[str, list[str]]:
    code = normalize_market(market)
    if code in MARKET_TOPIC_ALIASES:
        return MARKET_TOPIC_ALIASES[code]
    if generic_route:
        return GENERIC_TOPIC_ALIASES
    return A_SHARE_TOPIC_ALIASES


def metric_aliases_for_market(market: str | None) -> dict[str, list[str]]:
    code = normalize_market(market) or "CN"
    return METRIC_ALIASES.get(code) or METRIC_ALIASES["CN"]


def title_boost_keywords_for_market(market: str | None) -> list[str]:
    code = normalize_market(market) or "CN"
    return MARKET_TITLE_BOOSTS.get(code) or MARKET_TITLE_BOOSTS["CN"]


def classify_segment_title(title: str, market: str | None, generic_route: bool = False) -> str:
    aliases = topic_aliases_for_market(market, generic_route)
    text_space = re.sub(r"\s+", " ", str(title or "")).strip().lower()
    text_no_space = re.sub(r"\s+", "", text_space)
    if not text_space:
        return "other"
    for segment_type, keywords in aliases.items():
        for keyword in keywords:
            key_space = str(keyword or "").strip().lower()
            key_no_space = re.sub(r"\s+", "", key_space)
            if key_space and (key_space in text_space or key_no_space in text_no_space):
                return segment_type
    return "other"


def profile_for_market(market: str | None, generic_route: bool = False) -> MarketSemanticProfile:
    code = normalize_market(market) or ("GENERIC" if generic_route else "CN")
    source_language = {
        "CN": "zh-CN",
        "HK": "en/zh-Hant",
        "KR": "ko",
        "JP": "ja",
        "EU": "multi-European/en",
        "US": "en-US/sec",
    }.get(code, "multi")
    focus = {
        "HK": ["战略报告/业务回顾", "财务回顾", "风险管理", "分部和地区", "企业管治", "ESG"],
        "KR": ["DART 事业内容", "董事经营诊断", "风险管理", "财务事项", "产品/客户/产能"],
        "JP": ["有报事业内容", "事业风险", "经营者讨论分析", "研究开发", "分部/地区"],
        "EU": ["Strategic/Management report", "Risk report", "Sustainability/ESRS", "segments", "regions"],
        "US": ["10-K Item 1", "Item 1A", "Item 7 MD&A", "Item 7A", "Item 8", "Item 9A"],
    }.get(code, ["业务", "风险", "战略", "分部", "地区", "研发", "重大事项"])
    guardrails = [
        "规则事实层优先；LLM 不得新增财务数值或覆盖规则事实。",
        "正式输出必须引用输入允许的 segment_id/evidence_id。",
        "统一中文输出；保留原文标题、Item 编号、韩/日/繁中/英文术语作为 source_terms。",
        "证据不足、跨口径或疑似推断的内容进入 review_queue。",
    ]
    sec_item_map = {
        "item_1": "business_overview",
        "item_1a": "risk_factors",
        "item_2": "capex_projects",
        "item_3": "major_events",
        "item_7": "management_discussion",
        "item_7a": "industry_analysis",
        "item_8": "financial_statements",
        "item_9a": "corporate_governance",
    } if code == "US" else None
    return MarketSemanticProfile(
        market=code,
        source_language=source_language,
        output_language="zh-CN",
        topic_aliases=topic_aliases_for_market(code, generic_route),
        title_boost_keywords=title_boost_keywords_for_market(code),
        llm_focus=focus,
        llm_guardrails=guardrails,
        sec_item_map=sec_item_map,
    )
