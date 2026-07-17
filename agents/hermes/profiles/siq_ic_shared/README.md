# SIQ IC 共享 Profile 资产

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
| `openclaw_asset_migration_inventory.json` | OpenClaw profile / template / skill 资产迁移清单 |
| `golden_case_manifest.json` | 候选黄金用例与尚未通过的质量验收状态 |
| `tasks/` | R0-R4 结构化任务、双库来源分类和 fallback 身份模板 |
| `templates/` | 复用型模板目录 |
| `skills/` | 迁入 Hermes runtime 的一级市场投委会技能包 |

## 当前最新状态

`siq_ic_shared` 是一级市场 Deal OS 的治理中心。当前项目正在把 IC workflow 从 profile 级提示词升级为可审计流程：材料 readiness、R1-R4 报告、争议识别、主席裁决和 project_shared 记忆都应回到这里定义的 contract。

商业价值在于可复制的投委会流程：不同交易、不同委员和不同模型运行结果可以遵守同一套证据等级、报告结构和流程门禁，减少“每个项目靠人肉管理”的不确定性。

## 对可执行 Profile 的约束

所有 `siq_ic_*` 可执行 profile 都应遵守这里的共享规则：

- 使用 `siq_ic_*` 作为 canonical profile ID。
- 共享报告结构与 evidence 等级定义。
- 共享 workflow 阶段和门禁语义。
- 不在各自目录重复定义一套相互冲突的评分或报告 contract。
- 每个 IC profile 同时检索共享项目 Evidence collection 与自己的 Milvus 私有背景 collection。
- 项目 Evidence 与背景知识引用必须保存不同的 `source_class`；背景知识不得验证项目事实。

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
- OpenClaw 本地脚本中携带的密钥、旧 workspace 状态或项目专属样例。

## OpenClaw 资产迁移规则

`scripts/hermes/migrate_openclaw_ic_assets.py` 是当前 OpenClaw -> Hermes profile 资产迁移入口。它只迁移 allowlist 中的可复用资产：

- 各 `siq_ic_*` profile 的 `BOOTSTRAP.md`、`USER.md`、`HEARTBEAT.md` 和少量角色专属协议。
- 可复用报告、检索和投决模板。
- 一级市场投委会相关 Hermes skills。

迁移时会自动将旧 `ic_*` agent ID、`ic_collaboration_shared` collection、OpenClaw workspace 路径和本地检索脚本入口改写为 SIQ/Hermes 语义。

运行任一 `siq_ic_*` profile 时，`scripts/hermes/run_gateway.sh` 会把 `siq_ic_shared/skills` 同步到该 runtime profile 的 `skills/` 目录，确保投委会角色具备同一套一级市场技能包。

## 维护原则

- 共享 contract 变更优先小步、显式、可审阅，避免隐式漂移。
- 新增角色、阶段或模板时，应先修改 shared policy，再落具体 profile 说明。
- 若某个角色需要例外规则，应优先扩展 contract，而不是在本地 profile 私自分叉。
- 这里的内容应保持机器可读和人可读兼顾，便于 API、前端和批处理脚本引用。
