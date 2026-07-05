from __future__ import annotations

from ...models import StatementType
from ...normalization import compact_label
from ..base import MetricRule


EU_LABEL_RULES: tuple[MetricRule, ...] = (
    MetricRule(
        "operating_revenue",
        StatementType.INCOME_STATEMENT,
        (
            "revenue",
            "revenues",
            "sales",
            "net sales",
            "total net sales",
            "sales revenue",
            "turnover",
            "net revenue",
            "external revenue",
            "sales to external customers",
            "ifrs-full:Revenue",
        ),
        10,
    ),
    MetricRule("gross_profit", StatementType.INCOME_STATEMENT, ("gross profit", "gross margin")),
    MetricRule(
        "cost_of_sales",
        StatementType.INCOME_STATEMENT,
        ("cost of sales", "cost of revenue", "cost of goods sold", "cost of goods and services sold"),
    ),
    MetricRule(
        "operating_profit",
        StatementType.INCOME_STATEMENT,
        (
            "operating profit",
            "operating income",
            "operating result",
            "profit from operations",
            "income from operations",
            "operating loss",
            "ifrs-full:ProfitLossFromOperatingActivities",
        ),
    ),
    MetricRule(
        "total_profit",
        StatementType.INCOME_STATEMENT,
        (
            "profit before tax",
            "profit before taxation",
            "profit before income tax",
            "income before income taxes",
            "earnings before tax",
            "loss before tax",
            "ifrs-full:ProfitLossBeforeTax",
        ),
        5,
    ),
    MetricRule(
        "income_tax_expense",
        StatementType.INCOME_STATEMENT,
        ("income tax expense", "income taxes", "tax expense", "taxation", "ifrs-full:TaxExpenseIncome"),
    ),
    MetricRule(
        "finance_costs",
        StatementType.INCOME_STATEMENT,
        ("finance costs", "finance expenses", "interest expense", "borrowing costs", "ifrs-full:FinanceCosts"),
    ),
    MetricRule(
        "net_profit",
        StatementType.INCOME_STATEMENT,
        (
            "profit for the year",
            "profit for the period",
            "loss for the year",
            "loss for the period",
            "net profit",
            "net income",
            "net loss",
            "profit attributable to shareholders",
            "profit attributable to owners",
            "profit attributable to owners of the parent",
            "profit attributable to equity holders",
            "ifrs-full:ProfitLoss",
        ),
        10,
    ),
    MetricRule(
        "parent_net_profit",
        StatementType.INCOME_STATEMENT,
        (
            "profit attributable to owners of the parent",
            "profit attributable to shareholders",
            "profit attributable to ordinary shareholders",
            "profit attributable to equity holders",
            "net income attributable to shareholders",
            "net income attributable to owners of the parent",
            "ifrs-full:ProfitLossAttributableToOwnersOfParent",
        ),
        5,
    ),
    MetricRule(
        "nci_profit",
        StatementType.INCOME_STATEMENT,
        (
            "profit attributable to non-controlling interests",
            "profit attributable to noncontrolling interests",
            "non-controlling interests",
            "noncontrolling interests",
            "ifrs-full:ProfitLossAttributableToNoncontrollingInterests",
        ),
    ),
    MetricRule("total_assets", StatementType.BALANCE_SHEET, ("total assets", "ifrs-full:Assets"), 10),
    MetricRule("current_assets", StatementType.BALANCE_SHEET, ("current assets", "total current assets", "ifrs-full:CurrentAssets")),
    MetricRule(
        "non_current_assets",
        StatementType.BALANCE_SHEET,
        ("non-current assets", "non current assets", "total non-current assets", "total non current assets", "ifrs-full:NoncurrentAssets"),
    ),
    MetricRule("total_liabilities", StatementType.BALANCE_SHEET, ("total liabilities", "ifrs-full:Liabilities"), 10),
    MetricRule(
        "current_liabilities",
        StatementType.BALANCE_SHEET,
        ("current liabilities", "total current liabilities", "ifrs-full:CurrentLiabilities"),
    ),
    MetricRule(
        "non_current_liabilities",
        StatementType.BALANCE_SHEET,
        ("non-current liabilities", "non current liabilities", "total non-current liabilities", "total non current liabilities", "ifrs-full:NoncurrentLiabilities"),
    ),
    MetricRule(
        "total_equity",
        StatementType.BALANCE_SHEET,
        (
            "total equity",
            "shareholders' equity",
            "shareholders’ equity",
            "total shareholders' equity",
            "total shareholders’ equity",
            "stockholders' equity",
            "total stockholders' equity",
            "net assets",
            "ifrs-full:Equity",
        ),
        10,
    ),
    MetricRule(
        "parent_equity",
        StatementType.BALANCE_SHEET,
        (
            "equity attributable to owners of the parent",
            "equity attributable to shareholders",
            "equity attributable to equity holders",
            "ifrs-full:EquityAttributableToOwnersOfParent",
        ),
    ),
    MetricRule(
        "nci_equity",
        StatementType.BALANCE_SHEET,
        ("non-controlling interests", "noncontrolling interests", "ifrs-full:NoncontrollingInterests"),
    ),
    MetricRule(
        "total_liabilities_and_equity",
        StatementType.BALANCE_SHEET,
        (
            "total equity and liabilities",
            "total liabilities and equity",
            "total shareholders' equity and liabilities",
            "total shareholders’ equity and liabilities",
            "total liabilities & shareholders' equity",
            "total liabilities & shareholders’ equity",
            "total shareholders' equity and liabilities",
            "total shareholders’ equity and liabilities",
            "equity and liabilities",
            "ifrs-full:EquityAndLiabilities",
        ),
    ),
    MetricRule(
        "cash_and_cash_equivalents",
        StatementType.BALANCE_SHEET,
        ("cash and cash equivalents", "cash and cash equivalents at end of year", "cash and short-term deposits", "ifrs-full:CashAndCashEquivalents"),
    ),
    MetricRule(
        "trade_receivables",
        StatementType.BALANCE_SHEET,
        ("trade receivables", "trade and other receivables", "accounts receivable", "ifrs-full:TradeAndOtherCurrentReceivables"),
    ),
    MetricRule("inventories", StatementType.BALANCE_SHEET, ("inventories", "inventory", "ifrs-full:Inventories")),
    MetricRule(
        "property_plant_equipment",
        StatementType.BALANCE_SHEET,
        ("property, plant and equipment", "property plant and equipment", "plant and equipment", "ifrs-full:PropertyPlantAndEquipment"),
    ),
    MetricRule("goodwill", StatementType.BALANCE_SHEET, ("goodwill", "ifrs-full:Goodwill")),
    MetricRule("right_of_use_assets", StatementType.BALANCE_SHEET, ("right-of-use assets", "right of use assets", "ifrs-full:RightofuseAssets")),
    MetricRule("borrowings", StatementType.BALANCE_SHEET, ("borrowings", "loans and borrowings", "financial debt", "ifrs-full:Borrowings")),
    MetricRule("lease_liabilities", StatementType.BALANCE_SHEET, ("lease liabilities", "ifrs-full:LeaseLiabilities")),
    MetricRule("contract_liabilities", StatementType.BALANCE_SHEET, ("contract liabilities", "deferred revenue", "ifrs-full:ContractLiabilities")),
    MetricRule(
        "operating_cash_flow_net",
        StatementType.CASH_FLOW_STATEMENT,
        (
            "net cash from operating activities",
            "net cash generated from operating activities",
            "net cash provided by operating activities",
            "net cash provided by/(used in) operating activities",
            "net cash provided by/(used in) continuing operating activities",
            "net cash provided by/(used in) operating activities of the discontinued opella business",
            "net cash provided/(used) by operating activities",
            "net cash provided/used by operating activities",
            "net cash used in operating activities",
            "cash flows from operating activities",
            "net cash flows from operating activities",
            "net cash flows generated from operating activities",
            "cash flow from operating activities",
            "cash inflow/outflow from operating activities",
            "ifrs-full:CashFlowsFromUsedInOperatingActivities",
        ),
        10,
    ),
    MetricRule(
        "investing_cash_flow_net",
        StatementType.CASH_FLOW_STATEMENT,
        (
            "net cash from investing activities",
            "net cash used in investing activities",
            "net cash generated from investing activities",
            "net cash provided by/(used in) investing activities",
            "net cash provided by/(used in) continuing investing activities",
            "net cash provided/(used) by investing activities",
            "net cash provided/used by investing activities",
            "cash flows from investing activities",
            "net cash flows from investing activities",
            "cash flow from investing activities",
            "ifrs-full:CashFlowsFromUsedInInvestingActivities",
        ),
    ),
    MetricRule(
        "financing_cash_flow_net",
        StatementType.CASH_FLOW_STATEMENT,
        (
            "net cash from financing activities",
            "net cash used in financing activities",
            "net cash generated from financing activities",
            "net cash provided by/(used in) financing activities",
            "net cash provided by/(used in) continuing financing activities",
            "net cash provided/(used) by financing activities",
            "net cash provided/used by financing activities",
            "cash flows from financing activities",
            "net cash flows from financing activities",
            "cash flow from financing activities",
            "cash inflow/outflow from financing activities",
            "ifrs-full:CashFlowsFromUsedInFinancingActivities",
        ),
    ),
    MetricRule(
        "cash_equivalents_net_increase",
        StatementType.CASH_FLOW_STATEMENT,
        (
            "net increase in cash and cash equivalents",
            "net decrease in cash and cash equivalents",
            "increase in cash and cash equivalents",
            "decrease in cash and cash equivalents",
            "net increase/(decrease) in cash and cash equivalents",
            "change in cash and cash equivalents",
            "net change in cash and cash equivalents",
            "ifrs-full:IncreaseDecreaseInCashAndCashEquivalents",
        ),
    ),
    MetricRule(
        "fx_effect_cash",
        StatementType.CASH_FLOW_STATEMENT,
        (
            "effect of exchange rate changes",
            "effect of foreign exchange rate changes",
            "effect of exchange rate on cash and cash equivalents",
            "impact of exchange rates on cash and cash equivalents",
            "ifrs-full:EffectOfExchangeRateChangesOnCashAndCashEquivalents",
        ),
    ),
    MetricRule(
        "cash_equivalents_beginning",
        StatementType.CASH_FLOW_STATEMENT,
        (
            "cash and cash equivalents, beginning of period",
            "cash and cash equivalents at beginning of period",
            "cash and cash equivalents as at 1 january",
            "cash and cash equivalents at start of year",
        ),
    ),
    MetricRule(
        "cash_equivalents_ending",
        StatementType.CASH_FLOW_STATEMENT,
        (
            "cash and cash equivalents, end of period",
            "cash and cash equivalents at end of period",
            "cash and cash equivalents as at 31 december",
            "cash and cash equivalents at end of year",
        ),
    ),
    MetricRule(
        "capital_expenditure",
        StatementType.CASH_FLOW_STATEMENT,
        ("capital expenditure", "capital expenditures", "purchase of property, plant and equipment", "payments to acquire property, plant and equipment"),
    ),
    MetricRule("basic_eps", StatementType.KEY_METRICS, ("basic earnings per share", "basic eps", "ifrs-full:BasicEarningsLossPerShare")),
    MetricRule("diluted_eps", StatementType.KEY_METRICS, ("diluted earnings per share", "diluted eps", "ifrs-full:DilutedEarningsLossPerShare")),
)


