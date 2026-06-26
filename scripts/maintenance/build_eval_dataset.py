#!/usr/bin/env python3
"""构建 SIQ/SIQ 兼容的第一版 KupasEval 评测语料。

输出：
- eval_datasets/siq_financial_analysis_eval_v1.jsonl
- eval_datasets/siq_financial_analysis_eval_v1.csv
- eval_datasets/siq_financial_analysis_eval_v1_metadata.json
- eval_datasets/README.md
"""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path


ROOT = Path(
    os.getenv("SIQ_PROJECT_ROOT")
    or os.getenv("SIQ_PROJECT_ROOT")
    or Path(__file__).resolve().parents[2]
).expanduser().resolve()
OUT_DIR = Path(os.getenv("SIQ_EVAL_DATASETS_DIR", ROOT / "eval_datasets")).expanduser().resolve()

COMPANIES = [
    ("000333", "美的集团", "家用电器", "白色家电"),
    ("000625", "长安汽车", "汽车", "乘用车"),
    ("002594", "比亚迪", "汽车", "新能源汽车"),
    ("600104", "上汽集团", "汽车", "乘用车"),
    ("600399", "抚顺特钢", "钢铁", "特钢材料"),
    ("600418", "江淮汽车", "汽车", "商用车与乘用车"),
    ("600733", "北汽蓝谷", "汽车", "新能源汽车"),
    ("601127", "赛力斯", "汽车", "新能源汽车"),
    ("601238", "广汽集团", "汽车", "乘用车"),
    ("601633", "长城汽车", "汽车", "乘用车"),
]

TASKS = [
    {
        "task_type": "key_metric_extraction",
        "dimension": "财务指标抽取准确性",
        "input": "请基于{company}（{code}）2025年年度报告，提取营业收入、归母净利润、经营活动现金流量净额、总资产、归母净资产、资产负债率等核心指标，并说明每个指标的来源。",
        "expected": "应覆盖营业收入、归母净利润、经营活动现金流量净额、总资产、归母净资产、资产负债率等核心指标；应说明指标来自利润表、资产负债表、现金流量表或结构化指标文件；不得编造无法从年报或本地Wiki追溯的数值。",
    },
    {
        "task_type": "revenue_profit_cashflow",
        "dimension": "盈利与现金流匹配度",
        "input": "请分析{company}（{code}）2025年营业收入、归母净利润与经营现金流之间是否匹配，判断是否存在利润增长但现金流承压的情况。",
        "expected": "应同时讨论营业收入、归母净利润、经营活动现金流量净额三类指标；应判断利润与现金流是否方向一致；如存在背离，应解释可能来自应收、存货、预收、费用或减值变化；必须给出证据来源或说明证据缺口。",
    },
    {
        "task_type": "balance_sheet_debt",
        "dimension": "偿债能力分析",
        "input": "请评估{company}（{code}）2025年资产负债结构和偿债压力，重点关注资产负债率、短期借款、一年内到期负债、货币资金和经营现金流。",
        "expected": "应覆盖资产负债率、短期有息负债、货币资金和经营现金流；应区分短期流动性压力与长期资本结构压力；不得直接给出投资建议；应说明结论依据。",
    },
    {
        "task_type": "cashflow_quality",
        "dimension": "现金流质量分析",
        "input": "请分析{company}（{code}）2025年现金流质量，重点比较经营活动、投资活动和筹资活动现金流，并指出主要风险信号。",
        "expected": "应覆盖经营活动、投资活动、筹资活动三类现金流；应解释经营现金流是否支撑利润质量；应识别资本开支、融资依赖、现金流波动等风险信号；应给出来源。",
    },
    {
        "task_type": "three_statement_consistency",
        "dimension": "三大表勾稽与一致性",
        "input": "请对{company}（{code}）2025年利润表、资产负债表和现金流量表做一致性检查，指出至少3个需要核对的勾稽关系。",
        "expected": "应至少提出3个勾稽关系，例如净利润与经营现金流、货币资金变动与现金流量表、资产减值与利润变动、应收或存货变化与收入现金流；应说明每个勾稽点如何验证；不得凭空断言异常。",
    },
    {
        "task_type": "asset_quality_risk",
        "dimension": "资产质量与风险识别",
        "input": "请识别{company}（{code}）2025年年报中的资产质量风险，重点关注应收账款、存货、商誉、固定资产、减值准备等项目。",
        "expected": "应覆盖应收账款、存货、商誉、固定资产或减值准备中的关键项目；应结合行业特征判断风险，而不是只罗列指标；应区分已确认风险和需要进一步核实的风险；应标注证据来源。",
    },
    {
        "task_type": "profitability_drivers",
        "dimension": "盈利质量与驱动因素",
        "input": "请分析{company}（{code}）2025年盈利能力变化的主要驱动因素，重点关注毛利率、期间费用率、研发投入、减值损失和非经常性损益。",
        "expected": "应分析毛利率、期间费用率、研发投入、减值损失、非经常性损益等因素；应判断利润变化来自主营改善、成本控制、费用变化、减值或一次性收益；应避免只给结论不列依据。",
    },
    {
        "task_type": "industry_context",
        "dimension": "行业适配分析",
        "input": "请结合{company}（{code}）所属的{industry}/{sub_industry}行业特征，解释2025年财务表现中最值得关注的3个经营问题。",
        "expected": "应结合公司所属行业特征解释财务表现；至少输出3个经营问题；每个问题应包含对应财务指标或年报证据；不能使用与行业明显不匹配的分析框架。",
    },
    {
        "task_type": "evidence_grounded_answer",
        "dimension": "证据忠实度与可追溯性",
        "input": "请回答：{company}（{code}）2025年最核心的财务风险是什么？要求每个结论都附带年报或结构化数据来源；如果本地资料不足，请明确说明不足。",
        "expected": "应给出1到3个核心财务风险；每个风险必须有年报、表格、指标文件或本地Wiki证据；如证据不足，应明确说证据不足；不得编造页码、文件名或不存在的指标。",
    },
    {
        "task_type": "tracking_items",
        "dimension": "后续跟踪事项生成",
        "input": "请基于{company}（{code}）2025年年报分析结论，生成未来6个月需要持续跟踪的事项清单，要求包含监控指标、触发阈值、验证方法和更新频率。",
        "expected": "应生成可执行的跟踪事项；每个事项应包含监控指标、触发阈值、验证方法和更新频率；应优先选择财务风险、经营承诺、现金流、资产质量、监管或公告事项；不得输出买卖建议。",
    },
]


