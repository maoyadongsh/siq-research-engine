from __future__ import annotations

from ...models import StatementType
from ...normalization import compact_label, normalize_concept
from ..base import MetricRule


JP_CONCEPT_RULES: tuple[MetricRule, ...] = (
    MetricRule("operating_revenue", StatementType.INCOME_STATEMENT, ("ifrs-full:Revenue", "jpcrp_cor:NetSales", "jppfs_cor:NetSales", "RevenueIFRS", "NetSales"), 10),
    MetricRule("gross_profit", StatementType.INCOME_STATEMENT, ("ifrs-full:GrossProfit", "jpcrp_cor:GrossProfit", "jppfs_cor:GrossProfit", "GrossProfit")),
    MetricRule("cost_of_sales", StatementType.INCOME_STATEMENT, ("ifrs-full:CostOfSales", "jpcrp_cor:CostOfSales", "jppfs_cor:CostOfSales", "CostOfSales")),
    MetricRule("operating_profit", StatementType.INCOME_STATEMENT, ("ifrs-full:ProfitLossFromOperatingActivities", "jpcrp_cor:OperatingIncome", "jppfs_cor:OperatingIncome", "OperatingIncome", "OperatingProfit"), 10),
    MetricRule("total_profit", StatementType.INCOME_STATEMENT, ("ifrs-full:ProfitLossBeforeTax", "jpcrp_cor:OrdinaryIncome", "jppfs_cor:OrdinaryIncome", "OrdinaryIncome", "IncomeBeforeIncomeTaxes")),
    MetricRule("income_tax_expense", StatementType.INCOME_STATEMENT, ("ifrs-full:TaxExpenseIncome", "jpcrp_cor:IncomeTaxes", "jppfs_cor:IncomeTaxes", "IncomeTaxes")),
    MetricRule("net_profit", StatementType.INCOME_STATEMENT, ("ifrs-full:ProfitLoss", "jpcrp_cor:ProfitLoss", "jppfs_cor:ProfitLoss", "ProfitLoss", "NetIncome"), 10),
    MetricRule("parent_net_profit", StatementType.INCOME_STATEMENT, ("ifrs-full:ProfitLossAttributableToOwnersOfParent", "jpcrp_cor:ProfitLossAttributableToOwnersOfParent", "ProfitLossAttributableToOwnersOfParent")),
    MetricRule("nci_profit", StatementType.INCOME_STATEMENT, ("ifrs-full:ProfitLossAttributableToNoncontrollingInterests", "jpcrp_cor:ProfitLossAttributableToNonControllingInterests", "NonControllingInterestsProfitLoss")),
    MetricRule("total_assets", StatementType.BALANCE_SHEET, ("ifrs-full:Assets", "jpcrp_cor:Assets", "jppfs_cor:Assets", "Assets"), 10),
    MetricRule("current_assets", StatementType.BALANCE_SHEET, ("ifrs-full:CurrentAssets", "jpcrp_cor:CurrentAssets", "jppfs_cor:CurrentAssets", "CurrentAssets")),
    MetricRule("non_current_assets", StatementType.BALANCE_SHEET, ("ifrs-full:NoncurrentAssets", "jpcrp_cor:NoncurrentAssets", "jppfs_cor:NoncurrentAssets", "NoncurrentAssets")),
    MetricRule("total_liabilities", StatementType.BALANCE_SHEET, ("ifrs-full:Liabilities", "jpcrp_cor:Liabilities", "jppfs_cor:Liabilities", "Liabilities"), 10),
    MetricRule("current_liabilities", StatementType.BALANCE_SHEET, ("ifrs-full:CurrentLiabilities", "jpcrp_cor:CurrentLiabilities", "jppfs_cor:CurrentLiabilities", "CurrentLiabilities")),
    MetricRule("non_current_liabilities", StatementType.BALANCE_SHEET, ("ifrs-full:NoncurrentLiabilities", "jpcrp_cor:NoncurrentLiabilities", "jppfs_cor:NoncurrentLiabilities", "NoncurrentLiabilities")),
    MetricRule("total_equity", StatementType.BALANCE_SHEET, ("ifrs-full:Equity", "jpcrp_cor:NetAssets", "jppfs_cor:NetAssets", "NetAssets", "Equity"), 10),
    MetricRule("parent_equity", StatementType.BALANCE_SHEET, ("ifrs-full:EquityAttributableToOwnersOfParent", "jpcrp_cor:EquityAttributableToOwnersOfParent", "ShareholdersEquity")),
    MetricRule("nci_equity", StatementType.BALANCE_SHEET, ("ifrs-full:NoncontrollingInterests", "jpcrp_cor:NonControllingInterests", "NonControllingInterests")),
    MetricRule("total_liabilities_and_equity", StatementType.BALANCE_SHEET, ("ifrs-full:EquityAndLiabilities", "jpcrp_cor:LiabilitiesAndNetAssets", "jppfs_cor:LiabilitiesAndNetAssets", "LiabilitiesAndNetAssets")),
    MetricRule("cash_and_cash_equivalents", StatementType.BALANCE_SHEET, ("ifrs-full:CashAndCashEquivalents", "jpcrp_cor:CashAndDeposits", "jppfs_cor:CashAndDeposits", "CashAndDeposits", "CashAndCashEquivalents")),
    MetricRule("trade_receivables", StatementType.BALANCE_SHEET, ("ifrs-full:TradeAndOtherCurrentReceivables", "jpcrp_cor:NotesAndAccountsReceivableTrade", "jppfs_cor:NotesAndAccountsReceivableTrade", "AccountsReceivableTrade")),
    MetricRule("inventories", StatementType.BALANCE_SHEET, ("ifrs-full:Inventories", "jpcrp_cor:Inventories", "jppfs_cor:Inventories", "Inventories")),
    MetricRule("property_plant_equipment", StatementType.BALANCE_SHEET, ("ifrs-full:PropertyPlantAndEquipment", "jpcrp_cor:PropertyPlantAndEquipment", "jppfs_cor:PropertyPlantAndEquipment", "PropertyPlantAndEquipment")),
    MetricRule("borrowings", StatementType.BALANCE_SHEET, ("ifrs-full:Borrowings", "jpcrp_cor:ShortTermBorrowings", "jpcrp_cor:LongTermBorrowings", "Borrowings")),
    MetricRule("operating_cash_flow_net", StatementType.CASH_FLOW_STATEMENT, ("ifrs-full:CashFlowsFromUsedInOperatingActivities", "jpcrp_cor:NetCashProvidedByUsedInOperatingActivities", "jppfs_cor:NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivities"), 10),
    MetricRule("investing_cash_flow_net", StatementType.CASH_FLOW_STATEMENT, ("ifrs-full:CashFlowsFromUsedInInvestingActivities", "jpcrp_cor:NetCashProvidedByUsedInInvestingActivities", "jppfs_cor:NetCashProvidedByUsedInInvestingActivities", "NetCashProvidedByUsedInInvestingActivities")),
    MetricRule("financing_cash_flow_net", StatementType.CASH_FLOW_STATEMENT, ("ifrs-full:CashFlowsFromUsedInFinancingActivities", "jpcrp_cor:NetCashProvidedByUsedInFinancingActivities", "jppfs_cor:NetCashProvidedByUsedInFinancingActivities", "NetCashProvidedByUsedInFinancingActivities")),
    MetricRule("cash_equivalents_net_increase", StatementType.CASH_FLOW_STATEMENT, ("ifrs-full:IncreaseDecreaseInCashAndCashEquivalents", "jpcrp_cor:NetIncreaseDecreaseInCashAndCashEquivalents", "NetIncreaseDecreaseInCashAndCashEquivalents")),
    MetricRule("fx_effect_cash", StatementType.CASH_FLOW_STATEMENT, ("ifrs-full:EffectOfExchangeRateChangesOnCashAndCashEquivalents", "jpcrp_cor:EffectOfExchangeRateChangeOnCashAndCashEquivalents", "EffectOfExchangeRateChangeOnCashAndCashEquivalents")),
    MetricRule("cash_equivalents_beginning", StatementType.CASH_FLOW_STATEMENT, ("jpcrp_cor:CashAndCashEquivalentsAtBeginningOfPeriod", "CashAndCashEquivalentsAtBeginningOfPeriod")),
    MetricRule("cash_equivalents_ending", StatementType.CASH_FLOW_STATEMENT, ("ifrs-full:CashAndCashEquivalents", "jpcrp_cor:CashAndCashEquivalentsAtEndOfPeriod", "CashAndCashEquivalentsAtEndOfPeriod")),
    MetricRule("basic_eps", StatementType.KEY_METRICS, ("ifrs-full:BasicEarningsLossPerShare", "jpcrp_cor:BasicEarningsLossPerShare", "BasicEarningsLossPerShare")),
    MetricRule("diluted_eps", StatementType.KEY_METRICS, ("ifrs-full:DilutedEarningsLossPerShare", "jpcrp_cor:DilutedEarningsLossPerShare", "DilutedEarningsLossPerShare")),
)


