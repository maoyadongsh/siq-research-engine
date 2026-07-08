from __future__ import annotations

from ...models import StatementType
from ...normalization import compact_label
from ..base import MetricRule


HK_LABEL_RULES: tuple[MetricRule, ...] = (
    MetricRule(
        "operating_revenue",
        StatementType.INCOME_STATEMENT,
        (
            "revenue",
            "turnover",
            "total revenues",
            "net revenues",
            "sales revenue",
            "sales of goods",
            "sales to external customers",
            "收益",
            "收入",
            "营业收入",
            "營業收入",
            "营业额",
            "營業額",
        ),
        10,
    ),
    MetricRule(
        "gross_profit",
        StatementType.INCOME_STATEMENT,
        ("gross profit", "毛利"),
    ),
    MetricRule(
        "cost_of_sales",
        StatementType.INCOME_STATEMENT,
        ("cost of sales", "cost of revenue", "sales costs", "销售成本", "銷售成本", "收入成本", "营业成本", "營業成本"),
        5,
    ),
    MetricRule(
        "operating_profit",
        StatementType.INCOME_STATEMENT,
        ("operating profit", "profit from operations", "income from operations", "loss from operations", "经营盈利", "經營盈利", "经营利润", "經營利潤"),
    ),
    MetricRule(
        "total_profit",
        StatementType.INCOME_STATEMENT,
        (
            "profit before tax",
            "profit before taxation",
            "profit before income tax",
            "profit before income taxes",
            "profit/(loss) before tax",
            "profit/(loss) before income tax",
            "profit (loss) before tax",
            "profit (loss) before income tax",
            "total profit",
            "income before income tax",
            "income before income taxes",
            "loss before tax",
            "loss before income tax",
            "loss before income taxes",
            "除税前利润",
            "除稅前利潤",
            "除所得税前溢利",
            "除所得稅前溢利",
            "税前利润",
            "稅前利潤",
            "利润总额",
            "利潤總額",
        ),
        5,
    ),
    MetricRule(
        "income_tax_expense",
        StatementType.INCOME_STATEMENT,
        ("income tax expense", "income tax expenses", "taxation", "tax expense", "income tax benefit", "所得税", "稅項", "税项"),
    ),
    MetricRule(
        "finance_costs",
        StatementType.INCOME_STATEMENT,
        ("finance costs", "interest expense", "borrowing costs", "融资成本", "融資成本", "利息开支", "利息開支"),
    ),
    MetricRule(
        "share_of_associates_jv",
        StatementType.INCOME_STATEMENT,
        (
            "share of results of associates",
            "share of results of joint ventures",
            "share of profits and losses of associates",
            "应占联营公司业绩",
            "應佔聯營公司業績",
            "应占合营企业业绩",
            "應佔合營企業業績",
        ),
    ),
    MetricRule(
        "net_profit",
        StatementType.INCOME_STATEMENT,
        (
            "profit for the year",
            "profit for the period",
            "loss for the year",
            "loss for the period",
            "profit/(loss) for the year",
            "(loss)/profit for the year",
            "(loss)/profit for the period",
            "net profit",
            "net income",
            "net loss",
            "consolidated net income",
            "年度利润",
            "年度利潤",
            "期内利润",
            "期內利潤",
            "净利润",
            "淨利潤",
        ),
        10,
    ),
    MetricRule(
        "parent_net_profit",
        StatementType.INCOME_STATEMENT,
        (
            "profit attributable to owners of the company",
            "profit attributable to equity holders",
            "profit attributable to equity holders of the company",
            "profit attributable to shareholders of the company",
            "profit attributable to shareholders",
            "profit attributable to ordinary shareholders",
            "profit attributable to ordinary shareholders of the company",
            "net income attributable to ordinary shareholders",
            "net income attributable to the company's ordinary shareholders",
            "net income attributable to alibaba group holding limited",
            "net income attributable to jd.com, inc.",
            "net income attributable to ordinary shareholders of li auto inc.",
            "net profit attributable to equity holders of the bank",
            "net profit attributable to equity shareholders of the bank",
            "net profit attributable to shareholders of the bank",
            "net profit attributable to ordinary shareholders of the bank",
            "net profit attributable to equity holders of the parent company",
            "profit for the year attributable to equity holders of the bank",
            "profit for the year attributable to equity shareholders of the bank",
            "profit for the year attributable to shareholders of the bank",
            "profit for the year attributable to ordinary equity holders of the bank",
            "profit for the year attributable to ordinary shareholders of the bank",
            "profit for the year attributable to equity holders of the parent company",
            "profit for the year attributable to ordinary shareholders of the parent company",
            "owners of the parent",
            "本公司拥有人应占利润",
            "本公司擁有人應佔利潤",
            "归属于母公司",
            "歸屬於母公司",
        ),
        5,
    ),
    MetricRule(
        "nci_profit",
        StatementType.INCOME_STATEMENT,
        (
            "profit attributable to non-controlling interests",
            "non-controlling interests",
            "minority interests",
            "net income attributable to non-controlling interests",
            "net loss attributable to noncontrolling interests",
            "net income attributable to non-controlling interests shareholders",
            "net income attributable to noncontrolling interests shareholders",
            "非控股权益应占利润",
            "非控股權益應佔利潤",
            "少数股东损益",
            "少數股東損益",
        ),
        5,
    ),
    MetricRule(
        "total_assets",
        StatementType.BALANCE_SHEET,
        ("total assets", "资产总额", "資產總額", "资产总计", "資產總計"),
        10,
    ),
    MetricRule(
        "current_assets",
        StatementType.BALANCE_SHEET,
        ("current assets", "total current assets", "流动资产", "流動資產", "流动资产总额", "流動資產總額"),
        5,
    ),
    MetricRule(
        "non_current_assets",
        StatementType.BALANCE_SHEET,
        ("non-current assets", "non current assets", "total non-current assets", "total non current assets", "非流动资产", "非流動資產", "非流动资产总额", "非流動資產總額"),
        5,
    ),
    MetricRule(
        "total_liabilities",
        StatementType.BALANCE_SHEET,
        ("total liabilities", "负债总额", "負債總額", "负债合计", "負債合計"),
        10,
    ),
    MetricRule(
        "current_liabilities",
        StatementType.BALANCE_SHEET,
        ("current liabilities", "total current liabilities", "流动负债", "流動負債", "流动负债总额", "流動負債總額"),
        5,
    ),
    MetricRule(
        "non_current_liabilities",
        StatementType.BALANCE_SHEET,
        ("non-current liabilities", "non current liabilities", "total non-current liabilities", "total non current liabilities", "非流动负债", "非流動負債", "非流动负债总额", "非流動負債總額"),
        5,
    ),
    MetricRule(
        "redeemable_noncontrolling_interest",
        StatementType.BALANCE_SHEET,
        (
            "redeemable non-controlling interests",
            "redeemable noncontrolling interests",
            "redeemable non-controlling interest",
            "redeemable noncontrolling interest",
            "mezzanine equity",
            "可赎回非控股权益",
            "可贖回非控股權益",
        ),
        1,
    ),
    MetricRule(
        "total_equity",
        StatementType.BALANCE_SHEET,
        (
            "total equity",
            "total shareholders' equity",
            "total shareholders’ equity",
            "total jd.com, inc. shareholders' equity",
            "total li auto inc. shareholders’ equity",
            "net assets",
            "capital and reserves",
            "权益总额",
            "權益總額",
            "权益合计",
            "權益合計",
            "资产净值",
            "資產淨值",
            "股东权益",
            "股東權益",
        ),
        10,
    ),
    MetricRule(
        "total_liabilities_and_equity",
        StatementType.BALANCE_SHEET,
        (
            "total equity and liabilities",
            "total liabilities and equity",
            "total liabilities and shareholders' equity",
            "total liabilities and shareholders’ equity",
            "total liabilities, redeemable noncontrolling interests and equity",
            "total liabilities, redeemable noncontrolling interests and shareholders' equity",
            "total liabilities, redeemable noncontrolling interests and shareholders’ equity",
            "total liabilities, redeemable non-controlling interests and equity",
            "total liabilities, redeemable non-controlling interests and shareholders' equity",
            "total liabilities, redeemable non-controlling interests and shareholders’ equity",
            "total liabilities, mezzanine equity and equity",
            "total liabilities, mezzanine equity and shareholders' equity",
            "total liabilities, mezzanine equity and shareholders’ equity",
            "负债及权益总额",
            "負債及權益總額",
        ),
        1,
    ),
    MetricRule(
        "cash_and_cash_equivalents",
        StatementType.BALANCE_SHEET,
        ("cash and cash equivalents", "cash and bank balances", "现金及现金等价物", "現金及現金等價物", "银行结余及现金", "銀行結餘及現金"),
    ),
    MetricRule(
        "trade_receivables",
        StatementType.BALANCE_SHEET,
        ("trade receivables", "accounts receivable", "trade and other receivables", "贸易应收款项", "貿易應收款項", "应收账款", "應收賬款"),
    ),
    MetricRule(
        "inventories",
        StatementType.BALANCE_SHEET,
        ("inventories", "properties held for sale", "存货", "存貨", "待售物业", "待售物業"),
    ),
    MetricRule(
        "investment_properties",
        StatementType.BALANCE_SHEET,
        ("investment properties", "投资物业", "投資物業", "投资性房地产", "投資性房地產"),
    ),
    MetricRule(
        "property_plant_equipment",
        StatementType.BALANCE_SHEET,
        ("property plant and equipment", "property, plant and equipment", "ppe", "物业厂房及设备", "物業廠房及設備", "固定资产", "固定資產"),
    ),
    MetricRule(
        "right_of_use_assets",
        StatementType.BALANCE_SHEET,
        ("right-of-use assets", "right of use assets", "使用权资产", "使用權資產"),
    ),
    MetricRule(
        "borrowings",
        StatementType.BALANCE_SHEET,
        ("borrowings", "bank borrowings", "bank loans", "借款", "银行贷款", "銀行貸款"),
    ),
    MetricRule(
        "lease_liabilities",
        StatementType.BALANCE_SHEET,
        ("lease liabilities", "租赁负债", "租賃負債"),
    ),
    MetricRule(
        "contract_liabilities",
        StatementType.BALANCE_SHEET,
        ("contract liabilities", "deferred revenue", "合约负债", "合約負債", "合同负债", "递延收入", "遞延收入"),
    ),
    MetricRule(
        "parent_equity",
        StatementType.BALANCE_SHEET,
        (
            "equity attributable to owners of the company",
            "equity attributable to shareholders",
            "本公司拥有人应占权益",
            "本公司擁有人應佔權益",
            "归属于母公司股东权益",
            "歸屬於母公司股東權益",
        ),
    ),
    MetricRule(
        "nci_equity",
        StatementType.BALANCE_SHEET,
        ("non-controlling interests", "minority interests", "非控股权益", "非控股權益", "少数股东权益", "少數股東權益"),
    ),
    MetricRule(
        "operating_cash_flow_net",
        StatementType.CASH_FLOW_STATEMENT,
        (
            "net cash generated from operating activities",
            "net cash from operating activities",
            "net cash provided by operating activities",
            "net cash provided by/(used in) operating activities",
            "net cash provided by (used in) operating activities",
            "net cash used in operating activities",
            "net cash inflow from operating activities",
            "net cash outflow from operating activities",
            "net cash from operating activities",
            "net cash generated from operations",
            "cash generated from operations",
            "net cash flow from operating activities",
            "net cash flow (used in)/from operating activities",
            "net cash flow used in/from operating activities",
            "net cash flows from operating activities",
            "net cash flows generated from operating activities",
            "net cash flows used in operating activities",
            "net cash flows (used in)/generated from operating activities",
            "net cash flows generated from/(used in) operating activities",
            "cash flows from operating activities",
            "经营活动所得现金净额",
            "經營活動所得現金淨額",
            "经营活动所得现金流量净额",
            "經營活動所得現金流量淨額",
            "经营活动产生的现金流量净额",
            "經營活動產生的現金流量淨額",
        ),
        10,
    ),
    MetricRule(
        "investing_cash_flow_net",
        StatementType.CASH_FLOW_STATEMENT,
        (
            "net cash used in investing activities",
            "net cash inflow from investing activities",
            "net cash outflow from investing activities",
            "net cash flows used in investing activities",
            "net cash generated from investing activities",
            "net cash flows generated from/(used in) investing activities",
            "net cash flows generated from/used in investing activities",
            "net cash flows used in/generated from investing activities",
            "net cash from investing activities",
            "net cash from investing activities",
            "net cash flows from investing activities",
            "投资活动现金流量净额",
            "投資活動現金流量淨額",
        ),
    ),
    MetricRule(
        "financing_cash_flow_net",
        StatementType.CASH_FLOW_STATEMENT,
        (
            "net cash used in financing activities",
            "net cash inflow from financing activities",
            "net cash outflow from financing activities",
            "net cash flows used in financing activities",
            "net cash generated from financing activities",
            "net cash flows generated from/(used in) financing activities",
            "net cash flows generated from/used in financing activities",
            "net cash flows used in/generated from financing activities",
            "net cash from financing activities",
            "net cash from financing activities",
            "net cash flows from financing activities",
            "融资活动现金流量净额",
            "融資活動現金流量淨額",
        ),
    ),
    MetricRule(
        "cash_equivalents_net_increase",
        StatementType.CASH_FLOW_STATEMENT,
        (
            "net increase in cash and cash equivalents",
            "net increase/(decrease) in cash and cash equivalents",
            "net increase (decrease) in cash and cash equivalents",
            "net increase/decrease in cash and cash equivalents",
            "net decrease in cash and cash equivalents",
            "现金及现金等价物增加净额",
            "現金及現金等價物增加淨額",
        ),
    ),
    MetricRule(
        "fx_effect_cash",
        StatementType.CASH_FLOW_STATEMENT,
        (
            "effect of foreign exchange rate changes",
            "effect of exchange rate changes",
            "effect of exchange rate changes on cash and cash equivalents",
            "exchange losses/gains on cash and cash equivalents",
            "exchange (losses)/gains on cash and cash equivalents",
            "exchange gains/losses on cash and cash equivalents",
            "exchange (gains)/losses on cash and cash equivalents",
            "foreign exchange effect on cash and cash equivalents",
            "汇率变动影响",
            "匯率變動影響",
            "匯率變動對現金及現金等價物的影響",
            "汇率变动对现金及现金等价物的影响",
        ),
    ),
    MetricRule(
        "cash_equivalents_beginning",
        StatementType.CASH_FLOW_STATEMENT,
        (
            "cash and cash equivalents at beginning",
            "cash and cash equivalents at the beginning",
            "cash and cash equivalents at beginning of the year",
            "cash and cash equivalents at the beginning of the year",
            "期初现金及现金等价物",
            "期初現金及現金等價物",
        ),
    ),
    MetricRule(
        "cash_equivalents_ending",
        StatementType.CASH_FLOW_STATEMENT,
        (
            "cash and cash equivalents at end",
            "cash and cash equivalents at the end",
            "cash and cash equivalents at end of the year",
            "cash and cash equivalents at the end of the year",
            "期末现金及现金等价物",
            "期末現金及現金等價物",
        ),
    ),
    MetricRule(
        "basic_eps",
        StatementType.KEY_METRICS,
        ("basic earnings per share", "basic eps", "每股基本盈利", "基本每股收益"),
    ),
    MetricRule(
        "diluted_eps",
        StatementType.KEY_METRICS,
        ("diluted earnings per share", "diluted eps", "每股摊薄盈利", "每股攤薄盈利", "稀释每股收益", "攤薄每股收益"),
    ),
)


