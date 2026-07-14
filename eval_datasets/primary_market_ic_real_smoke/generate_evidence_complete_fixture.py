#!/usr/bin/env python3
"""Generate the evidence-complete synthetic PMIC positive smoke fixture.

The fixture is deliberately deterministic and self-contained.  It represents
no real issuer, counterparty, transaction, market study, or legal opinion.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
DEAL_ID = "DEAL-PMIC-POSITIVE-COND-2026"
TARGET = ROOT / DEAL_ID
DOCUMENT_ID = "DOC-PMICPOSCOND2026A1"
PARSE_RUN_ID = "PRUN-20260714-PMICPOSCOND001"
SOURCE_ID = f"PM:{DEAL_ID}:{DOCUMENT_ID}:{PARSE_RUN_ID}"
CREATED_AT = "2026-07-14T00:00:00Z"
COMPANY_NAME = "启衡（纯合成）"
INDUSTRY = "先进封装检测"
SOURCE_PATH = (
    f"parsed_documents/{DOCUMENT_ID}/runs/{PARSE_RUN_ID}/content_list_enhanced.json"
)
ARCHIVE_PATH = f"parsed_documents/{DOCUMENT_ID}/runs/{PARSE_RUN_ID}/archive_manifest.json"
NOTICE = (
    "SYNTHETIC EVALUATION ONLY: every issuer, counterparty, figure, test, opinion, "
    "approval and transaction term in this package is fictitious."
)

COORDINATOR = "siq_ic_master_coordinator"
CHAIRMAN = "siq_ic_chairman"
STRATEGIST = "siq_ic_strategist"
SECTOR = "siq_ic_sector_expert"
FINANCE = "siq_ic_finance_auditor"
LEGAL = "siq_ic_legal_scanner"
RISK = "siq_ic_risk_controller"

ROLE_HINTS = {
    "business": [STRATEGIST, SECTOR],
    "finance": [FINANCE],
    "legal": [LEGAL],
    "risk": [RISK],
}
EXECUTIVE_TRACE_CODES = {
    "BUS-003",
    "BUS-008",
    "FIN-009",
    "FIN-010",
    "LEG-004",
    "LEG-010",
    "RSK-004",
    "RSK-010",
}


def _json_text(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n"


def _sha256(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _spec(
    code: str,
    dimension: str,
    topic: str,
    page: int,
    section: str,
    claim: str,
    quote: str,
    verification: str,
    *,
    materiality: str = "material",
) -> dict[str, Any]:
    return {
        "code": code,
        "dimension": dimension,
        "topic": topic,
        "page": page,
        "section": section,
        "claim": claim,
        "quote": quote,
        "verification": verification,
        "materiality": materiality,
    }


EVIDENCE_SPECS = [
    _spec(
        "BUS-001",
        "business",
        "product_revenue_identity",
        68,
        "产品与收入构成",
        "2025年收入8.00亿元由先进封装AOI设备、量测设备和服务收入完整构成。",
        "2025年度收入为800百万元，其中先进封装AOI设备560百万元、量测设备160百万元、维保及软件服务80百万元，560+160+80=800；设备收入720百万元均来自本样本定义的目标市场。",
        "审计收入明细账、发票、验收单及产品台账逐项勾稽（全部为合成评测记录）",
    ),
    _spec(
        "BUS-002",
        "business",
        "bottom_up_tam",
        92,
        "全球TAM自下而上测算",
        "2025年目标产品全球TAM为72.90亿元，测算口径和乘法均已给出。",
        "第三方合成行业底稿覆盖86条先进封装产线，推算2025年全球可采购检测及量测系统1,620台，验证后的平均单价4.50百万元/台；1,620×4.50=7,290百万元TAM，未计入传统封装和纯软件。",
        "86条产线访谈样本、设备采购记录和单价发票三角验证（合成）",
    ),
    _spec(
        "BUS-003",
        "business",
        "bottom_up_sam_som",
        96,
        "中国SAM与SOM",
        "2025年中国SAM为30.885亿元，公司设备SOM为23.3%，边界与分母一致。",
        "中国境内符合工艺节点、客户资质和交付半径条件的采购量为710台，平均单价4.35百万元，710×4.35=3,088.5百万元SAM；公司同口径设备收入720百万元，720÷3,088.5=23.3%实际SOM。",
        "46家客户采购清单与公司设备收入按同一SKU口径重算（合成）",
    ),
    _spec(
        "BUS-004",
        "business",
        "market_growth_sensitivity",
        101,
        "市场增长与敏感性",
        "目标市场2025至2028年采购量基准CAGR为9.0%，上下行情景均有独立数量假设。",
        "已核验客户扩产计划对应采购量由2025年710台增至2028年920台，三年CAGR为9.0%；剔除未获批产线后的下行情景为2028年790台（3.6% CAGR），加计两条已批先进封装线的上行情景为1,010台（12.5% CAGR）。",
        "客户董事会批复、建设进度与设备BOM需求映射（合成）",
    ),
    _spec(
        "BUS-005",
        "business",
        "competitor_share",
        112,
        "竞争份额",
        "2025年中国同口径市场份额完整加总至100%，公司排名第二。",
        "2025年中国SAM份额为：合成竞争对手A 26.5%、启衡23.3%、合成竞争对手B 19.8%、合成竞争对手C 12.4%、其他厂商18.0%，合计100.0%；份额依据46家客户已验收设备金额而非厂商口径。",
        "客户侧验收金额、海关合成记录与厂商序列号去重（合成）",
    ),
    _spec(
        "BUS-006",
        "business",
        "product_performance",
        124,
        "产品性能与客户验证",
        "核心设备在六家客户产线的检测率、误报率、吞吐和可用率均完成对标验证。",
        "六家客户连续90天验收结果：缺陷检出率99.45%、误报率0.62%、吞吐125片/小时、设备可用率98.7%；三家主要竞争产品区间分别为99.10%-99.40%、0.70%-1.00%、112-122片/小时和97.6%-98.5%。",
        "客户FAT/SAT报告、盲样复测和产线日志联合验证（合成）",
    ),
    _spec(
        "BUS-007",
        "business",
        "customers_and_retention",
        139,
        "客户结构与留存",
        "客户集中度、复购率和流失原因均已穿透至客户确认。",
        "2025年共有18家收入客户，第一大客户占14.0%、前五大占48.0%；过去24个月设备客户复购或续约率92.0%。唯一流失客户贡献2024年收入1.6%，因其产线关停而非产品替换；18家客户余额与交易额均已函证。",
        "全量客户收入台账、函证回函和流失访谈（合成）",
    ),
    _spec(
        "BUS-008",
        "business",
        "orders_and_conversion",
        146,
        "在手订单与转化",
        "在手订单、交付期、定金、取消率和框架订单边界均已核验。",
        "截至2026-03-31不可撤销在手订单620百万元，其中527百万元约定12个月内交付，已收定金形成合同负债72百万元；客户书面确认覆盖98.1%，历史订单取消率2.1%。另有180百万元框架意向未计入在手订单或预测的确定订单。",
        "合同逐份审阅、客户函证、定金银行流水和历史取消台账（合成）",
    ),
    _spec(
        "BUS-009",
        "business",
        "unit_economics_and_delivery",
        158,
        "单机经济性与交付质量",
        "单机售价、毛利、验收周期和质保成本形成可复核单位经济模型。",
        "2025年交付160台设备、设备收入720百万元，平均单价4.50百万元/台；设备毛利率42.2%，中位验收周期21天，按期交付率96.8%，质保索赔成本占设备收入1.3%，均与总账及客户验收记录一致。",
        "序列号级收入成本表、验收日期和质保工单重算（合成）",
    ),
    _spec(
        "BUS-010",
        "business",
        "team_execution",
        171,
        "团队与执行记录",
        "核心团队履历、交付爬坡和继任安排均已验证。",
        "CEO赵岚和CTO陈序共同任职9年；CTO此前带队量产两代检测平台。2023-2025年交付量由102台、132台增至160台，研发里程碑按期完成率93%。CEO与CTO均签署至2029年的竞业、保密和知识产权协议，研发副总被董事会书面指定为CTO继任人。",
        "人事档案、前雇主核验、董事会纪要和交付台账（合成）",
    ),
    _spec(
        "FIN-001",
        "finance",
        "audit_scope_and_identity",
        302,
        "审计范围与报表身份",
        "2023至2025年三年合并财务报表为同一主体、同一口径并获无保留意见。",
        "合成审计底稿确认报表主体为启衡及两家全资子公司，合并范围三年未变；2023-2025年报表获无保留模拟审计意见，总账与三张主表勾稽差异为0，期后审计调整和未更正错报均为0。",
        "合并底稿、试算平衡表、审计调整汇总及模拟审计意见勾稽（合成）",
    ),
    _spec(
        "FIN-002",
        "finance",
        "income_statement_identity",
        310,
        "三年利润表",
        "三年利润表从收入到净利润逐层恒等且补助单列。",
        "百万元：2023收入480-成本288=毛利192，销售38+管理31+研发50+税附4+减值5=128，核心营业利润64+补助8-财务费2=税前70-所得税10=净利60；2024为620-366=254-155+10-2=107-15=92；2025为800-472=328-196+12-1=143-21=122。",
        "审计利润表、费用科目和非经常性损益逐项重算（合成）",
    ),
    _spec(
        "FIN-003",
        "finance",
        "balance_sheet_identity",
        318,
        "三年资产负债表",
        "三年资产负债表均满足资产等于负债加权益，2025年关键科目已拆分。",
        "百万元：2023流动资产330+非流动230=资产560，流动负债195+非流动55=负债250，权益310；2024为410+270=680、222+70=292、权益388；2025为500+320=820、270+70=340、权益480。2025流动资产含现金138、应收176、存货132、其他54；非流动含固定资产250、无形45、其他25。",
        "审计资产负债表、科目明细和权益变动表重算（合成）",
    ),
    _spec(
        "FIN-004",
        "finance",
        "cash_flow_identity",
        326,
        "三年现金流量表",
        "经营、投资、融资现金流与期初期末现金三年完整勾稽。",
        "百万元：2023经营现金流75+投资现金流-70+融资现金流19=净增24，现金66增至90；2024为105-92+15=28，现金90增至118；2025为134-126+12=20，现金118增至138。2025净利122至经营现金流134的调节项目净额为12。",
        "银行流水、现金流量表及间接法调节表重算（合成）",
    ),
    _spec(
        "FIN-005",
        "finance",
        "accounts_receivable",
        337,
        "应收账款质量",
        "应收账款账龄、减值、DSO、集中度和期后回款均已验证。",
        "2025年末应收原值185百万元，1年内166.5、1-2年13、2-3年4、3年以上1.5，减值准备9，净额176；前五大应收占41%。2025 DSO为[(142+176)÷2]÷800×365=72.5天；截至2026-03-31回款151百万元，占原值81.6%，无期后冲回。",
        "账龄表、发票、客户函证、期后银行回款和减值模型重算（合成）",
    ),
    _spec(
        "FIN-006",
        "finance",
        "revenue_quality_and_cutoff",
        346,
        "收入质量与截止性",
        "收入确认、截止性、退货和合同负债已完成全量高风险测试。",
        "设备在客户签署终验单且控制权转移时确认收入，服务按履约期间确认。审计抽取覆盖2025年收入78%的全部大额和期末样本，错期金额为0；期后90天退货/折让占收入0.4%。合同负债72百万元与BUS-008已收订单定金逐笔一致。",
        "合同、终验单、物流、发票、期后贷项通知和定金台账穿行测试（合成）",
    ),
    _spec(
        "FIN-007",
        "finance",
        "normalized_profitability",
        354,
        "正常化盈利",
        "政府补助影响已从盈利中剥离，正常化利润仍为正且可复算。",
        "2025年税前利润143百万元包含政府补助12百万元，适用税率15%；剔除补助后的正常化净利润=122-12×(1-15%)=111.8百万元。2025折旧摊销30百万元，报告EBITDA=营业利润144+30=174百万元，剔除补助后EBITDA为162百万元。",
        "补助文件、利润表、所得税测算和折旧摊销表重算（合成）",
    ),
    _spec(
        "FIN-008",
        "finance",
        "forecast_driver_trace",
        366,
        "经营预测驱动",
        "2026至2028年基准预测由交付台数、单价和服务收入驱动并与产能匹配。",
        "基准预测（百万元）：2026交付205台×4.40=设备902，加服务78得收入980、净利156；2027交付245台×4.45=1,090.25，加服务109.75得收入1,200、净利202；2028交付286台×4.50=1,287，加服务163得收入1,450、净利252；毛利率依次41.5%、42.0%、42.2%。",
        "订单交付桥、销售漏斗、ASP清单、服务合同和产能排程重算（合成）",
    ),
    _spec(
        "FIN-009",
        "finance",
        "forecast_scenarios_and_dcf",
        378,
        "情景预测与DCF",
        "上下行预测、FCFF、WACC、永续增长和净现金形成完整DCF轨迹。",
        "收入/净利基准情景为2026-2028年980/156、1,200/202、1,450/252；上行为1,100/187、1,390/251、1,710/322；下行为790/86、860/97、970/116。基准FCFF 2026-2030年为150/200/260/330/400，WACC 11.5%、永续增长3.5%、净现金73，股权价值4,004百万元；敏感区间3,586-4,619百万元。",
        "三情景模型、营运资本/资本开支桥和DCF公式复算（合成）",
    ),
    _spec(
        "FIN-010",
        "finance",
        "valuation_and_return_trace",
        389,
        "交易估值与回报",
        "交易价格、可比估值、持股比例及基准/下行回报均可复算。",
        "拟投240百万元，投前估值3,600、投后3,840百万元；每股15元，对应新增16百万股和投后6.25%持股。投前估值÷2026基准净利156=23.1倍，低于可比中位数26.0倍对应4,056百万元。2029基准退出估值9,000、再稀释10%后持股5.625%，价值506.25、MOIC 2.11倍、四年IRR 20.5%；下行4,500估值对应MOIC 1.05倍。",
        "签署版合成条款清单、股本模型、可比公司口径和退出瀑布复算（合成）",
    ),
    _spec(
        "LEG-001",
        "legal",
        "ownership_and_control",
        42,
        "股权与控制权",
        "投前投后股权、实际控制人和投资人持股均已穿透并可复算。",
        "投前240百万股：赵岚38%、陈序5%、员工平台12%、合成A轮基金18%、合成B轮基金15%、其他股东12%，合计100%；赵岚与陈序为配偶并签一致行动协议，控制43%。新增16百万股后投资人持股6.25%，一致行动人合计40.31%，不存在代持、质押或未披露受益所有人。",
        "工商档案、股东名册、出资流水和受益所有人声明穿透（合成）",
    ),
    _spec(
        "LEG-002",
        "legal",
        "transaction_approvals",
        51,
        "融资与扩产批准",
        "本轮融资、股份发行和分期扩产已取得全部内部批准并处理优先权。",
        "2026-06-20董事会7/7通过融资和分期扩产，2026-06-28股东会92.4%表决权通过；全体老股东书面放弃优先认购权，员工平台完成内部授权。境内人民币基金投资不触发外商投资审查，市场监管变更登记为交割后10个工作日义务。",
        "董事会/股东会决议、放弃函、基金主体证明和登记清单审阅（合成）",
    ),
    _spec(
        "LEG-003",
        "legal",
        "licenses_and_regulatory",
        213,
        "生产经营许可",
        "生产、环保、消防和安全许可均有效且不存在重大行政处罚。",
        "排污许可有效至2029-05-31，辐射安全备案有效至2028-11-30，消防验收于2025-09-18完成，安全生产标准化证书有效至2028-12-31；登记机关逐项回函确认有效。2023-2026-06无环保、消防、税务、海关或市场监管重大处罚。",
        "许可证原件、主管机关回函和全国/地方处罚数据库检索（合成）",
    ),
    _spec(
        "LEG-004",
        "legal",
        "freedom_to_operate",
        228,
        "FTO检索与结论",
        "核心产品FTO检索覆盖四法域、全部关键权利要求和残余风险。",
        "外部合成知识产权律师检索中国、美国、日本、欧洲3,842件有效专利，将27项高相关权利要求映射至QH-5000设计；未发现阻断性权利要求。竞争对手A一件2028年到期专利经权利要求对照确认缺少本产品的双光路要素，不构成字面或等同侵权；书面FTO意见为可实施。",
        "专利检索式、权利要求图、设计图纸和外部FTO法律意见复核（合成）",
    ),
    _spec(
        "LEG-005",
        "legal",
        "ip_ownership",
        239,
        "知识产权权属",
        "核心专利、软件和员工职务成果权属链完整。",
        "公司拥有67件授权专利和24项软件著作权，QH-5000涉及的11件核心专利均由公司原始申请；67名研发员工及6名顾问全部签署职务成果转让、保密和开源合规承诺。代码扫描发现的9个开源组件均为MIT/BSD/Apache-2.0许可，无传染性许可冲突。",
        "专利登记簿、劳动/顾问协议、代码成分分析和许可清单（合成）",
    ),
    _spec(
        "LEG-006",
        "legal",
        "related_party_fairness",
        268,
        "关联交易公允性",
        "唯一持续关联租赁已履行回避批准并通过独立市场价格验证。",
        "公司向赵岚持有30%的合成启衡产业园租赁厂房，2025年租金6.0百万元；同园区三项独立可比年租金为6.2、6.4、6.6百万元。三名独立董事一致批准，赵岚回避表决；合同允许公司提前90天无罚金终止，2026续租价格上限不高于独立评估中位数。",
        "关联方清单、租赁合同、三项可比报价、评估报告和回避表决记录（合成）",
    ),
    _spec(
        "LEG-007",
        "legal",
        "material_contracts",
        281,
        "重大合同可执行性",
        "重大客户、供应商和贷款合同已审阅，交易不会触发终止或违约。",
        "覆盖2025年收入81%和采购76%的合同均由适格主体签署且在有效期内；融资不触发控制权变更条款。两份含转让限制的客户合同已取得书面同意。银行借款65百万元无财务维持契约，分期扩产及本轮股权融资均不构成违约。",
        "重大合同全量清单、授权签字样本、同意函和银行确认（合成）",
    ),
    _spec(
        "LEG-008",
        "legal",
        "litigation_labor_tax",
        292,
        "诉讼、劳动与税务",
        "诉讼、劳动、社保和税务敞口已完成全量检索并量化。",
        "唯一未结事项为一宗0.38百万元劳动争议，公司已全额计提并于2026-07-02签署和解，无其他诉讼、仲裁或执行案件。员工社保公积金覆盖率100%；2023-2025企业所得税、增值税申报与审计报表一致，税务机关回函无欠税或处罚。",
        "法院/仲裁检索、律师函、和解协议、员工全量清册和纳税回函（合成）",
    ),
    _spec(
        "LEG-009",
        "legal",
        "data_and_export_compliance",
        298,
        "数据与出口合规",
        "客户数据、跨境传输和产品出口分类均取得书面合规结论。",
        "设备仅处理缺陷图像和设备参数，不采集个人信息；18家客户合同均授权将去标识图像用于模型优化，数据保留不超过180天且境内存储。外部合成贸易律师将整机和核心相机分类为EAR99，将所用FPGA分类为3A991；当前中国交付无需许可证，筛查未命中受限制主体。",
        "数据流图、客户授权、删除日志、ECCN分类备忘录和制裁筛查（合成）",
    ),
    _spec(
        "LEG-010",
        "legal",
        "investment_terms_and_cp",
        404,
        "投资条款与交割条件",
        "投资人保护条款、交割先决条件和禁止性安排均已明确。",
        "合成条款清单约定每股15元、投资240百万元、1倍非参与清算优先、广义加权平均反稀释、董事会观察员和信息权；无公司或创始人保本回购、固定收益或对赌。交割条件仅包括许可持续有效、620百万元订单函证覆盖不低于95%、无重大不利变化及登记文件签署，现有材料均已满足，交割日再次确认。",
        "条款清单、公司章程修订稿、监管合规备忘录和交割清单（合成）",
    ),
    _spec(
        "RSK-001",
        "risk",
        "supplier_concentration",
        186,
        "供应商集中与替代",
        "供应商集中度、关键料号替代和切换周期均已验证。",
        "2025年前五大供应商占采购44%，最大单一供应商占12%。三类关键料号均有至少两家通过量产验证的供应商；进口线扫传感器现有库存覆盖5.5个月，国内替代件已在两家客户完成1,000小时测试，切换需12周且使单机成本增加1.8%。",
        "采购台账、供应商函证、库存盘点、替代件测试和切换计划（合成）",
    ),
    _spec(
        "RSK-002",
        "risk",
        "export_control_stress",
        197,
        "出口管制压力测试",
        "受出口规则影响的BOM暴露、分类、替代方案和财务影响均已量化。",
        "3A991 FPGA占2025年BOM成本8%，现行中国交付无需许可且供应商筛查通过。若供应中断，已验证的境内替代将于12周切换，使2026年最多延迟18台交付、收入递延79.2百万元而非取消，并使当年毛利率下降0.9个百分点；5.5个月安全库存覆盖切换窗口。",
        "贸易律师分类、供应链映射、替代测试及交付排程压力测试（合成）",
    ),
    _spec(
        "RSK-003",
        "risk",
        "subsidy_dependency",
        208,
        "政府补助依赖",
        "补助占比和取消补助后的盈利能力已量化。",
        "2025年政府补助12百万元，占税前利润143百万元的8.4%；全部补助为已收非经常性项目款，不附带未来业绩条件。若2026年起补助归零，按15%税率2025正常化净利润为111.8百万元，仍覆盖利息费用1百万元111.8倍。",
        "补助批文、银行回单、会计处理和无补助情景重算（合成）",
    ),
    _spec(
        "RSK-004",
        "risk",
        "capacity_expansion_tension",
        417,
        "扩产基准与下行压力",
        "扩产风险证据范围完整，是投决准入流程、风险评分和投资条款的核心依据；基准情景合理，但下行利用率要求分期触发。",
        "现有年产能180台，2025交付160台、利用率88.9%；一期新增90台后总产能270台，基准2027交付245台、利用率90.7%，下行仅168台、利用率62.2%。180百万元扩产分两笔各90百万元；第二笔仅在未来12个月不可撤销订单达到新增产能收入的1.2倍且首笔设备验收后释放。",
        "产线节拍审计、订单排程、下行情景和分期资本开支决议（合成）",
    ),
    _spec(
        "RSK-005",
        "risk",
        "customer_and_order_stress",
        431,
        "客户与订单压力测试",
        "最大客户流失和订单取消组合压力已量化至收入和利润。",
        "最大客户占2025年收入14%，前五大占48%。压力情景假设最大客户2026年收入归零且其余不可撤销订单取消率由2.1%升至8%，2026收入由基准980降至790百万元、净利由156降至86百万元；公司仍盈利，期末现金不低于205百万元。",
        "客户级预测、取消概率、毛利贡献和现金模型联动（合成）",
    ),
    _spec(
        "RSK-006",
        "risk",
        "liquidity_and_covenants",
        442,
        "流动性与债务",
        "融资后流动性、扩产支出和下行情景现金底线均已核验。",
        "2025年末现金138百万元、计息债务65百万元、净现金73百万元；本轮到账240后备考现金378。支付180百万元扩产、营运资金峰值48和债务偿还20后仍有130百万元静态缓冲；联动下行情景经营现金流后2026年末现金205百万元。借款无财务维持契约且无交叉违约。",
        "银行确认、债务合同、月度现金瀑布和下行情景模型（合成）",
    ),
    _spec(
        "RSK-007",
        "risk",
        "fx_and_geography",
        451,
        "汇率与地域风险",
        "境外收入、进口成本和人民币波动净敞口已量化。",
        "2025年境外收入占18%，外币采购占成本22%，美元收入与采购形成72%的自然对冲。人民币对美元升值10%的净影响为毛利减少8百万元、税后利润减少6.8百万元；公司已签不超过未来六个月已承诺净敞口70%的远期额度，不进行投机交易。",
        "币种级收入采购台账、银行远期额度和10%敏感性重算（合成）",
    ),
    _spec(
        "RSK-008",
        "risk",
        "quality_and_continuity",
        462,
        "质量与业务连续性",
        "质量损失、停产恢复和保险覆盖均已通过压力测试。",
        "2025年质保索赔成本占设备收入1.3%，无批量召回；假设核心型号20台现场整改，最大直接成本9百万元。双机房和每日离线备份演练的RTO为8小时、RPO为30分钟；财产险、产品责任险和营业中断险合计保额320百万元，高于经评估最大单次损失86百万元。",
        "质保工单、召回检索、灾备演练日志和保险经纪损失评估（合成）",
    ),
    _spec(
        "RSK-009",
        "risk",
        "key_person_and_governance",
        473,
        "关键人员与治理",
        "关键人员流失风险由锁定、继任和董事会监督共同缓释。",
        "CEO、CTO及12名核心工程师签署至2029年的留任安排，未归属股权占其总激励的55%；研发副总已完成CTO职责演练。投后董事会5席中创始人2席、机构股东2席、独立董事1席，投资人享观察员席位；关联交易和单笔超过30百万元资本开支须经非关联董事多数批准。",
        "留任协议、激励台账、继任演练和章程治理条款（合成）",
    ),
    _spec(
        "RSK-010",
        "risk",
        "integrated_downside_and_monitors",
        486,
        "综合下行与监控阈值",
        "综合风险直接约束投决决策、评分、条款和退出回报；下行仍保持偿付能力，但估值回报和扩产效率构成实质条件。",
        "综合下行情景同时采用2026收入790、净利86、毛利率下降0.9个百分点和扩产后2027利用率62.2%，无现金短缺或债务违约；但2029退出估值4,500百万元时投资MOIC仅1.05倍。因此投决条件为投前估值不高于3,600百万元、扩产第二笔满足RSK-004触发器，并按月监控订单覆盖、利用率、DSO和替代料认证。",
        "财务、供应链、订单和退出模型联动压力测试及董事会监控方案（合成）",
    ),
]


REQUIRED_TOPICS = {
    "business": {
        "product_revenue_identity",
        "bottom_up_tam",
        "bottom_up_sam_som",
        "market_growth_sensitivity",
        "competitor_share",
        "product_performance",
        "customers_and_retention",
        "orders_and_conversion",
        "unit_economics_and_delivery",
        "team_execution",
    },
    "finance": {
        "audit_scope_and_identity",
        "income_statement_identity",
        "balance_sheet_identity",
        "cash_flow_identity",
        "accounts_receivable",
        "revenue_quality_and_cutoff",
        "normalized_profitability",
        "forecast_driver_trace",
        "forecast_scenarios_and_dcf",
        "valuation_and_return_trace",
    },
    "legal": {
        "ownership_and_control",
        "transaction_approvals",
        "licenses_and_regulatory",
        "freedom_to_operate",
        "ip_ownership",
        "related_party_fairness",
        "material_contracts",
        "litigation_labor_tax",
        "data_and_export_compliance",
        "investment_terms_and_cp",
    },
    "risk": {
        "supplier_concentration",
        "export_control_stress",
        "subsidy_dependency",
        "capacity_expansion_tension",
        "customer_and_order_stress",
        "liquidity_and_covenants",
        "fx_and_geography",
        "quality_and_continuity",
        "key_person_and_governance",
        "integrated_downside_and_monitors",
    },
}


def _assert_close(actual: float, expected: float, *, tolerance: float = 0.05) -> None:
    if not math.isclose(actual, expected, abs_tol=tolerance):
        raise AssertionError(f"numeric invariant failed: {actual} != {expected}")


def validate_numeric_invariants() -> dict[str, Any]:
    assert 560 + 160 + 80 == 800
    assert 1620 * 4.5 == 7290
    _assert_close(710 * 4.35, 3088.5)
    _assert_close(720 / 3088.5 * 100, 23.3)
    _assert_close(sum((26.5, 23.3, 19.8, 12.4, 18.0)), 100.0)

    assert 480 - 288 == 192 and 192 - 128 + 8 - 2 - 10 == 60
    assert 620 - 366 == 254 and 254 - 155 + 10 - 2 - 15 == 92
    assert 800 - 472 == 328 and 328 - 196 + 12 - 1 - 21 == 122
    assert 560 == 250 + 310 and 680 == 292 + 388 and 820 == 340 + 480
    assert 75 - 70 + 19 == 90 - 66
    assert 105 - 92 + 15 == 118 - 90
    assert 134 - 126 + 12 == 138 - 118
    _assert_close(((142 + 176) / 2) / 800 * 365, 72.5)
    _assert_close(151 / 185 * 100, 81.6)
    _assert_close(122 - 12 * (1 - 0.15), 111.8)

    _assert_close(205 * 4.40 + 78, 980)
    _assert_close(245 * 4.45 + 109.75, 1200)
    _assert_close(286 * 4.50 + 163, 1450)
    fcff = [150, 200, 260, 330, 400]
    wacc = 0.115
    terminal_growth = 0.035
    pv_fcff = sum(value / (1 + wacc) ** year for year, value in enumerate(fcff, 1))
    terminal_value = fcff[-1] * (1 + terminal_growth) / (wacc - terminal_growth)
    equity_value = pv_fcff + terminal_value / (1 + wacc) ** 5 + 73
    _assert_close(round(equity_value), 4004, tolerance=1)
    _assert_close(3600 / 156, 23.1)
    assert 156 * 26 == 4056
    _assert_close(240 / (3600 + 240) * 100, 6.25)
    exit_value = 9000 * 0.0625 * 0.90
    _assert_close(exit_value, 506.25)
    _assert_close(exit_value / 240, 2.11)
    _assert_close((exit_value / 240) ** 0.25 - 1, 0.205, tolerance=0.001)

    _assert_close(160 / 180 * 100, 88.9)
    _assert_close(245 / 270 * 100, 90.7)
    _assert_close(168 / 270 * 100, 62.2)
    assert 138 - 65 == 73
    assert 138 + 240 == 378

    return {
        "tam_cny_m": 7290,
        "sam_cny_m": 3088.5,
        "som_pct": 23.3,
        "dcf_equity_value_cny_m": round(equity_value),
        "transaction_pre_money_cny_m": 3600,
        "post_money_ownership_pct": 6.25,
        "base_exit_moic": 2.11,
        "base_exit_irr_pct": 20.5,
        "downside_capacity_utilization_pct": 62.2,
    }


def validate_specs() -> None:
    codes = [item["code"] for item in EVIDENCE_SPECS]
    if len(codes) != len(set(codes)):
        raise AssertionError("evidence codes must be unique")
    dimensions = {dimension: [] for dimension in REQUIRED_TOPICS}
    for item in EVIDENCE_SPECS:
        dimensions[item["dimension"]].append(item)
        if not item["quote"] or not item["verification"]:
            raise AssertionError(f"empty trace for {item['code']}")
    for dimension, required_topics in REQUIRED_TOPICS.items():
        rows = dimensions[dimension]
        if len(rows) != 10:
            raise AssertionError(f"{dimension} must have exactly 10 default-retrievable items")
        topics = {item["topic"] for item in rows}
        if topics != required_topics:
            raise AssertionError(f"{dimension} topic coverage mismatch: {topics ^ required_topics}")
    validate_numeric_invariants()


def _evidence_id(code: str) -> str:
    return f"EVID-PMIC-POS-{code}"


def _evidence_items() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for position, spec in enumerate(EVIDENCE_SPECS, 1):
        block_id = f"pmic-pos-{spec['code'].lower()}"
        role_hints = list(ROLE_HINTS[spec["dimension"]])
        if spec["code"] in EXECUTIVE_TRACE_CODES:
            role_hints.extend((COORDINATOR, CHAIRMAN))
        items.append(
            {
                "schema_version": "siq_deal_evidence_item_v1",
                "evidence_id": _evidence_id(spec["code"]),
                "deal_id": DEAL_ID,
                "source_id": SOURCE_ID,
                "document_id": DOCUMENT_ID,
                "parse_run_id": PARSE_RUN_ID,
                "source_type": "primary_market_prospectus",
                "source_class": "project_evidence",
                "source_path": SOURCE_PATH,
                "artifact_path": ARCHIVE_PATH,
                "source_url": (
                    f"/api/primary-market/projects/{DEAL_ID}/documents/{DOCUMENT_ID}/"
                    f"runs/{PARSE_RUN_ID}/source?page={spec['page']}"
                ),
                "confidence": "high",
                "evidence_type": "verified",
                "dimension": spec["dimension"],
                "topic": spec["topic"],
                "materiality": spec["materiality"],
                "claim": spec["claim"],
                "quote": spec["quote"],
                "verification_method": spec["verification"],
                "citation": f"合成投前尽调数据册第{spec['page']}页，{spec['section']}",
                "locator": f"synthetic_diligence_book.pdf:p{spec['page']}:{spec['section']}",
                "source_anchor": {
                    "page": spec["page"],
                    "section": spec["section"],
                    "block_id": block_id,
                    "sequence": position,
                },
                "role_hints": role_hints,
                "synthetic_evaluation_only": True,
                "created_at": CREATED_AT,
            }
        )
    return items


def build_files() -> dict[str, str]:
    validate_specs()
    items = _evidence_items()
    blocks = [
        {
            "id": item["source_anchor"]["block_id"],
            "type": "text",
            "page": item["source_anchor"]["page"],
            "section": item["source_anchor"]["section"],
            "text": item["quote"],
            "synthetic_evaluation_only": True,
        }
        for item in items
    ]
    content_payload = {
        "schema_version": "siq_synthetic_primary_market_source_v1",
        "deal_id": DEAL_ID,
        "document_id": DOCUMENT_ID,
        "parse_run_id": PARSE_RUN_ID,
        "fixture_notice": NOTICE,
        "blocks": blocks,
    }
    content_text = _json_text(content_payload)
    archive_payload = {
        "schema_version": "siq_primary_market_parse_archive_v1",
        "deal_id": DEAL_ID,
        "document_id": DOCUMENT_ID,
        "parse_run_id": PARSE_RUN_ID,
        "build_mode": "deterministic_synthetic_evaluation_only_v1",
        "created_at": CREATED_AT,
        "bundle_sha256": _sha256(content_text),
        "artifacts": [
            {
                "path": "content_list_enhanced.json",
                "sha256": _sha256(content_text),
                "block_count": len(blocks),
            }
        ],
        "fixture_notice": NOTICE,
    }
    archive_text = _json_text(archive_payload)
    archive_hash = _sha256(archive_text)

    analysis_sources = {
        "schema_version": "siq_primary_market_analysis_sources_v1",
        "deal_id": DEAL_ID,
        "sources": [
            {
                "schema_version": "siq_primary_market_analysis_source_v1",
                "source_id": SOURCE_ID,
                "domain": "primary_market",
                "source_type": "primary_market_prospectus",
                "deal_id": DEAL_ID,
                "market": "CN",
                "company_id": f"PRIMARY:{DEAL_ID}",
                "filing_id": f"SYNTHETIC-DILIGENCE:{DOCUMENT_ID}",
                "document_id": DOCUMENT_ID,
                "parse_run_id": PARSE_RUN_ID,
                "artifact_manifest_path": ARCHIVE_PATH,
                "archive_manifest_sha256": archive_hash,
                "status": "ready",
                "capabilities": {
                    "text_evidence": "ready",
                    "source_page_trace": "ready",
                    "financial_facts": "ready",
                    "semantic_index": "ready",
                },
                "quality_status": "pass",
                "synthetic_evaluation_only": True,
                "activated_by": {
                    "id": "primary-market-ic-positive-smoke",
                    "username": "synthetic-evaluation-generator",
                },
                "activated_at": CREATED_AT,
                "created_at": CREATED_AT,
                "updated_at": CREATED_AT,
            }
        ],
    }

    dimension_counts = {
        dimension: sum(item["dimension"] == dimension for item in items)
        for dimension in REQUIRED_TOPICS
    }
    index_payload = {
        "schema_version": "siq_deal_evidence_index_v1",
        "deal_id": DEAL_ID,
        "build_mode": "deterministic_synthetic_evaluation_only_v1",
        "llm_used": False,
        "agent_used": False,
        "milvus_written": False,
        "built_at": CREATED_AT,
        "paths": {
            "items": "evidence/evidence_items.ndjson",
            "quality": "evidence/evidence_quality_report.json",
        },
        "counts": {
            "documents_total": 1,
            "documents_bound": 1,
            "documents_indexed": 1,
            "documents_skipped": 0,
            "items": len(items),
            "verified_items": len(items),
            "invalid_metadata_files": 0,
            "invalid_ndjson_lines": 0,
            "by_dimension": dimension_counts,
        },
        "documents": [
            {
                "document_id": DOCUMENT_ID,
                "parse_run_id": PARSE_RUN_ID,
                "status": "indexed",
                "item_count": len(items),
            }
        ],
        "items": [
            {
                "evidence_id": item["evidence_id"],
                "dimension": item["dimension"],
                "topic": item["topic"],
            }
            for item in items
        ],
        "fixture_notice": NOTICE,
    }
    index_text = _json_text(index_payload)
    index_hash = _sha256(index_text)
    snapshot_digest = "\n".join(
        [
            "siq_deal_evidence_item_v1",
            f"{SOURCE_ID}:{archive_hash}",
            f"evidence_index:{index_hash}",
        ]
    )
    snapshot_payload = {
        "schema_version": "siq_deal_evidence_snapshot_v1",
        "deal_id": DEAL_ID,
        "snapshot_hash": _sha256(snapshot_digest),
        "active_sources": [
            {
                "source_id": SOURCE_ID,
                "document_id": DOCUMENT_ID,
                "parse_run_id": PARSE_RUN_ID,
                "status": "ready",
                "capabilities": {
                    "text_evidence": "ready",
                    "source_page_trace": "ready",
                    "financial_facts": "ready",
                    "semantic_index": "ready",
                },
                "archive_manifest_sha256": archive_hash,
            }
        ],
        "source_ids": [SOURCE_ID],
        "evidence_index_sha256": index_hash,
        "evidence_contract_version": "siq_deal_evidence_item_v1",
        "synthetic_evaluation_only": True,
        "created_at": CREATED_AT,
    }

    coverage = {
        dimension: {
            "status": "complete",
            "item_count": dimension_counts[dimension],
            "topics": sorted(REQUIRED_TOPICS[dimension]),
        }
        for dimension in REQUIRED_TOPICS
    }
    quality_payload = {
        "schema_version": "siq_deal_evidence_quality_v1",
        "deal_id": DEAL_ID,
        "status": "pass",
        "build_mode": "deterministic_synthetic_evaluation_only_v1",
        "llm_used": False,
        "agent_used": False,
        "milvus_written": False,
        "built_at": CREATED_AT,
        "policy_version": "2026-07-13",
        "required_verified_items": 3,
        "required_dimensions": list(REQUIRED_TOPICS),
        "item_count": len(items),
        "verified_count": len(items),
        "dimensions": list(REQUIRED_TOPICS),
        "missing_dimensions": [],
        "critical_fact_status": "complete",
        "known_critical_fact_gaps": [],
        "coverage": coverage,
        "counts": index_payload["counts"],
        "gates": [
            {"id": "document_bindings", "status": "pass", "message": "1/1 source bound"},
            {"id": "source_artifacts", "status": "pass", "message": "40/40 quotes trace to source blocks"},
            {"id": "verified_items", "status": "pass", "message": "40/3 verified Evidence items"},
            {"id": "dimension_coverage", "status": "pass", "message": "10 verified items in each required dimension"},
            {"id": "financial_identities", "status": "pass", "message": "income, balance-sheet and cash-flow identities recomputed"},
            {"id": "numeric_invariants", "status": "pass", "message": "market, AR, forecast, valuation and stress traces recomputed"},
            {"id": "critical_fact_completeness", "status": "pass", "message": "no known missing critical fact"},
            {"id": "ndjson_valid", "status": "pass", "message": "evidence_items.ndjson is valid"},
        ],
        "documents": index_payload["documents"],
        "warnings": [],
        "errors": [],
        "limitations": [NOTICE, "This fixture validates workflow behavior and contract traceability, not real-world investment merit."],
    }

    numeric_trace = validate_numeric_invariants()
    fixture_contract = {
        "schema_version": "siq_primary_market_ic_synthetic_fixture_v1",
        "fixture_id": "PMIC-GOLDEN-POSITIVE-CONDITIONAL-001",
        "deal_id": DEAL_ID,
        "label": "evidence_complete_positive_conditional_support",
        "synthetic_evaluation_only": True,
        "real_entity_or_transaction": False,
        "intended_phases": ["R0", "R1", "R1.5", "R2", "R3", "R4"],
        "expected_semantics": {
            "r0": "ready",
            "r1": "six_role_reports_with_project_evidence",
            "r1_5": "material_tension_is_evidence_complete_and_adjudicable",
            "r2": "synthesis_can_advance",
            "r3": "focused_capacity_and_valuation_debate",
            "r4": "conditional_support_pending_trusted_human_confirmation",
        },
        "critical_fact_completeness": {
            "status": "complete",
            "missing_critical_facts": [],
            "open_questions": [],
            "coverage": coverage,
        },
        "material_expert_tensions": [
            {
                "tension_id": "TENSION-CAPACITY-VALUATION-001",
                "materiality": "high",
                "evidence_complete": True,
                "sector_position": "620百万元已确认订单、88.9%现有利用率和性能领先支持扩产。",
                "finance_risk_position": "下行情景扩产后利用率62.2%、退出MOIC仅1.05倍，不支持无条件一次性投入。",
                "adjudicable_resolution": "投前估值不高于3,600百万元，扩产分两笔，第二笔仅在订单覆盖触发器满足后释放。",
                "supporting_evidence_ids": [
                    _evidence_id("BUS-006"),
                    _evidence_id("BUS-008"),
                    _evidence_id("BUS-009"),
                    _evidence_id("FIN-008"),
                    _evidence_id("FIN-009"),
                    _evidence_id("FIN-010"),
                    _evidence_id("RSK-004"),
                    _evidence_id("RSK-010"),
                ],
            }
        ],
        "numeric_trace": numeric_trace,
        "default_retrieval_contract": {
            "limit_per_profile": 10,
            "items_per_dimension": dimension_counts,
            "expected_role_hit_counts": {
                COORDINATOR: 10,
                CHAIRMAN: 10,
                STRATEGIST: 10,
                SECTOR: 10,
                FINANCE: 10,
                LEGAL: 10,
                RISK: 10,
            },
            "executive_min_dimension_coverage": ["business", "finance", "legal", "risk"],
        },
        "deterministic_detector_probe": {
            "input_kind": "contract_valid_human_authored_reports_not_live_artifacts",
            "expected_dispute_dimension": "committee_alignment",
            "expected_dispute_severity": "high",
            "expected_evidence_sufficiency_disputes": 0,
            "closure_basis": "existing project Evidence plus a chairman ruling; no new critical fact required",
            "live_model_boundary": (
                "The fixture cannot guarantee a model ruling. A live model may still create an "
                "open question, red flag, unsupported claim, or needs_more_evidence outcome."
            ),
        },
        "fixture_notice": NOTICE,
    }

    materials_manifest = {
        "schema_version": "siq_primary_market_materials_manifest_v1",
        "deal_id": DEAL_ID,
        "status": "ready",
        "document_count": 1,
        "prospectus_count": 1,
        "materials": [
            {
                "document_id": DOCUMENT_ID,
                "parse_run_id": PARSE_RUN_ID,
                "document_type": "prospectus",
                "document_profile": "cn_a_share_prospectus",
                "market": "CN",
                "status": "ready",
                "quality_status": "pass",
                "capabilities": analysis_sources["sources"][0]["capabilities"],
                "synthetic_evaluation_only": True,
            }
        ],
        "completeness": {
            "identity": "complete",
            "business": "complete_for_r0_r4",
            "finance": "complete_for_r0_r4",
            "legal": "complete_for_r0_r4",
            "risk": "complete_for_r0_r4",
        },
        "blocking_reasons": [],
        "fixture_notice": NOTICE,
    }
    metadata = {
        "schema_version": "siq_deal_document_v2",
        "deal_id": DEAL_ID,
        "document_id": DOCUMENT_ID,
        "document_type": "prospectus",
        "document_profile": "cn_a_share_prospectus",
        "parser_kind": "synthetic_fixture",
        "original_filename": "synthetic_evaluation_only_pmic_diligence_book.pdf",
        "sha256": _sha256(content_text),
        "current_parse_run_id": PARSE_RUN_ID,
        "analysis_source_status": "ready",
        "status": "ready",
        "synthetic_evaluation_only": True,
        "fixture_notice": NOTICE,
    }
    project_meta = {
        "schema_version": "siq_deal_project_v1",
        "deal_id": DEAL_ID,
        "legacy_project_id": None,
        "company_name": COMPANY_NAME,
        "industry": INDUSTRY,
        "stage": "Pre-IPO",
        "deal_type": "合成股权投资评测项目",
        "source": "synthetic_evaluation_only",
        "status": "draft",
        "created_by": {
            "id": "primary-market-ic-positive-smoke",
            "username": "synthetic-evaluation-generator",
        },
        "created_at": CREATED_AT,
        "updated_at": CREATED_AT,
        "final_decision": None,
        "final_score": None,
        "confidentiality_level": "private",
        "synthetic_evaluation_only": True,
        "fixture_notice": NOTICE,
    }
    workflow = {
        "schema_version": "siq_deal_workflow_state_v1",
        "deal_id": DEAL_ID,
        "legacy_project_id": None,
        "company_name": COMPANY_NAME,
        "industry": INDUSTRY,
        "stage": "Pre-IPO",
        "status": "draft",
        "current_phase": "R0",
        "policy_version": "2026-07-13",
        "phases": {
            phase: {"status": "pending"}
            for phase in ("R0", "R1", "R1.5", "R2", "R3", "R4")
        },
        "synthetic_evaluation_only": True,
        "created_at": CREATED_AT,
        "updated_at": CREATED_AT,
    }

    items_text = "".join(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n" for item in items)
    manifest = {
        "schema_version": "siq_deal_manifest_v1",
        "deal_id": DEAL_ID,
        "legacy_project_id": None,
        "company_name": COMPANY_NAME,
        "created_at": CREATED_AT,
        "updated_at": CREATED_AT,
        "synthetic_evaluation_only": True,
        "fixture_label": "evidence_complete_positive_conditional_support",
        "documents": [
            {
                "document_id": DOCUMENT_ID,
                "parse_run_id": PARSE_RUN_ID,
                "document_type": "prospectus",
                "document_profile": "cn_a_share_prospectus",
                "market": "CN",
                "status": "ready",
                "source_id": SOURCE_ID,
                "synthetic_evaluation_only": True,
            }
        ],
        "evidence": {
            "index_path": "evidence/evidence_index.json",
            "items_path": "evidence/evidence_items.ndjson",
            "quality_path": "evidence/evidence_quality_report.json",
            "snapshot_path": "evidence/evidence_snapshot.json",
        },
        "workflow": {
            "state_path": "phases/workflow_state.json",
            "policy_version": "2026-07-13",
        },
        "decision": {
            "markdown_path": "decision/IC_DECISION_REPORT.md",
            "html_path": "decision/IC_DECISION_REPORT.html",
        },
        "hashes": {
            "evidence_items_sha256": _sha256(items_text),
            "evidence_index_sha256": index_hash,
            "source_content_sha256": _sha256(content_text),
            "archive_manifest_sha256": archive_hash,
        },
        "fixture_notice": NOTICE,
    }

    readme = f"""# {DEAL_ID}\n\nSYNTHETIC EVALUATION ONLY. This package contains no real company, person, customer,\nsupplier, legal opinion, market study, financial statement, or investment transaction.\nIt must only be used for isolated primary-market IC workflow evaluation.\n\nThe fixture contains 40 verified project Evidence items: 10 each for business, finance,\nlegal, and risk. It is intended to support a truthful R0-R4 positive/conditional-support\nsmoke while preserving one evidence-complete material capacity/valuation tension.\nThere are no intentionally missing critical facts. Final R4 approval still requires the\nworkflow's trusted human confirmation and must never be inferred from this fixture.\n\nRegenerate with:\n\n```bash\npython eval_datasets/primary_market_ic_real_smoke/generate_evidence_complete_fixture.py\n```\n\nVerify byte-for-byte determinism with:\n\n```bash\npython eval_datasets/primary_market_ic_real_smoke/generate_evidence_complete_fixture.py --check\n```\n"""

    return {
        "README.md": readme,
        "manifest.json": _json_text(manifest),
        "project_meta.json": _json_text(project_meta),
        "fixture_contract.json": _json_text(fixture_contract),
        "data_room/materials_manifest.json": _json_text(materials_manifest),
        f"data_room/metadata/{DOCUMENT_ID}.json": _json_text(metadata),
        "sources/analysis_sources.json": _json_text(analysis_sources),
        "evidence/evidence_items.ndjson": items_text,
        "evidence/evidence_index.json": index_text,
        "evidence/evidence_quality_report.json": _json_text(quality_payload),
        "evidence/evidence_snapshot.json": _json_text(snapshot_payload),
        "phases/workflow_state.json": _json_text(workflow),
        SOURCE_PATH: content_text,
        ARCHIVE_PATH: archive_text,
    }


