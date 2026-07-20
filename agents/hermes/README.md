# SIQ Hermes 智能体体系

## 平台定位

`agents/hermes` 保存 SIQ 的智能体配置、协作边界、共享脚本和角色说明。这里维护的是“可审阅的协作规则层”，而不是运行态会话或模型缓存。它把不同研究角色组织成一套受控协作系统，让智能体围绕同一份证据层工作，而不是围绕模型记忆自由发挥。

Hermes 在产品上同时服务两大智能体集群：

| 产品面 | Profiles | 主要价值 |
| --- | --- | --- |
| 二级市场投研分析智能体集群 | `siq_assistant`、`siq_analysis`、`siq_factchecker`、`siq_tracking`、`siq_legal` 及多市场变体 | 围绕官方披露、财务指标、evidence package 和法律法规形成分析、核查、跟踪、法务闭环 |
| 一级市场投研决策智能体集群 | `siq_ic_master_coordinator`、`siq_ic_chairman`、`siq_ic_strategist`、`siq_ic_sector_expert`、`siq_ic_finance_auditor`、`siq_ic_legal_scanner`、`siq_ic_risk_controller` | 围绕 Deal OS、材料中心、专家意见、争议处理和 R0-R4 投委会流程形成可回放决策链 |

## 智能体矩阵

| Profile | 默认端口 | 前端入口 | API 前缀 | 核心职责 |
| --- | ---: | --- | --- | --- |
| `siq_assistant` | `18642` | `/chat` | `/api/chat/*` | 通用问答、指标解释、证据定位 |
| `siq_analysis` | `18651` | `/analysis` | `/api/analysis/*` | 年度经营分析、风险链条和研究报告 |
| `siq_factchecker` | `18649` | `/verify` | `/api/factchecker/*` | 对分析报告做事实、计算和证据核查 |
| `siq_tracking` | `18650` | `/tracking` | `/api/tracking/*` | 持续跟踪、预警、更新记录 |
| `siq_legal` | `18652` | `/legal` | `/api/legal/*` | 法规检索、合规分析、意见书草稿 |
| `siq_ic_master_coordinator` | `18660` | `/deals` | `/api/deals/*` | 投委会流程编排、证据门禁、专家材料收口 |
| `siq_ic_chairman` | `18661` | `/deals` | `/api/deals/*` | 投委会最终裁决、条件化投决与分歧处理 |
| `siq_ic_strategist` | `18662` | `/deals` | `/api/deals/*` | 战略适配、时点、宏观与基金 thesis |
| `siq_ic_sector_expert` | `18663` | `/deals` | `/api/deals/*` | 行业格局、产品验证、竞争与市场判断 |
| `siq_ic_finance_auditor` | `18664` | `/deals` | `/api/deals/*` | 财务一致性、预测、估值和压力测试 |
| `siq_ic_legal_scanner` | `18665` | `/deals` | `/api/deals/*` | 法务尽调、条款风险和监管暴露 |
| `siq_ic_risk_controller` | `18666` | `/deals` | `/api/deals/*` | 下行情景、红黄线、交易保护条款 |

## 协作原则

Hermes 在 SIQ 中不是“一个万能助手”，而是一组分工清晰的受控角色。它们共享证据底座，但不共享越权权限。

- 证据优先：所有关键结论都必须能回到 Wiki、PostgreSQL、PDF 页码、表格编号或法规条款。
- 角色分工：分析负责形成研究结论，核查负责拆穿错误，跟踪负责持续观察，法务负责依据和合规，投委会角色负责一级市场尽调与决策流程。
- 边界明确：任何角色都不能凭模型记忆伪造公司、指标、页码、法规或数据库记录。
- 产物可回放：报告优先写入标准目录，由 Web 和 API 再统一读取和展示。

## 当前最新状态

