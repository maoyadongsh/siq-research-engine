from __future__ import annotations

from ...models import StatementType
from ...normalization import compact_label, normalize_concept
from ..base import MetricRule


KR_CONCEPT_RULES: tuple[MetricRule, ...] = (
    MetricRule("operating_revenue", StatementType.INCOME_STATEMENT, ("ifrs-full:Revenue", "dart:Revenue", "Revenue", "ifrs_Revenue"), 10),
    MetricRule("gross_profit", StatementType.INCOME_STATEMENT, ("ifrs-full:GrossProfit", "dart:GrossProfit", "GrossProfit")),
    MetricRule("cost_of_sales", StatementType.INCOME_STATEMENT, ("ifrs-full:CostOfSales", "dart:CostOfSales", "CostOfSales")),
    MetricRule("operating_profit", StatementType.INCOME_STATEMENT, ("ifrs-full:ProfitLossFromOperatingActivities", "dart:OperatingIncomeLoss", "OperatingIncomeLoss", "OperatingProfit"), 10),
    MetricRule("total_profit", StatementType.INCOME_STATEMENT, ("ifrs-full:ProfitLossBeforeTax", "dart:ProfitLossBeforeTax", "ProfitLossBeforeTax")),
    MetricRule("income_tax_expense", StatementType.INCOME_STATEMENT, ("ifrs-full:TaxExpenseIncome", "dart:IncomeTaxExpense", "IncomeTaxExpense")),
    MetricRule("net_profit", StatementType.INCOME_STATEMENT, ("ifrs-full:ProfitLoss", "dart:ProfitLoss", "ProfitLoss", "NetIncome"), 10),
    MetricRule("parent_net_profit", StatementType.INCOME_STATEMENT, ("ifrs-full:ProfitLossAttributableToOwnersOfParent", "dart:ProfitLossAttributableToOwnersOfParent", "ProfitLossAttributableToOwnersOfParent")),
    MetricRule("nci_profit", StatementType.INCOME_STATEMENT, ("ifrs-full:ProfitLossAttributableToNoncontrollingInterests", "dart:ProfitLossAttributableToNonControllingInterests", "NonControllingInterestsProfitLoss")),
    MetricRule("total_assets", StatementType.BALANCE_SHEET, ("ifrs-full:Assets", "dart:Assets", "Assets"), 10),
    MetricRule("current_assets", StatementType.BALANCE_SHEET, ("ifrs-full:CurrentAssets", "dart:CurrentAssets", "CurrentAssets")),
    MetricRule("non_current_assets", StatementType.BALANCE_SHEET, ("ifrs-full:NoncurrentAssets", "dart:NoncurrentAssets", "NoncurrentAssets")),
    MetricRule("total_liabilities", StatementType.BALANCE_SHEET, ("ifrs-full:Liabilities", "dart:Liabilities", "Liabilities"), 10),
    MetricRule("current_liabilities", StatementType.BALANCE_SHEET, ("ifrs-full:CurrentLiabilities", "dart:CurrentLiabilities", "CurrentLiabilities")),
    MetricRule("non_current_liabilities", StatementType.BALANCE_SHEET, ("ifrs-full:NoncurrentLiabilities", "dart:NoncurrentLiabilities", "NoncurrentLiabilities")),
    MetricRule("total_equity", StatementType.BALANCE_SHEET, ("ifrs-full:Equity", "dart:Equity", "Equity"), 10),
    MetricRule("parent_equity", StatementType.BALANCE_SHEET, ("ifrs-full:EquityAttributableToOwnersOfParent", "dart:EquityAttributableToOwnersOfParent", "EquityAttributableToOwnersOfParent")),
    MetricRule("nci_equity", StatementType.BALANCE_SHEET, ("ifrs-full:NoncontrollingInterests", "dart:NonControllingInterests", "NonControllingInterests")),
    MetricRule("total_liabilities_and_equity", StatementType.BALANCE_SHEET, ("ifrs-full:EquityAndLiabilities", "dart:EquityAndLiabilities", "EquityAndLiabilities")),
    MetricRule("cash_and_cash_equivalents", StatementType.BALANCE_SHEET, ("ifrs-full:CashAndCashEquivalents", "dart:CashAndCashEquivalents", "CashAndCashEquivalents")),
    MetricRule("trade_receivables", StatementType.BALANCE_SHEET, ("ifrs-full:TradeAndOtherCurrentReceivables", "dart:TradeAndOtherCurrentReceivables", "TradeAndOtherCurrentReceivables")),
    MetricRule("inventories", StatementType.BALANCE_SHEET, ("ifrs-full:Inventories", "dart:Inventories", "Inventories")),
    MetricRule("property_plant_equipment", StatementType.BALANCE_SHEET, ("ifrs-full:PropertyPlantAndEquipment", "dart:PropertyPlantAndEquipment", "PropertyPlantAndEquipment")),
    MetricRule("borrowings", StatementType.BALANCE_SHEET, ("ifrs-full:Borrowings", "dart:Borrowings", "Borrowings")),
    MetricRule("lease_liabilities", StatementType.BALANCE_SHEET, ("ifrs-full:LeaseLiabilities", "dart:LeaseLiabilities", "LeaseLiabilities")),
    MetricRule("operating_cash_flow_net", StatementType.CASH_FLOW_STATEMENT, ("ifrs-full:CashFlowsFromUsedInOperatingActivities", "dart:CashFlowsFromUsedInOperatingActivities", "CashFlowsFromUsedInOperatingActivities"), 10),
    MetricRule("investing_cash_flow_net", StatementType.CASH_FLOW_STATEMENT, ("ifrs-full:CashFlowsFromUsedInInvestingActivities", "dart:CashFlowsFromUsedInInvestingActivities", "CashFlowsFromUsedInInvestingActivities")),
    MetricRule("financing_cash_flow_net", StatementType.CASH_FLOW_STATEMENT, ("ifrs-full:CashFlowsFromUsedInFinancingActivities", "dart:CashFlowsFromUsedInFinancingActivities", "CashFlowsFromUsedInFinancingActivities")),
    MetricRule("cash_equivalents_net_increase", StatementType.CASH_FLOW_STATEMENT, ("ifrs-full:IncreaseDecreaseInCashAndCashEquivalents", "dart:IncreaseDecreaseInCashAndCashEquivalents", "IncreaseDecreaseInCashAndCashEquivalents")),
    MetricRule("fx_effect_cash", StatementType.CASH_FLOW_STATEMENT, ("ifrs-full:EffectOfExchangeRateChangesOnCashAndCashEquivalents", "dart:EffectOfExchangeRateChangesOnCashAndCashEquivalents", "EffectOfExchangeRateChangesOnCashAndCashEquivalents")),
    MetricRule("cash_equivalents_beginning", StatementType.CASH_FLOW_STATEMENT, ("dart:CashAndCashEquivalentsAtBeginningOfPeriod", "CashAndCashEquivalentsAtBeginningOfPeriod")),
    MetricRule("cash_equivalents_ending", StatementType.CASH_FLOW_STATEMENT, ("ifrs-full:CashAndCashEquivalents", "dart:CashAndCashEquivalentsAtEndOfPeriod", "CashAndCashEquivalentsAtEndOfPeriod")),
    MetricRule("capital_expenditure", StatementType.CASH_FLOW_STATEMENT, ("ifrs-full:PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities", "dart:PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquirePropertyPlantAndEquipment")),
    MetricRule("basic_eps", StatementType.KEY_METRICS, ("ifrs-full:BasicEarningsLossPerShare", "dart:BasicEarningsLossPerShare", "BasicEarningsLossPerShare")),
    MetricRule("diluted_eps", StatementType.KEY_METRICS, ("ifrs-full:DilutedEarningsLossPerShare", "dart:DilutedEarningsLossPerShare", "DilutedEarningsLossPerShare")),
)