HK_RULE_BY_LABEL = {
    compact_label(label): rule
    for rule in HK_LABEL_RULES
    for label in rule.labels
}


def find_hk_rule(label: str) -> MetricRule | None:
    normalized = compact_label(label)
    if not normalized:
        return None
    direct = HK_RULE_BY_LABEL.get(normalized)
    if direct and _rule_allowed(direct, normalized):
        return direct
    if any(token in normalized for token in ("pershare", "perads", "earningspershare", "earningsperads", "netincomeper")):
        return None
    if any(token in normalized for token in ("nongaapnetincome", "adjustednetincome", "adjustednetloss", "adjustednetprofit")):
        return None
    candidates = [
        (alias, rule)
        for alias, rule in HK_RULE_BY_LABEL.items()
        if alias and alias in normalized and _rule_allowed(rule, normalized)
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda item: (item[1].priority, -len(item[0])))[1]


def _rule_allowed(rule: MetricRule, normalized: str) -> bool:
    if rule.canonical_name == "operating_revenue":
        if any(
            token in normalized
            for token in (
                "otherincome",
                "otherrevenue",
                "othergains",
                "interestincome",
                "comprehensiveincome",
                "comprehensiveexpenseincome",
                "othercomprehensive",
                "deferredincome",
                "incometax",
                "taxincome",
                "costofsales",
                "salescost",
                "salesdiscount",
                "averageannualrevenue",
                "emoluments",
                "companylimited",
                "incomecertificates",
                "foreignexchange",
                "fairvalue",
                "fvtpl",
                "gainson",
                "losson",
                "compensation",
                "scrap",
                "折扣",
                "返利",
                "其他收入",
                "其他收益",
                "全面收益",
                "全面支出收益",
                "其他全面",
                "遞延收益",
                "递延收益",
                "收益憑證",
                "收益凭证",
                "利息收入",
                "所得税",
                "所得稅",
                "销售成本",
                "銷售成本",
            )
        ):
            return False
    if rule.canonical_name in {"net_profit", "parent_net_profit"}:
        if any(
            token in normalized
            for token in (
                "forprofitfortheyear",
                "comprehensiveincome",
                "comprehensiveexpense",
                "earningspershare",
                "全面收益",
                "全面支出",
            )
        ):
            return False
    if rule.canonical_name == "income_tax_expense":
        if any(token in normalized for token in ("beforeincometax", "beforetax", "除所得税前", "除所得稅前")):
            return False
    if rule.canonical_name in {"total_assets", "total_liabilities"}:
        if any(token in normalized for token in ("average", "averagetotal", "ratio", "percentage", "turnover", "周轉", "周转", "比率")):
            return False
        if any(token in normalized for token in ("heldforsale", "classifiedasheldforsale", "directlyassociatedwithassets")):
            return False
        if "totalassetslesscurrentliabilities" in normalized:
            return False
    if rule.canonical_name == "current_liabilities" and "totalassetslesscurrentliabilities" in normalized:
        return False
    if rule.canonical_name == "nci_equity" and any(token in normalized for token in ("redeemable", "mezzanine", "可赎回", "可贖回")):
        return False
    if rule.canonical_name == "total_equity" and any(token in normalized for token in ("netassets", "資產淨值", "资产净值")):
        return normalized in {"netassets", "totalnetassets", "資產淨值", "资产净值"}
    if rule.canonical_name == "total_equity":
        if any(
            token in normalized
            for token in (
                "attributableto",
                "recognizedin",
                "recognisedin",
                "sharebasedpayment",
                "percentageof",
                "asapercentage",
                "statementofchanges",
                "changesinequity",
                "equitysettled",
                "reserves",
            )
        ):
            return False
    return True