| 方向 | 状态 | 说明 |
| --- | --- | --- |
| 二级市场 profiles | assistant / analysis / factchecker / tracking / legal 形成基础闭环 | 面向财报、证据包、报告、核查和持续跟踪 |
| 一级市场 IC profiles | chairman / strategist / sector / finance / legal / risk / coordinator 并行维护 | 面向 Deal OS、R1-R4 尽调、分歧裁决和投委会会议 |
| 拟人化全量记忆 | Hermes 原生会话记忆 + 本地临时任务记忆 + PostgreSQL 权威长期记忆 + Milvus 语义索引 + reranker | 支持 user_private、project_shared、system_shared 三类可见性、半衰期衰减和按需全量召回 |
| OpenShell 安全运行面 | `siq_analysis` 分析助手已在 NVIDIA OpenShell 上完成真实前端全链路验证 | 支持公司范围自动创建、对话代际、资源池租约/隔离/恢复和 Host 回退；正式生产质量发布门仍为 `NO_GO`，但不影响“分析助手链路已全面跑通”的事实 |
| 共享脚本 | 财务计算、勾稽校验、引用 schema、PostgreSQL query 等能力集中维护 | 减少各 profile 自行实现导致的结果漂移 |
| API / Web 接入 | 二级市场 profile 已有稳定前端入口；IC profile 通过 `/deals` 和会议室逐步产品化 | 保持模型协作与产品工作流一致 |

Hermes 的商业价值是“可治理的专家协作”。它不是把一个聊天框包装成多个角色，而是让每个角色围绕同一证据层、同一项目权限和同一产物目录工作，从而让研究过程能被复核、交接和审计。

## 拟人化全量记忆系统

SIQ 的智能体记忆不是简单聊天摘要，而是让研究助手具备“长期共事感”的记忆系统。Hermes 自身的会话连续性、本地临时任务状态、PostgreSQL 权威记忆账本和 Milvus 语义索引共同工作，记忆用于承接上下文和偏好，事实仍由 evidence package、数据库和原始材料裁定。

| 记忆层 | 内容 | 边界 |
| --- | --- | --- |
| Hermes 原生记忆 | 会话、响应、profile runtime、checkpoint、短期上下文 | 保持同一 profile 的上下文连续和工具执行状态 |
| 本地临时任务记忆 | 当前任务工作目录、报告草稿、临时 evidence、intermediate artifact | 支撑长任务分阶段推理、失败恢复和续写，不作为长期事实源 |
| PostgreSQL 权威长期记忆 | 用户偏好、纠错、项目结论、IC 阶段产物、来源、scope、ACL、有效期 | 可审计、可授权、可删除，是长期记忆账本 |
| Milvus 语义索引 | profile knowledge、动态 memory item 向量、scope metadata | 用于语义召回与泛化检索，可从权威层重建 |

关键能力：

- 拟人化连续性：智能体能记住用户偏好、历史纠错、项目上下文和协作习惯。
- 全量记忆：长期记忆按用户、项目、profile、agent group 和可见性保存完整记忆项，而不是只保留最近摘要。
- 半衰期：动态记忆默认随时间衰减，近期经验自然优先，旧偏好不会永久污染新任务。
- 按需全量召回：用户明确要求完整历史、全量检索或不要遗忘时，可绕过半衰期，但仍保留 ACL、scope 和上下文长度保护。

核心原则是：**记忆提供连续性，证据决定事实。** 财务数字、法律条款、投资判断和投委会结论必须回到当前证据层，不允许仅凭模型记忆固化事实。

### 记忆召回的实际排序

默认检索不是“向量相似度前几名直接塞入上下文”，而是候选生成、ACL 过滤、rerank、时间衰减和上下文预算共同决定：

```text
query + user/project/profile/ResearchIdentity
  -> PostgreSQL lexical / pgvector 或 Milvus semantic candidates
  -> tenant + visibility + owner/deal/project + agent_group/profile ACL
  -> reranker
  -> 30 天半衰期 + confidence/importance 排序
  -> 有界 memory context
```

显式全量召回只绕过时间衰减，并提高有界候选上限；不会绕过权限，也不会把其他公司或其他 parse run 的研究记忆混入当前事实上下文。向量写入失败不影响 PostgreSQL 权威项保存，索引可异步重建。

## 多模态智能体能力

Hermes profiles 以同一岗位合同消费不同模态，但每种模态都保留自己的证据边界：

