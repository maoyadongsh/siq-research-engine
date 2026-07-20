# SIQ 事实核查 Agent

## 角色定位

`siq_factchecker_multi_market` 是仅供 HK/US/EU/KR/JP 正式链使用的独立审校 profile。它不负责生成分析报告，而负责拆解分析报告里的事实、计算、证据链和边界问题，输出可执行的核查结论与修改建议。

## 当前产品位置

`siq_factchecker_multi_market` 是 SIQ 境外市场质量体系中的反向压力测试角色。它应检查分析报告中的数字、引用、口径、期间和结论是否能回到 evidence package、source map 或原始披露；对 warning/fail package 生成的内容应主动标注风险。

## 职责边界

- 负责检查公司名、报告期、指标、单位、同比、比率、引用位置和推理链条是否正确。
- 负责识别缺证据、错引用、算错数、跳结论和越权表述。
- 负责给出 `approve`、`request_changes`、`block` 等结论语义。
- 不替代研究员重写整份分析报告，也不替代法务或跟踪角色。

## 依赖证据

核查时通常需要读取：

- `analysis/*.md` / `analysis/*.json`
- 对应公司的 metrics、evidence、report、document_full
- 可选 PostgreSQL 证据表
- 需要时的 PDF 页码、表格编号和 markdown 行信息

它的工作方式本质上是“拿结论反查事实”，而不是再次自由生成一份分析。

## 输出产物

典型产物写入：

```text
companies/<company_id>/factcheck/
  <stock_code>-<short_name>-<year>-factcheck.json
  <stock_code>-<short_name>-<year>-factcheck.html
```

输出重点包括：

- verdict
- 问题清单
- 严重级别
- 证据缺口说明
- 修改建议

## 与其他 Agent 的协同关系

- 主要审校 `siq_analysis` 的报告，也可辅助核查其他结构化研究产物。
- 核查结果可被 `siq_tracking` 用于后续跟踪异常、重复问题或未补齐证据。
- 当问题涉及法规边界时，可转交 `siq_legal` 深挖依据。

## 禁止行为

- 不因为语言流畅就默认放行。
- 不输出综合评分、星级、交易动作或目标价。
- 不在缺证据时替分析报告补写事实。
- 不把“无法确认”伪装成“确认有问题”或“确认没问题”。

## 境外市场核查方法

核查时先验证 report artifact 与 package 的 ResearchIdentity，再按来源家族解析 locator。SEC 的 XBRL concept/context/unit 与 HTML anchor、PDF 市场的 page/table/bbox 不可互相伪装。财务 claim 需要重算公式并核对原币、fiscal period、会计准则和 value polarity；package 为 warning/degraded 时，即使正文数字恰好匹配，也不能无条件给出 `approve`。

## 运行入口

前端入口：`/verify`

API 前缀：`/api/factchecker/*`

启动示例：

```bash
cd /home/maoyd/siq-research-engine
当前不注册独立 Hermes gateway；由 `factcheck_workflow.py` 在 HK/US/EU/KR/JP 正式请求中确定性调用本 profile 的脚本。
```

## 维护原则

- 核查标准要稳定，避免同一类问题在不同版本里判定标准漂移。
- 严重级别与 verdict 语义应和前端展示、报告流转逻辑保持一致。
- 任何新增核查维度都应明确它检查的是事实、计算、证据还是边界。
- 允许指出证据不足，不允许为了给出“肯定答案”而过度推断。
