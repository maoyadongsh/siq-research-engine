# OpenShell V0.6 架构与安全评审记录模板

本文件是发布前人工评审说明，不是自动通过凭证。正式评审记录必须是符合
`infra/openshell/schemas/architecture-security-review.schema.json` 的脱敏 JSON，并通过
`check_v06_completion.py --review-record <path>` 显式传入。Markdown 中出现“批准”字样不再构成证据。
未由真实评审人填写、未绑定当前 evidence digest 或 checklist 不是全真时，readiness 仍为 `NO_GO`。

## 评审对象

- 版本：OpenShell `0.0.83` / Hermes `0.13.0`
- SIQ commit：`<commit-or-dirty-baseline>`
- policy digest：`<sha256>`
- immutable registry digest：`<sha256>`
- sandbox image digest：`<sha256>`
- A/B summary digest：`<sha256-or-not-run>`
- security evidence digest：`<sha256>`

## 必审边界

- [ ] 项目代码、Prompt、workflow 和已固化路径不能写入
- [ ] analysis、session、checkpoint、memory 正常写入
- [ ] 未知公网文件上传被拒绝，Tavily/Exa 和模型路由不被误拦
- [ ] 高危删除触发 sandbox 围栏、恢复和 transaction recovery
- [ ] PostgreSQL/Milvus 只读边界有真实负向证据
- [ ] host rollback 不改变进程身份、API 契约和输出路径
- [ ] 凭据、未经脱敏的 audit、session DB 和原始机器绑定状态不进入 Git；脱敏日志与状态摘要已通过发布门禁

## 风险与遗留项

| 风险/能力缺口 | 缓解措施 | 是否接受 | 备注 |
| --- | --- | --- | --- |
| `<填写>` | `<填写>` | `<是/否>` | `<填写>` |

## 结论

- 评审结论：`<批准灰度 / 暂不批准>`
- 允许的 profile：`<siq_analysis / none>`
- 评审人：`<姓名或组织>`
- 评审时间（UTC）：`<YYYY-MM-DDThh:mm:ssZ>`
- 复核人：`<姓名或组织>`
- 复核时间（UTC）：`<YYYY-MM-DDThh:mm:ssZ>`

机器可验证记录还必须满足：

- `decision` 为 `approved`；
- reviewer 的姓名、角色和组织均为非占位值；
- profile 固定为 `siq_analysis`，OpenShell 固定为 `0.0.83`，Hermes commit 固定为当前冻结版本；
- readiness、service、A/B、rollback、delete、formal egress 和 formal audit 的 SHA-256 与完成门禁当前读取值逐项一致；
- 八项 checklist 全部为 `true`；
- `cutover_performed` 为 `false`。
