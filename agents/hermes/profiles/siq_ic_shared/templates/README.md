# SIQ IC Templates

## 目录定位

`templates/` 保存投委会体系可复用的模板资产，用来承接 `siq_ic_shared` 中的 report / evidence / workflow contract，而不是承载具体项目的运行结果。

## 当前模板范围

当前或近期应落在这里的模板类型包括：

- 最终投委会决策报告模板。
- 专家角色报告模板。
- evidence package 摘要模板。
- 分歧记录与主席裁决日志模板。

## 当前产品位置

这些模板服务一级市场 Deal OS，而不是二级市场 HK MVP。它们的目标是让战略、行业、财务、法务、风控和主席报告在不同项目间保持同一结构，便于 API 汇总、前端展示、争议追踪和最终投委会回放。

## 模板使用规则

- 模板必须与 `ic_report_contract.md`、`ic_evidence_contract.md` 和 `ic_workflow_policy.json` 对齐。
- 模板应描述结构和字段，不应预置某个具体 deal 的事实内容。
- 模板既要适合人类阅读，也要方便后续自动化装配或校验。

## 后续扩展边界

- 新增模板前，先确认它属于“稳定复用资产”，而不是某次执行临时文稿。
- 若模板只服务某个单独角色且不具备通用性，应优先留在角色目录中说明，而不是放入 shared templates。
- 模板扩展要和 shared contract 同步演进，避免出现结构不兼容的文档骨架。
