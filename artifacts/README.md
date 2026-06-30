# SIQ 生成产物目录

`artifacts/` 用于保存构建、测试、评测和批处理生成的临时产物。该目录默认被 Git 忽略，除本 README 和必要的 `.gitkeep` 外，不提交生成内容。

## 推荐子目录

| 路径 | 内容 |
| --- | --- |
| `artifacts/test-results` | Playwright、pytest 或其他测试输出 |
| `artifacts/playwright-report` | Playwright HTML 报告 |
| `artifacts/eval-runs` | 单次评测运行输出，建议按 run id 或日期分目录 |
| `artifacts/generated-reports` | 批量生成的临时 Markdown / HTML / JSON 报告 |
| `artifacts/logs` | 一次性任务日志 |

## 与 `datasets/` 的区别

- `artifacts/` 放运行结果，默认不稳定、不可直接作为 golden case。
- `datasets/` 放可复现、可审查、体积受控的评测集、fixtures 和样本。
- 当某个产物需要长期成为基准，请人工裁剪、脱敏、加说明后再迁入 `datasets/`。
