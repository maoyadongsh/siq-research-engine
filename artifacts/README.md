# SIQ 生成产物目录

## 目录定位

`artifacts/` 用于保存构建、测试、评测、批处理和一次性分析任务生成的输出。它是“生成产物层”，不是长期事实层，也不是版本化样本目录。

## 主要内容

常见子目录包括：

- `artifacts/test-results`
- `artifacts/playwright-report`
- `artifacts/eval-runs`
- `artifacts/generated-reports`
- `artifacts/logs`

## 当前最新状态

二级市场 MVP 的一次性评测输出使用：

```text
artifacts/eval-runs/2026-07-06-secondary-market-mvp/
```

该目录可保存某次运行的 JSON / Markdown 报告和说明，但只有当结果被裁剪、脱敏并成为长期基线时，才应迁入 `datasets/`。这一区分让项目既能保留验收记录，又不会把一次性产物误当稳定事实。

## 与其他数据目录的边界

- `artifacts/`：构建、测试、评测和批处理生成产物。
- `datasets/`：可版本化稳定样本。
- `eval_datasets/`：历史评测语料。
- `data/` / `var/`：运行态数据。

如果一份输出只服务一次执行、一次调试或一次验收，它通常就属于 `artifacts/`。

## 可提交与不可提交内容

可提交：

- 本 README
- 必要的 `.gitkeep`

不可提交：

- Playwright HTML 报告
- 单次评测 JSON / HTML / Markdown 输出
- 临时日志和批处理导出结果

## 运行或使用建议

- 对于需要长期保留的产物，先确认它究竟属于“事实层”“评测基线”还是“只是运行结果”。
- 如果某个产物需要成为可复现样本，应裁剪和脱敏后迁入 `datasets/`。
- 工程验证时可以把 `artifacts/` 看作一次运行的收纳目录，而不是系统事实存储。

## 维护原则

- 保持该目录默认可清理，不把临时输出误当长期资产。
- 在 README 中始终明确它与 `datasets/`、`eval_datasets/` 和运行态目录的区别。
- 大量输出应按 run id、日期或任务类型分目录，方便清理和回溯。