| 模态 | 入口 | Hermes 侧作用 | 事实边界 |
| --- | --- | --- | --- |
| 图片 | Chat 上传 PNG/JPEG/WebP/GIF；parser 页图/figure | 本机 Nemotron 先做原生视觉理解，Hermes 再结合问题、历史附件和结构化证据回答 | 图片模型描述是派生内容，财务数字仍需 evidence/financial guard |
| 文档 | PDF、Office、HTML、TXT/Markdown/CSV/JSON/RTF | 等待 parser artifact 或有界文本预览，支持同会话继续追问 | 路径受 chat root 白名单和用户归属控制 |
| 短语音 | Chat 录音上传 | FunASR 转写成为用户问题，音频作为会话附件保留 | 受大小、时长、格式和用户归属限制 |
| 会议语音 | 实时 PCM 或录音导入 | stable transcript 进入修正、滚动纪要、最终纪要和行动项 profile | Hermes 只接收文本 segment，禁止接收原音频、声纹或 embedding |

本地 Nemotron、Qwen、Gemma 与云端模型可按 profile/任务切换；模型是可替换执行器，岗位规则、引用合同、记忆 ACL 和财务工具回执不会随模型切换丢失。

## 高精度回答合同

所有公开市场 profiles 共享 `financial_source_routing_contract.md` 与 `financial_calculation_contract.md`：

- 先解析市场、公司、filing 和 parse run，再检索；不能用“最新报告”或名称相似度代替身份确认。
- 主表净额/账面价值先走三大表，附注原值/准备/构成再走 note links；混合问题必须双来源。
- 同比、占比、单位换算、CAGR、人均等派生值必须调用 `financial_calculator.py`，不能由模型心算。
- 商誉等原值-准备-净额问题调用 `financial_reconciliation_validator.py`；每个输入带 evidence ID。
- trace 的 ResearchIdentity、期间、单位、币种和输入证据由 API 后端重新验证和计算，正文里写“已核验”不能替代工具回执。
- 缺少事实或校验失败时，profile 应输出证据缺口、N/A 或 request review，而不是补写一个完整但不可验证的答案。

这使不同模型、不同 profile 和 Host/OpenShell 两种运行面仍能遵守同一精度基线。

## 模型选择与协同

Hermes 将模型视为带来源的执行目标，而不是无状态字符串。当前主路径是：

| 模型路径 | 主要用途 | 治理方式 |
| --- | --- | --- |
| 云端 StepFun / `step-3.7-flash` | 默认云端复杂推理、报告与工具任务 | custom provider、200K context contract、受控 API key、OpenShell REST policy |
| 本地 Nemotron / `nemotron_3_nano_omni` | 私有问答、长上下文、工具调用、图片/音频/视频原生理解 | DGX Spark vLLM 8007、NVFP4、模型控制和本地 fallback |
| 本地 Qwen/Gemma | 其他本地文本生成与对照评测 | 独立 provider/端口，按 profile 显式切换 |

API 的 `hermes_model_control.py` 将 StepFun、Nemotron、Qwen、Gemma、MiniMax 与 Kimi 归一为可识别 mode，并保留本地/云端 fallback 顺序。会议任务进一步使用 immutable target pool：创建执行快照后固定 provider/model/locality/settings version，避免运行中修改 profile 导致历史纪要无法复现。

模型切换只改变推理执行器，不改变 evidence、memory scope、financial trace、report contract 或 OpenShell company scope。云端主模型不可用时允许显式降级到本地，敏感场景也可直接选择 Nemotron；任何 fallback 都应在回执中可见。

## 共享脚本与共用能力

`profiles/shared` 保存多 profile 共用的底层能力：

| 脚本 | 作用 |
| --- | --- |
| `financial_calculator.py` | 财务比率和派生指标计算 |
| `financial_reconciliation_validator.py` | 财务勾稽校验 |
| `citation_schema.py` | 引用格式和 schema |
| `local_citations.py` | 本地证据映射与引用修复 |
| `pg_query.py` | 只读 PostgreSQL 查询辅助 |
| `statement_metric_lookup.py` | 财务科目与指标映射 |
| `update_company_index.py` | 公司索引与目录维护 |

这些能力属于共享证据基础设施，应该复用而不是在各 profile 中各自复制一份逻辑。

## 运行入口与端口

### 一键启动

```bash
cd /home/maoyd/siq-research-engine
export SIQ_AUTH_SECRET_KEY="$(openssl rand -hex 32)"
./start_all.sh
```

### 单独启动某个 profile

```bash
cd /home/maoyd/siq-research-engine
scripts/hermes/run_gateway.sh siq_analysis
```

### 基础健康检查