KR_LABEL_RULES: tuple[MetricRule, ...] = (
    MetricRule("operating_revenue", StatementType.INCOME_STATEMENT, ("매출액", "영업수익", "수익", "revenue", "sales"), 10),
    MetricRule("gross_profit", StatementType.INCOME_STATEMENT, ("매출총이익", "gross profit")),
    MetricRule("cost_of_sales", StatementType.INCOME_STATEMENT, ("매출원가", "cost of sales")),
    MetricRule("operating_profit", StatementType.INCOME_STATEMENT, ("영업이익", "영업손익", "operating profit", "operating income"), 10),
    MetricRule(
        "total_profit",
        StatementType.INCOME_STATEMENT,
        (
            "법인세비용차감전순이익",
            "법인세비용차감전순손익",
            "법인세비용차감전당기순손익",
            "법인세비용차감전계속영업순이익",
            "세전이익",
            "profit before tax",
        ),
    ),
    MetricRule("income_tax_expense", StatementType.INCOME_STATEMENT, ("법인세비용", "income tax expense")),
    MetricRule("net_profit", StatementType.INCOME_STATEMENT, ("당기순이익", "분기순이익", "연결당기순이익", "profit for the period", "net income"), 10),
    MetricRule("parent_net_profit", StatementType.INCOME_STATEMENT, ("지배기업의 소유주에게 귀속되는 당기순이익", "지배기업소유주지분", "owners of parent")),
    MetricRule("nci_profit", StatementType.INCOME_STATEMENT, ("비지배지분에 귀속되는 당기순이익", "비지배지분", "non-controlling interests")),
    MetricRule("total_assets", StatementType.BALANCE_SHEET, ("자산총계", "자산 총계", "total assets"), 10),
    MetricRule("current_assets", StatementType.BALANCE_SHEET, ("유동자산", "current assets")),
    MetricRule("non_current_assets", StatementType.BALANCE_SHEET, ("비유동자산", "non-current assets")),
    MetricRule("total_liabilities", StatementType.BALANCE_SHEET, ("부채총계", "부채 총계", "total liabilities"), 10),
    MetricRule("current_liabilities", StatementType.BALANCE_SHEET, ("유동부채", "current liabilities")),
    MetricRule("non_current_liabilities", StatementType.BALANCE_SHEET, ("비유동부채", "non-current liabilities")),
    MetricRule("total_equity", StatementType.BALANCE_SHEET, ("자본총계", "자본 총계", "total equity"), 10),
    MetricRule("parent_equity", StatementType.BALANCE_SHEET, ("지배기업지분", "지배기업 소유주지분", "지배기업의 소유주에게 귀속되는 자본", "equity attributable to owners")),
    MetricRule("nci_equity", StatementType.BALANCE_SHEET, ("비지배지분", "non-controlling interests")),
    MetricRule("total_liabilities_and_equity", StatementType.BALANCE_SHEET, ("부채와자본총계", "부채및자본총계", "total liabilities and equity")),
    MetricRule("cash_and_cash_equivalents", StatementType.BALANCE_SHEET, ("현금및현금성자산", "현금 및 현금성자산", "cash and cash equivalents")),
    MetricRule("trade_receivables", StatementType.BALANCE_SHEET, ("매출채권", "매출채권및기타채권", "trade receivables")),
    MetricRule("inventories", StatementType.BALANCE_SHEET, ("재고자산", "inventories")),
    MetricRule("property_plant_equipment", StatementType.BALANCE_SHEET, ("유형자산", "property plant and equipment")),
    MetricRule("borrowings", StatementType.BALANCE_SHEET, ("차입금", "borrowings")),
    MetricRule("lease_liabilities", StatementType.BALANCE_SHEET, ("리스부채", "lease liabilities")),
    MetricRule("operating_cash_flow_net", StatementType.CASH_FLOW_STATEMENT, ("영업활동현금흐름", "영업활동으로 인한 현금흐름", "영업활동으로부터의 현금흐름", "net cash provided by operating activities"), 10),
    MetricRule("investing_cash_flow_net", StatementType.CASH_FLOW_STATEMENT, ("투자활동현금흐름", "투자활동으로 인한 현금흐름", "투자활동으로부터의 현금흐름", "net cash used in investing activities")),
    MetricRule("financing_cash_flow_net", StatementType.CASH_FLOW_STATEMENT, ("재무활동현금흐름", "재무활동으로 인한 현금흐름", "재무활동으로부터의 현금흐름", "net cash used in financing activities")),
    MetricRule("cash_equivalents_net_increase", StatementType.CASH_FLOW_STATEMENT, ("현금및현금성자산의 증가", "현금및현금성자산의순증감", "현금및현금성자산의 순증가", "현금및현금성자산의 순증가감소", "net increase in cash and cash equivalents")),
    MetricRule("cash_equivalents_beginning", StatementType.CASH_FLOW_STATEMENT, ("기초현금및현금성자산", "기초의 현금및현금성자산", "cash and cash equivalents at beginning")),
    MetricRule("cash_equivalents_ending", StatementType.CASH_FLOW_STATEMENT, ("기말현금및현금성자산", "기말의 현금및현금성자산", "cash and cash equivalents at end")),
    MetricRule("basic_eps", StatementType.KEY_METRICS, ("기본주당이익", "basic earnings per share")),
    MetricRule("diluted_eps", StatementType.KEY_METRICS, ("희석주당이익", "diluted earnings per share")),
)


