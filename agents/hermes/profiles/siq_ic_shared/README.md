# SIQ IC Shared Profile Assets

## 目录定位

`siq_ic_shared` 是 SIQ 一级市场投委会体系的共享 contract 与政策目录。它不是一个可执行 agent profile，而是所有 `siq_ic_*` 角色共用的 workflow policy、report contract、evidence contract、prompt contract 和角色矩阵来源。

## 共享合同与政策文件

| 文件 | 作用 |
| --- | --- |
| `ic_workflow_policy.json` | 投委会阶段、角色权重、流程门禁和评分政策 |
| `ic_profile_matrix.json` | profile ID、端口、角色名称与来源映射 |
| `ic_report_contract.md` | 专家报告和主席报告的结构与质量要求 |
| `ic_evidence_contract.md` | 证据分类、引用、验证和争议处理规则 |
| `ic_prompt_contract.md` | prompt 组合、角色边界与协作限制 |
| `openclaw_script_migration_matrix.json` | OpenClaw 到 SIQ 的迁移追踪矩阵 |
| `templates/` | 复用型模板目录 |

## 对可执行 Profile 的约束

所有 `siq_ic_*` 可执行 profile 都应遵守这里的共享规则：

- 使用 `siq_ic_*` 作为 canonical profile ID。
- 共享报告结构与 evidence 等级定义。
- 共享 workflow 阶段和门禁语义。
- 不在各自目录重复定义一套相互冲突的评分或报告 contract。

## 与 `data/wiki/deals` 的关系

一级市场项目的执行型产物应落在 `data/wiki/deals` 或其后续稳定别名目录下。`siq_ic_shared` 不存放具体 deal 的运行态结果，而负责定义这些结果应该遵守的结构与规则。

换句话说：

- 这里定义 contract。
- `data/wiki/deals` 承载实例化产物。

## 不应放入本目录的内容

以下内容不应写入 `siq_ic_shared`：

- 会话、memory、响应缓存、vector store。
- `.venv`、运行日志、上传文件和中间产物。
- 某个具体项目的执行态报告或一次性草稿。

## 维护原则

- 共享 contract 变更优先小步、显式、可审阅，避免隐式漂移。
- 新增角色、阶段或模板时，应先修改 shared policy，再落具体 profile 说明。
- 若某个角色需要例外规则，应优先扩展 contract，而不是在本地 profile 私自分叉。
- 这里的内容应保持机器可读和人可读兼顾，便于 API、前端和批处理脚本引用。
