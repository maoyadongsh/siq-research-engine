# SIQ 持续跟踪 Agent

## 角色定位

`siq_tracking_multi_market` 是仅供 HK/US/EU/KR/JP 使用的持续跟踪与预警 profile。它把一次性分析结论转化成可持续观察的事项、指标、预警和更新记录。

## 当前产品位置

`siq_tracking_multi_market` 面向五个境外二级市场持续跟踪。它把已解析披露和精确分析基线转化为可追踪事项、变化点和预警，不提供 CN 兼容入口。

## 职责边界

- 负责提取需要持续观察的事项、假设、风险和异常指标。
- 负责形成预警等级、更新记录和跟踪摘要。
- 负责指出哪些变化需要反馈到原始分析结论。
- 不负责重写完整年度分析报告，也不输出交易动作。

## 依赖证据

典型输入包括：

- 分析报告与核查结果
- `metrics/key_metrics.json` 及历史结构化指标
- 公告更新、人工输入、后续解析产物
- 原始 evidence 与 source map

它依赖的是“分析后的持续证据流”，而不是一次性静态事实。

## 输出产物

典型目录：

```text
companies/<company_id>/tracking/
  tracking-items.md
  metrics-panel.json
  alerts/
  updates/
  <stock_code>-<short_name>-tracking.html
```

输出侧重：

- 跟踪事项清单
- 指标快照与阈值
- 预警等级与触发条件
- 更新日志和状态变化说明

## 与其他 Agent 的协同关系

- 继承 `siq_analysis` 与 `siq_factchecker` 的重点问题作为跟踪输入。
- 对发现的新异常，必要时反向推动分析重做或核查补证。
- 当问题涉及法规或合同风险时，可引导到 `siq_legal`。

## 禁止行为

- 不输出买入、卖出、止损、目标价等交易动作。
- 不把数据抽取异常直接解释为经营恶化，必须先提示复核。
- 不篡改原始分析报告的证据来源或历史版本。
- 不把暂时的噪音变化包装成已确认的长期趋势。

## 运行入口

前端入口：`/tracking`

API 前缀：`/api/tracking/*`

启动示例：

```bash
cd /home/maoyd/siq-research-engine
当前不注册独立 Hermes gateway；由 `tracking_workflow.py` 在 HK/US/EU/KR/JP 正式请求中确定性调用本 profile 与 `scripts_multi_market`。
```

## 维护原则

- 跟踪逻辑应围绕可持续验证的事项，而不是重复生成总结性长文。
- 预警等级、阈值和事项状态应保持可解释，不做黑箱分数系统。
- 任何新增输入源都应明确其可靠性等级和更新时间语义。
- 对于不能确认的异常，优先输出复核建议而非强结论。
