# SIQ 可版本化数据集目录

## 目录定位

`datasets/` 用于保存可以进入源码仓、并且适合长期维护的稳定数据集、fixtures 和小型样本。它是“可版本化样本层”，而不是运行态大文件目录。

它支撑 README 中报告的测试和评测体系：二级市场 market ingestion、财务 QA、document parser cases、一级市场 IC golden cases 和 OpenShell A/B 前置样本都应尽量沉淀成小型、脱敏、可复现的数据集，而不是留在一次性 `artifacts/` 输出里。

## 主要内容

推荐子类包括：

- `datasets/eval`：稳定评测集与 golden cases
- `datasets/fixtures`：单元测试与 contract tests 使用的小型 fixture
- `datasets/samples`：最小样本、演示材料和人工构造示例
- `datasets/market_ingestion`：二级市场 MVP 与 market evidence package 静态评测样本

## 当前最新状态

当前新增的商业 MVP 样本位于：

```text
datasets/market_ingestion/secondary_market_mvp_cases.json
```

它用于验证官方来源命中、package 可定位性、证据覆盖、三大表覆盖和 bridge check 等指标。这个目录代表“可复现评测样本”，不是下载文件或 parser 运行结果的落点。

## 与其他数据目录的边界

- `datasets/`：可版本化稳定样本。
- `eval_datasets/`：历史评测语料和回归集。
- `artifacts/`：单次运行输出。
- `data/` / `var/`：运行态数据。

如果某份材料无法稳定复现、体积过大、含敏感信息或只是单次运行输出，它就不属于 `datasets/`。

## 可提交与不可提交内容

可提交：

- JSON、JSONL、CSV、Markdown 等小型文本样本
- 经裁剪、脱敏、可复现的 fixture
- 对 schema、来源、用途有清晰说明的数据

不可提交：

- 大体积原始披露文件
- 用户上传原文、敏感合同、版权不明大文件
- 运行态数据库、缓存和一次性产物

## 运行或使用建议

- 新增样本时尽量附上用途、来源、schema version 和更新方式。
- 当某份 `artifacts/` 结果被证明值得长期保留时，应先裁剪和脱敏，再迁入 `datasets/`。
- 对测试样本，应优先最小化，避免把大文件问题转嫁给仓库体积。

## 维护原则

- 优先稳定、可复现、体积受控。
- 数据集目录服务测试与文档，不服务临时存储。
- 样本变更要考虑下游测试和评测的兼容性。