EU_RULE_BY_LABEL = {
    compact_label(label): rule
    for rule in EU_LABEL_RULES
    for label in rule.labels
}


def find_eu_label_rule(label: str) -> MetricRule | None:
    normalized = compact_label(label)
    if not normalized:
        return None
    if any(
        token in normalized
        for token in (
            "totalassetslesscurrentliabilities",
            "netassetslesscurrentliabilities",
        )
    ):
        return None
    direct = EU_RULE_BY_LABEL.get(normalized)
    if direct and _rule_allowed(direct, normalized):
        return direct
    if any(token in normalized for token in ("pershare", "earningspershare", "eps", "perordinaryshare")):
        return None
    if any(token in normalized for token in ("adjusted", "nongaap", "normalized", "underlying", "ebitda")):
        return None
    candidates = [
        (alias, rule)
        for alias, rule in EU_RULE_BY_LABEL.items()
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
                "otheroperatingincome",
                "financeincome",
                "interestincome",
                "taxincome",
                "incometax",
                "comprehensiveincome",
                "deferredincome",
                "costofsales",
                "costofrevenue",
                "averageannualrevenue",
                "revenuegrowth",
                "salesgrowth",
                "fairvalue",
                "gainson",
                "losson",
            )
        ):
            return False
    if rule.canonical_name in {"net_profit", "parent_net_profit"}:
        if any(token in normalized for token in ("comprehensiveincome", "comprehensiveexpense", "earningspershare")):
            return False
    if rule.canonical_name == "operating_profit":
        if any(token in normalized for token in ("otheroperatingincome", "otheroperatingexpense", "otherincome")):
            return False
    if rule.canonical_name == "income_tax_expense":
        if any(token in normalized for token in ("beforeincometax", "beforetax")):
            return False
    if rule.canonical_name == "total_assets":
        if any(
            token in normalized
            for token in (
                "averagetotalassets",
                "subtotalassets",
                "totalassetsless",
                "totalassetsexcluding",
                "excludinggoodwill",
                "lesscurrentliabilities",
                "financialservices",
                "segmentassets",
                "assetsbacking",
                "assetsatfairvalue",
                "totalassetsatfairvalue",
                "assetsforunitlinked",
                "unitlinkedcontracts",
            )
        ):
            return False
        return normalized in {"totalassets", "ifrsfullassets"} or normalized.startswith("totalassets") or normalized.endswith("totalassets")
    if rule.canonical_name == "total_liabilities":
        if any(
            token in normalized
            for token in (
                "averagetotalliabilities",
                "subtotalliabilities",
                "financialservices",
                "segmentliabilities",
                "liabilitiesexcluding",
                "excludingshareholder",
                "excludingequity",
                "liabilitiesarisingfrom",
                "liabilitiesatfairvalue",
                "totalliabilitiesatfairvalue",
                "incurredclaims",
                "unitlinkedcontracts",
                "bankingactivities",
                "technicalreserves",
            )
        ):
            return False
        return normalized in {"totalliabilities", "ifrsfullliabilities"} or normalized.startswith("totalliabilities") or normalized.endswith("totalliabilities")
    if rule.canonical_name == "total_equity" and "netassets" in normalized:
        return normalized in {"netassets", "totalnetassets"}
    if rule.canonical_name == "total_equity":
        if any(
            token in normalized
            for token in (
                "averageoftotalequity",
                "totalequitynetdebt",
                "netdebt",
                "attheendoftheperiod",
                "returnon",
                "percentageof",
                "excluding",
                "liabilitiesexcluding",
                "fairvalue",
                "opening",
                "closing",
                "movementin",
                "historicalexchangerate",
                "exchangerateasof",
            )
        ):
            return False
    return True
