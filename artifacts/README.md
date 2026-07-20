# SIQ 生成产物目录

## 目录定位

`artifacts/` 用于保存构建、测试、评测、批处理和一次性分析任务生成的输出。它是“生成产物层”，不是长期事实层，也不是版本化样本目录。

这里也是 OpenShell 脱敏证据的发布目录。`var/openshell/` 中的原始 gateway、broker、audit 和 proof 只能在移除凭据、私有正文和机器敏感信息后导出到 `artifacts/openshell/**`，并由 tracked manifest 绑定摘要、大小和路径。

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
- `.gitignore` 放行、经过脱敏扫描并由 manifest 绑定的 OpenShell 证据和日志

不可提交：

- Playwright HTML 报告
- 未经筛选和脱敏的单次评测 JSON / HTML / Markdown 输出
- 未经脱敏的临时日志和批处理导出结果
- OpenShell 原始审计事件、网络请求正文、用户输入输出、token、TLS 和 gateway 状态库

OpenShell 参赛证据和日志在移除凭据值及私有业务正文、通过 secret scan、登记 `tracked-artifacts.json` 后可以提交。`.gitignore` 的后缀例外只提供候选路径，不能绕过 manifest 的路径、大小和摘要绑定。

## 运行或使用建议

- 对于需要长期保留的产物，先确认它究竟属于“事实层”“评测基线”还是“只是运行结果”。
- 如果某个产物需要成为可复现样本，应裁剪和脱敏后迁入 `datasets/`。
- 工程验证时可以把 `artifacts/` 看作一次运行的收纳目录，而不是系统事实存储。

## 维护原则

- 保持该目录默认可清理，不把临时输出误当长期资产。
- 在 README 中始终明确它与 `datasets/`、`eval_datasets/` 和运行态目录的区别。
- 大量输出应按 run id、日期或任务类型分目录，方便清理和回溯。

## 审计证据要求

高精度、OpenShell、多模态或会议发布证据应同时包含机器可读结果、执行参数摘要、版本/hash、时间、真实/Mock 标记和脱敏状态。只保留截图或一段“passed”文本不足以支持复核。

OpenShell 证据必须经过 tracked allowlist、secret scan 和 sanitized manifest；会议/语音证据不得包含原始客户音频、完整 transcript 或声纹 embedding。`artifacts/` 保存的是证明过程的产物，不是生产事实数据库。