```bash
curl -s http://127.0.0.1:18642/health
curl -s http://127.0.0.1:18651/health
curl -s http://127.0.0.1:18649/health
curl -s http://127.0.0.1:18650/health
curl -s http://127.0.0.1:18652/health
curl -s http://127.0.0.1:18660/health
curl -s http://127.0.0.1:18661/health
curl -s http://127.0.0.1:18662/health
curl -s http://127.0.0.1:18663/health
curl -s http://127.0.0.1:18664/health
curl -s http://127.0.0.1:18665/health
curl -s http://127.0.0.1:18666/health
```

IC profiles 默认随主链路启动，并配合 `/deals` 相关链路使用。资源受限或仅运行公开市场流程时，可设置 `SIQ_ENABLE_IC_HERMES=0` 显式关闭这 7 个网关。

使用 systemd user 部署时，7 个 IC profiles 由 `hermes-gateway-siq-ic@.service` 模板分别托管；`siq-research-engine.service` 不重复拉起 Hermes，以避免与已有网关端口冲突。

## NVIDIA OpenShell 运行面

Host Hermes 仍是全局环境回退基线；`siq_analysis` 已可由 API 控制面按公司范围路由到 NVIDIA OpenShell 沙箱，并已经通过真实 `/api/analysis/chat/stream` 前端请求完成端到端验证。当前实现没有采用 NemoClaw / NemoHermes，而是保留 SIQ `/v1/runs`、SSE、停止、报告路径、profile 和 Prompt 合同，在 OpenShell 沙箱中运行 BYOC Hermes 镜像。

OpenShell 路径已经验证：

| 能力 | 说明 |
| --- | --- |
| 网关 / 沙箱 | 使用 SIQ 专用 OpenShell 网关创建、探测、转发、停止和删除沙箱 |
| Provider / Broker | Provider 凭据由 OpenShell 网关管理，公网和数据访问经宿主出网/数据 broker 与请求身份约束 |
| 文件/进程边界 | 当前公司 Wiki 只读、当前公司 `analysis/` 可写、其他公司拒写、Landlock 与 seccomp 约束 |
| 对话代际 | 同一对话同公司复用热沙箱代际，切换公司生成隔离代际 |
| 资源池租约 / 恢复 | 请求级租约、单公司单写者、owner 隔离、终态/写入静默后释放、API 重启恢复 |
| TTL 回收 | 空闲范围在无活跃/等待/孤儿租约时自动删除沙箱 |

这条路径对 `siq_analysis` 而言已经全面跑通：真实前端请求、公司 scope 自动创建、Hermes SSE、lease、generation、runtime provenance、终态释放和 TTL 回收均已验证。正式生产质量发布门仍为 `NO_GO`，含义是当前尚未把所有 A/B 质量线、人工安全评审和可发布证据凑齐，也没有执行全局默认切流；它不表示分析助手 OpenShell 功能没有完成。

### OpenShell 对投研业务的具体价值

OpenShell 解决的不是普通容器部署，而是“让会使用终端、文件和网络的研究智能体在最小权限下工作”：

- 文件边界按当前公司生成：公司 Wiki 只读，当前公司 `analysis/` 可写，源码、Prompt、其他公司目录拒写。
- 凭据不进入 agent 环境明文；Provider 由网关注入，外部 REST 路径、方法与目的地主机受策略约束。
- PostgreSQL/Milvus 通过只读 broker 暴露固定语法、collection/field allowlist、行数/超时/响应大小限制，并带签名请求身份。
- 对话沙箱代际同时绑定 owner、profile、company scope；跨公司切换生成新代际，不复用旧工作空间。
- 每次运行返回 runtime origin、sandbox/lease 信息和 Host fallback 原因，方便质量 A/B 与事故追踪。
- 安全能力与模型质量分开门禁：沙箱能运行不代表报告质量达标，正式切流仍需证据包、A/B 和人工安全评审。

## 运行态目录

默认 runtime home：

```text
data/hermes/home/
```

常见结构：

```text
data/hermes/home/
  profiles/
  sessions/
  logs/
  responses/
```

常用环境变量：

| 变量 | 用途 |
| --- | --- |
| `SIQ_HERMES_HOME` | Hermes runtime 根目录 |
| `SIQ_HERMES_PROFILES_ROOT` | profiles 根目录 |
| `SIQ_ENABLE_IC_HERMES` | 是否启用 IC profiles 网关 |