KR_RULE_BY_CONCEPT = {normalize_concept(label): rule for rule in KR_CONCEPT_RULES for label in rule.labels}
KR_RULE_BY_LABEL = {compact_label(label): rule for rule in KR_LABEL_RULES for label in rule.labels}


def find_kr_concept_rule(concept: str) -> MetricRule | None:
    return KR_RULE_BY_CONCEPT.get(normalize_concept(concept))


def find_kr_label_rule(label: str) -> MetricRule | None:
    normalized = compact_label(label)
    if normalized in KR_RULE_BY_LABEL:
        rule = KR_RULE_BY_LABEL[normalized]
        return rule if _rule_allowed(rule, normalized) else None
    matches: list[tuple[int, int, MetricRule]] = []
    for key, rule in KR_RULE_BY_LABEL.items():
        if key and key in normalized and _rule_allowed(rule, normalized):
            matches.append((len(key), -rule.priority, rule))
    if not matches:
        return None
    return max(matches, key=lambda item: (item[0], item[1]))[2]


def _rule_allowed(rule: MetricRule, normalized: str) -> bool:
    if rule.canonical_name == "operating_revenue":
        if any(
            token in normalized
            for token in (
                "법인세",
                "비용",
                "비율",
                "수익률",
                "미수수익",
                "선수수익",
                "이연수익",
                "배당금수익",
                "이자수익",
                "금융수익",
                "기타수익",
                "외환차익",
                "임대료수익",
                "공정가치",
            )
        ):
            return False
    if rule.canonical_name == "income_tax_expense":
        if any(token in normalized for token in ("차감전", "beforetax")):
            return False
    return True
