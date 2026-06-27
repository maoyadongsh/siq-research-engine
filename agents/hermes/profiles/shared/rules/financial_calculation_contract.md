# SIQ Financial Calculation Contract v1

本契约适用于 `siq_assistant`、`siq_analysis`、`siq_factchecker`、`siq_tracking`、`siq_legal` 五个 profile。只要回答或产物中出现衍生财务计算，就必须执行本契约；模型只能解释计算器结果，不能心算后直接输出。

## 强制使用计算器

涉及以下任一场景时，必须调用共享计算器：

```bash
python3 /home/maoyd/siq-research-engine/data/hermes/home/profiles/shared/scripts/financial_calculator.py ...
```

- 金额单位换算：元、千元、万元、百万元、亿元、million、billion 等。
- 外币金额换算：美元、欧元、港元、日元等；换算人民币必须提供汇率、日期和来源，无法提供时只保留原币结果并提示缺口。
- 人均、每股、每户等分子/分母类指标。
- 同比、环比、增长率、占比、毛利率、资产负债率、现金短债覆盖等比例指标。
- CAGR、复合增长率；跨正负号或基数为 0 时必须输出 N/A，不得强算。
- 对报告中已有派生数字做事实核查。

## 勾稽校验

涉及主表净额与附注原值/备抵/减值准备的关系时，必须调用共享勾稽校验器或后端同源函数：

```bash
python3 /home/maoyd/siq-research-engine/data/hermes/home/profiles/shared/scripts/financial_reconciliation_validator.py goodwill \
  --company <公司或代码> --format markdown
```

- 商誉必须按 `附注商誉账面原值 - 商誉减值准备 = 三大表商誉账面净额/账面价值` 校验。
- 三大表 `goodwill` / `商誉` 是扣除减值准备后的账面净额；不得把附注 `(1).商誉账面原值` 当成主表商誉余额。
- 回答商誉构成、减值准备或商誉风险时，应分别标明 `原值`、`减值准备`、`净额` 三种口径，并保留勾稽公式、差异和来源。
- 坏账准备、存货跌价准备、固定资产/无形资产/长期股权投资减值准备等“原值/准备/净额”关系，按同一原则处理；没有专用命令时，至少要用同源数据明确列出公式和来源，不能人工猜口径。

## 通用单位规则

1. 金额先转为原币最小单位，再派生展示值。
2. A 股默认币种为 CNY；海外公司或外币披露必须保留原币口径。
3. 金额类指标的报告展示默认归一为“亿元”，保留 2 位小数；外币金额展示为“亿欧元/亿美元/亿港元”等，同时可在有汇率时给出人民币“亿元”。
4. 人均、每股、每户等指标不是金额总量，不得归一为“亿元/人”；应展示为“元/人、万元/人、欧元/人、万欧元/人”等。
5. 分母是员工数、股数、户数、销量等数量单位时，必须按数量单位处理，不能按金额单位处理。
6. `10.16 亿欧元 = 1,016,000,000 欧元`；`1016 百万欧元 = 1,016,000,000 欧元`；二者相等，均不是 `10,160,000,000 欧元`。
7. `亿元 = 100,000,000 元`，`万元 = 10,000 元`，`百万元 = 1,000,000 元`。
8. 比率类指标统一展示为百分比，保留 2 位小数，并保留分子、分母和单位。
9. 同比/增长率若上期值为 0 或负数，默认输出 `not_applicable` 并说明“由亏转盈/亏损扩大/亏损收窄”等方向；不得把负基数下的百分比当作普通增长率。确需展示特殊口径时，必须显式说明采用绝对值基数。
10. 年报表格中 `(1,016)`、`（1,016）` 这类括号金额表示负数，即使后面跟随“百万元/亿欧元”等单位，也必须按负数解析。
11. `HKD`、`HK$` 等币种符号不是 `K=千` 的数量单位；币种识别和金额单位倍率必须分离。

## 推荐命令

外币金额归一：

```bash
python3 /home/maoyd/siq-research-engine/data/hermes/home/profiles/shared/scripts/financial_calculator.py --format markdown normalize \
  --value -10.16 --unit 亿欧元 --currency EUR \
  --fx-to-cny 7.8 --fx-date 2026-06-21 --fx-source 用户提供
```

人均指标：

```bash
python3 /home/maoyd/siq-research-engine/data/hermes/home/profiles/shared/scripts/financial_calculator.py --format markdown per-capita \
  --amount -10.16 --amount-unit 亿欧元 --currency EUR \
  --count 110820 --count-unit 人 \
  --fx-to-cny 7.8 --fx-date 2026-06-21 --fx-source 用户提供
```

报告值复核：

```bash
python3 /home/maoyd/siq-research-engine/data/hermes/home/profiles/shared/scripts/financial_calculator.py --format markdown per-capita \
  --amount -10.16 --amount-unit 亿欧元 --currency EUR \
  --count 110820 --count-unit 人 \
  --fx-to-cny 7.8 --fx-date 2026-06-21 --fx-source 用户提供 \
  --reported-cny-10k -71.5
```

同比：

```bash
python3 /home/maoyd/siq-research-engine/data/hermes/home/profiles/shared/scripts/financial_calculator.py --format markdown yoy \
  --current 120 --current-unit 亿元 \
  --previous 100 --previous-unit 亿元 --currency CNY
```

占比/率：

```bash
python3 /home/maoyd/siq-research-engine/data/hermes/home/profiles/shared/scripts/financial_calculator.py --format markdown ratio \
  --numerator 30 --numerator-unit 亿元 \
  --denominator 100 --denominator-unit 亿元 --currency CNY
```

## 输出要求

- 正文出现派生计算时，必须能追溯到一次 `## 计算器校验` 或等价 JSON 输出。
- 引用来源仍按 SIQ Citation Contract 执行：分子、分母各自要有来源；计算器只负责算术和单位，不替代证据。
- 若计算器输出 `fx_required`、`division_by_zero`、`not_applicable`、`error`，不得把结果写成确定数字；必须说明原因和缺口。
- 若模型自己的草稿值与计算器 `checks` 不一致，以计算器为准，并把原值标注为计算错误或笔误。
- CLI 中 `fx_required`、`division_by_zero`、`not_applicable` 属于受控业务状态，不代表工具失败；只要输出了结构化 JSON/Markdown，就应继续解释该状态。只有 `status=error` 才视为脚本执行失败。
