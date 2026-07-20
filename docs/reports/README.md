# 工程审计报告

本目录保存已经完成、具有明确审查日期和范围的工程报告。报告反映的是对应时间点
的代码与运行状态，不应被理解为当前版本的永久结论；涉及问题是否仍然存在，应
结合后续 commit、测试和运行证据重新核验。

| 日期 | 报告 | 范围 |
| --- | --- | --- |
| 2026-07-11 | [性能、可扩展性与数据处理分析](./2026-07-11-performance-analysis.md) | 数据库、API、解析、向量入库和大对象处理。 |
| 2026-07-17 | [全方位代码审查](./2026-07-17-code-review.md) | apps、services、packages、Hermes、infra、scripts、db 和 CI。 |
| 持续更新 | [多市场 document_full PostgreSQL 回测](./market-document-full-postgres-backtest.md) | 多市场解析结果、PostgreSQL 合同和回测结论。 |

新增报告应遵循以下规则：

- 文件名包含日期或稳定版本；
- 开头说明审查范围、commit 或数据快照；
- 区分静态代码结论、离线测试、live smoke 和生产运行证据；
- 不包含密钥、客户材料、原始日志或未经脱敏的运行数据；
- 一次性输出先进入 `artifacts/`，整理为长期可读报告后再进入本目录。