JP_LABEL_RULES: tuple[MetricRule, ...] = (
    MetricRule("operating_revenue", StatementType.INCOME_STATEMENT, ("売上収益", "売上高", "営業収益", "収益", "revenue", "net sales"), 10),
    MetricRule("gross_profit", StatementType.INCOME_STATEMENT, ("売上総利益", "gross profit")),
    MetricRule("cost_of_sales", StatementType.INCOME_STATEMENT, ("売上原価", "cost of sales")),
    MetricRule("operating_profit", StatementType.INCOME_STATEMENT, ("営業利益", "営業損益", "operating profit", "operating income"), 10),
    MetricRule("total_profit", StatementType.INCOME_STATEMENT, ("税引前利益", "経常利益", "profit before tax", "profit before income taxes", "income before income taxes", "income before income tax", "ordinary income")),
    MetricRule("income_tax_expense", StatementType.INCOME_STATEMENT, ("法人所得税費用", "法人税等", "income tax")),
    MetricRule("net_profit", StatementType.INCOME_STATEMENT, ("当期利益", "当期純利益", "親会社株主に帰属する当期純利益", "profit for the year", "net income"), 10),
    MetricRule("parent_net_profit", StatementType.INCOME_STATEMENT, ("親会社の所有者に帰属する当期利益", "親会社株主に帰属する当期純利益", "profit attributable to owners of parent", "profit attributable to owners of the parent", "net income attributable", "owners of parent")),
    MetricRule("nci_profit", StatementType.INCOME_STATEMENT, ("非支配持分に帰属する当期利益", "non-controlling interests")),
    MetricRule("total_assets", StatementType.BALANCE_SHEET, ("資産合計", "総資産", "total assets"), 10),
    MetricRule("current_assets", StatementType.BALANCE_SHEET, ("流動資産", "current assets")),
    MetricRule("non_current_assets", StatementType.BALANCE_SHEET, ("非流動資産", "固定資産", "non-current assets")),
    MetricRule("total_liabilities", StatementType.BALANCE_SHEET, ("負債合計", "total liabilities"), 10),
    MetricRule("current_liabilities", StatementType.BALANCE_SHEET, ("流動負債", "current liabilities")),
    MetricRule("non_current_liabilities", StatementType.BALANCE_SHEET, ("非流動負債", "固定負債", "non-current liabilities")),
    MetricRule("total_equity", StatementType.BALANCE_SHEET, ("資本合計", "純資産合計", "total equity", "net assets"), 10),
    MetricRule("parent_equity", StatementType.BALANCE_SHEET, ("親会社の所有者に帰属する持分", "株主資本合計", "equity attributable to owners")),
    MetricRule("nci_equity", StatementType.BALANCE_SHEET, ("非支配持分", "non-controlling interests")),
    MetricRule("total_liabilities_and_equity", StatementType.BALANCE_SHEET, ("負債及び資本合計", "負債純資産合計", "total liabilities and equity")),
    MetricRule("cash_and_cash_equivalents", StatementType.BALANCE_SHEET, ("現金及び現金同等物", "現金及び預金", "cash and cash equivalents")),
    MetricRule("trade_receivables", StatementType.BALANCE_SHEET, ("売上債権", "受取手形及び売掛金", "trade receivables")),
    MetricRule("inventories", StatementType.BALANCE_SHEET, ("棚卸資産", "inventories")),
    MetricRule("property_plant_equipment", StatementType.BALANCE_SHEET, ("有形固定資産", "property plant and equipment")),
    MetricRule("borrowings", StatementType.BALANCE_SHEET, ("借入金", "borrowings")),
    MetricRule("operating_cash_flow_net", StatementType.CASH_FLOW_STATEMENT, ("営業活動によるキャッシュフロー", "営業活動によるキャッシュ・フロー", "net cash provided by operating activities", "net cash generated by operating activities", "cash generated by operating activities", "cash flows from operating activities"), 10),
    MetricRule("investing_cash_flow_net", StatementType.CASH_FLOW_STATEMENT, ("投資活動によるキャッシュフロー", "投資活動によるキャッシュ・フロー", "net cash used in investing activities", "net cash used in generated by investing activities", "net cash generated by investing activities", "cash flows from investing activities")),
    MetricRule("financing_cash_flow_net", StatementType.CASH_FLOW_STATEMENT, ("財務活動によるキャッシュフロー", "財務活動によるキャッシュ・フロー", "net cash used in financing activities", "net cash provided by financing activities", "net cash generated by financing activities", "cash flows from financing activities")),
    MetricRule("cash_equivalents_net_increase", StatementType.CASH_FLOW_STATEMENT, ("現金及び現金同等物の増減額", "net increase in cash and cash equivalents")),
    MetricRule("cash_equivalents_beginning", StatementType.CASH_FLOW_STATEMENT, ("現金及び現金同等物の期首残高", "cash and cash equivalents at beginning")),
    MetricRule("cash_equivalents_ending", StatementType.CASH_FLOW_STATEMENT, ("現金及び現金同等物の期末残高", "cash and cash equivalents at end")),
    MetricRule("basic_eps", StatementType.KEY_METRICS, ("基本的1株当たり当期利益", "基本的1株当たり利益", "basic earnings per share")),
    MetricRule("diluted_eps", StatementType.KEY_METRICS, ("希薄化後1株当たり当期利益", "diluted earnings per share")),
)


JP_RULE_BY_CONCEPT = {normalize_concept(label): rule for rule in JP_CONCEPT_RULES for label in rule.labels}
JP_RULE_BY_LABEL = {compact_label(label): rule for rule in JP_LABEL_RULES for label in rule.labels}


def find_jp_concept_rule(concept: str) -> MetricRule | None:
    return JP_RULE_BY_CONCEPT.get(normalize_concept(concept))


def find_jp_label_rule(label: str) -> MetricRule | None:
    normalized = compact_label(label)
    if normalized in JP_RULE_BY_LABEL:
        return JP_RULE_BY_LABEL[normalized]
    matches: list[tuple[int, int, MetricRule]] = []
    for key, rule in JP_RULE_BY_LABEL.items():
        if key and key in normalized:
            matches.append((len(key), -rule.priority, rule))
    if not matches:
        return None
    return max(matches)[2]