def build_records() -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for company_index, (code, company, industry, sub_industry) in enumerate(COMPANIES, 1):
        for task_index, task in enumerate(TASKS, 1):
            case_id = f"siq-v1-{company_index:02d}-{task_index:02d}"
            prompt = (
                "你是 SIQ 上市公司财报智能分析智能体。"
                "请基于本地已入库的2025年年度报告、结构化指标和证据链作答。"
                "回答必须区分事实、计算、推断和证据缺口；不得编造数据、页码或来源；"
                "不得给出股票买卖建议。"
            )
            input_text = task["input"].format(
                code=code,
                company=company,
                industry=industry,
                sub_industry=sub_industry,
            )
            expected = task["expected"]
            records.append(
                {
                    "case_id": case_id,
                    "subset": "全部",
                    "industry": "金融",
                    "scenario": "上市公司财报识别及分析",
                    "company_code": code,
                    "company_name": company,
                    "company_industry": industry,
                    "company_sub_industry": sub_industry,
                    "report_year": "2025",
                    "report_type": "annual_report",
                    "task_type": task["task_type"],
                    "eval_dimension": task["dimension"],
                    "prompt": prompt,
                    "input": input_text,
                    "expected": expected,
                    "scoring_notes": (
                        "重点评估回答是否基于本地年报证据、是否覆盖题目要求指标、"
                        "是否清楚说明证据来源或缺口、是否避免幻觉和投资建议。"
                    ),
                }
            )
    return records


def write_jsonl(records: list[dict[str, str]], path: Path) -> None:
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def write_csv(records: list[dict[str, str]], path: Path) -> None:
    fieldnames = [
        "case_id",
        "subset",
        "industry",
        "scenario",
        "company_code",
        "company_name",
        "company_industry",
        "company_sub_industry",
        "report_year",
        "report_type",
        "task_type",
        "eval_dimension",
        "prompt",
        "input",
        "expected",
        "scoring_notes",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)


def write_metadata(records: list[dict[str, str]], path: Path) -> None:
    metadata = {
        "name": "SIQ上市公司财报智能分析评测语料集",
        "version": "1.0",
        "created_at": "2026-05-27",
        "type": "评测语料",
        "industry": "金融",
        "scenario": "上市公司财报识别及分析",
        "record_count": len(records),
        "company_count": len(COMPANIES),
        "companies": [
            {
                "code": code,
                "name": company,
                "industry": industry,
                "sub_industry": sub_industry,
            }
            for code, company, industry, sub_industry in COMPANIES
        ],
        "eval_dimensions": sorted({record["eval_dimension"] for record in records}),
        "recommended_agent": "SIQ 上市公司财报智能分析智能体",
        "recommended_api": {
            "method": "POST",
            "content_type": "application/json",
            "request_body_template": {"message": "$(input)"},
            "response_output_field": "reply",
            "receive_mode": "Single",
        },
        "description": (
            "面向上市公司年报识别与财务分析场景构建的智能体评测语料集，"
            "覆盖财务指标抽取、三大表勾稽、盈利质量、现金流质量、资产质量、"
            "偿债能力、风险识别、证据链溯源与事实核查等任务。"
        ),
    }
    path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_readme(path: Path) -> None:
    text = """# SIQ 评测语料集 v1.0

本目录包含第一版 KupasEval 评测语料，共 100 条：

- 10 家已入库上市公司
- 每家公司 10 条评测样本
- 覆盖财务指标抽取、利润现金流匹配、偿债能力、现金流质量、三大表勾稽、资产质量、盈利驱动、行业适配、证据忠实度、后续跟踪事项

## 文件说明

- `siq_financial_analysis_eval_v1.csv`：适合人工查看和平台表格导入。
- `siq_financial_analysis_eval_v1.jsonl`：适合程序处理和二次转换。
- `siq_financial_analysis_eval_v1_metadata.json`：语料集元信息和推荐智能体接入配置。

## KupasEval 建议

语料集名称：`SIQ上市公司财报智能分析评测语料集`

语料集类型：`评测语料`

行业类型：`金融`

智能体名称：`SIQ 上市公司财报智能分析智能体`

Request Body Template：

```json
{
  "message": "$(input)"
}
```

Response outputField：

```text
reply
```

建议先使用非流式 / Single 模式完成联通测试，再启动正式评测。
"""
    path.write_text(text, encoding="utf-8")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    records = build_records()
    write_jsonl(records, OUT_DIR / "siq_financial_analysis_eval_v1.jsonl")
    write_csv(records, OUT_DIR / "siq_financial_analysis_eval_v1.csv")
    write_metadata(records, OUT_DIR / "siq_financial_analysis_eval_v1_metadata.json")
    write_readme(OUT_DIR / "README.md")
    print(json.dumps({"ok": True, "records": len(records), "out_dir": str(OUT_DIR)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