## 基础环境与测试情况

Hermes profile 配置本身主要是文件合同和运行脚本，实际模型运行依赖 `apps/api`、Hermes gateway、OpenAI-compatible 模型服务、PostgreSQL、Milvus 和可选 OpenShell runtime。当前根 README 采样环境为 Python `3.13.12`、Node `v22.22.2`、Docker `29.1.3`、uv `0.11.7`，但 Hermes 具体进程通常由 `scripts/hermes/run_gateway.sh` 或 systemd user unit 托管。

| 测试面 | 命令或来源 | 关注点 |
| --- | --- | --- |
| profile/gateway smoke | `scripts/hermes/run_gateway.sh <profile>` 后访问 `/health` | profile 配置、端口、Hermes gateway 可用性 |
| API Agent 测试 | `cd apps/api && uv run python -m pytest tests` | SSE、停止/恢复、附件、memory 注入、runtime 路由 |
| OpenShell 灰度回归 | `docs/siq-openshell-hermes-integration-status.md` | 最新记录 `78 passed`，覆盖范围自动创建、对话代际、租约、TTL、恢复和 Host 回退 |
| 发布门禁 | `python3 scripts/openshell/check_v06_completion.py --json` | 正式生产门禁；当前仍应保持 `NO_GO` |

## 产物目录与前端/API 对接

Hermes 的报告型产物最终会进入标准 Wiki 路径，再由 `apps/api` 聚合并交给前端展示。

| 类型 | 标准目录 |
| --- | --- |
| 分析报告 | `companies/<company_id>/analysis/` |
| 事实核查 | `companies/<company_id>/factcheck/` |
| 持续跟踪 | `companies/<company_id>/tracking/` |
| 法务意见 | `companies/<company_id>/legal/` |
| 一级市场流程 | `data/wiki/deals/...` |

前端与 API 的典型对接方式：

- `/chat` + `/api/chat/*` 对应 `siq_assistant`
- `/analysis` + `/api/analysis/*` 对应 `siq_analysis`
- `/verify` + `/api/factchecker/*` 对应 `siq_factchecker`
- `/tracking` + `/api/tracking/*` 对应 `siq_tracking`
- `/legal` + `/api/legal/*` 对应 `siq_legal`

## 维护原则

- 把角色边界写清楚，优先于把提示词写得“像真人”。
- 共用能力沉入 shared scripts，不在各 profile 中重复实现。
- 产物路径、profile ID 和端口要保持稳定，避免前端 / API / 网关三方漂移。
- 所有对外结论都必须和证据层绑定，允许保守、不允许编造。
- 这里保存的是协作规则和配置，不保存会话、缓存、向量索引或运行日志。

## 创新性与商业价值

Hermes 采用“职责型智能体 + 共享证据层 + 阶段状态机”，区别于让多个人格自由讨论的多智能体演示。每个 profile 的输入、禁止行为、交付物和升级条件都可检查，模型更换不会改变业务责任边界。

| 机制 | 技术难点 | 商业价值 |
| --- | --- | --- |
| Profile 即岗位合同 | AGENTS、skills、templates、tasks 和 gateway 共同约束 | 将机构研究方法论固化为可迭代资产 |
| 证据绑定输出 | evidence id、source map、审计 trace 与报告 manifest | 降低无来源结论进入正式材料的风险 |
| IC R0-R4 分阶段 | intake、专家报告、争议裁决、汇总复核、人工确认 | 投委会过程可回放、可签核、可定位责任 |
| 四层记忆 | Hermes 原生记忆、本地临时任务记忆、PostgreSQL 权威账本、Milvus 语义索引，带 scope/ACL/半衰期 | 让助手有长期共事感，同时避免把记忆误当事实 |
| OpenShell 原生集成 | 保留 Hermes `/v1/runs` 合同，在 NVIDIA OpenShell 沙箱中运行 BYOC Hermes | 在不采用 NemoClaw 的前提下获得受控执行、凭据隔离、公司级沙箱和 Host 回退 |
| 可替换模型网关 | profile 与具体模型服务解耦 | 客户可按成本、隐私和效果选择模型 |

商业壁垒来自“组织流程可执行化”：SIQ 不只提高单次写作速度，还把分析、核查、法务、风控和主席裁决变成可治理的协作系统。