def _validate_rendered_files(files: dict[str, str]) -> None:
    parsed_items = [
        json.loads(line)
        for line in files["evidence/evidence_items.ndjson"].splitlines()
        if line.strip()
    ]
    source = json.loads(files[SOURCE_PATH])
    blocks = {item["id"]: item for item in source["blocks"]}
    if len(parsed_items) != 40 or len(blocks) != 40:
        raise AssertionError("fixture must contain exactly 40 Evidence/source blocks")
    for item in parsed_items:
        if item["deal_id"] != DEAL_ID or item["source_id"] != SOURCE_ID:
            raise AssertionError(f"identity mismatch for {item['evidence_id']}")
        block = blocks.get(item["source_anchor"]["block_id"])
        if not block or block["text"] != item["quote"]:
            raise AssertionError(f"source trace mismatch for {item['evidence_id']}")
    index_hash = _sha256(files["evidence/evidence_index.json"])
    snapshot = json.loads(files["evidence/evidence_snapshot.json"])
    if snapshot["evidence_index_sha256"] != index_hash:
        raise AssertionError("snapshot evidence index hash mismatch")
    archive_hash = _sha256(files[ARCHIVE_PATH])
    active = snapshot["active_sources"][0]
    if active["archive_manifest_sha256"] != archive_hash:
        raise AssertionError("snapshot archive hash mismatch")
    digest = "\n".join(
        [
            "siq_deal_evidence_item_v1",
            f"{SOURCE_ID}:{archive_hash}",
            f"evidence_index:{index_hash}",
        ]
    )
    if snapshot["snapshot_hash"] != _sha256(digest):
        raise AssertionError("snapshot hash is not reproducible")


def write_fixture(files: dict[str, str]) -> None:
    if TARGET.parent != ROOT or TARGET.name != DEAL_ID:
        raise AssertionError("fixture output escaped scoped evaluation root")
    for relative, text in files.items():
        destination = TARGET / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8")


def check_fixture(files: dict[str, str]) -> None:
    differences: list[str] = []
    for relative, expected in files.items():
        path = TARGET / relative
        try:
            actual = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            differences.append(f"missing:{relative}")
            continue
        if actual != expected:
            differences.append(f"changed:{relative}")
    if differences:
        raise SystemExit("fixture check failed: " + ", ".join(differences))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify committed files without writing")
    args = parser.parse_args()
    files = build_files()
    _validate_rendered_files(files)
    if args.check:
        check_fixture(files)
        action = "checked"
    else:
        write_fixture(files)
        action = "generated"
    print(
        json.dumps(
            {
                "action": action,
                "deal_id": DEAL_ID,
                "path": str(TARGET),
                "files": len(files),
                "evidence_items": len(EVIDENCE_SPECS),
                "dimensions": {dimension: 10 for dimension in REQUIRED_TOPICS},
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
