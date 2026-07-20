# SIQ Research Engine - DGX Spark Hackathon 评审对照与演示指南

## 参赛定位

SIQ Research Engine 参加 **DGX Spark Hackathon**。本项目不是将云端聊天模型简单搬到开发机，而是把 DGX Spark 作为一台可承载“多模型、多数据服务、多智能体、多安全边界”的完整投研基础设施：

- 本地 Nemotron 负责私有多模态推理。
- MinerU 负责文档视觉解析。
- Qwen3-VL Embedding/Reranker 负责独立向量候选与精排。
- FunASR 负责语音识别与会议转写链路。
- StepFun `Step-3.7 Flash` 负责云端主推理。
- Hermes 负责岗位智能体协作与记忆。
- OpenShell 负责不可信智能体执行面的安全隔离。
- PostgreSQL、Milvus、MinIO、Redis 和 LLM-Wiki 共同构成本地数据与知识服务层。

项目的核心竞争力不是单一模型参数，而是把高精度解析、可审计证据、确定性财务计算、多智能体协作、多模态交互和安全执行统一成可运行的投研生产线。

## 评审标准对照

| 评审维度 | 权重 | SIQ 对应能力 | 可核验依据 |
| --- | ---: | --- | --- |
| 项目实用性、行业落地价值与技术创新性 | 25% | 面向研究员、基金经理、投研数据团队、合规和投委会；覆盖官方披露、多市场财报、尽调、法律和持续跟踪；以 LLM-Wiki 逻辑跳转、财务勾稽、确定性计算和 OpenShell 业务权限解决传统 RAG 与通用 Agent 的痛点 | 根 README 的“商业价值与可复制壁垒”“LLM-Wiki”“财务校验体系”章节 |
| 智能体融合与模型优化技术深度 | 25% | Hermes 二级市场/一级市场岗位集群、四层记忆与 ACL、StepFun/Nemotron 双主模型、本地原生图片/语音、多 vLLM 并行、Reranker 并发保护和 OpenShell+Hermes 控制面 | `agents/hermes/`、`apps/api/services/agent_memory*`、`infra/model-services/`、`infra/openshell/` |
| 项目完整性 | 20% | Web、FastAPI、Hermes、解析、市场服务、PostgreSQL、Milvus、MinIO、Redis、会议和安全运行面完整协同；提供报告、引用、审计、降级和恢复链路 | `apps/web/`、`apps/api/`、`apps/*parser/`、`services/`、各模块 README 与测试 |
| 平台适配性 | 15% | 充分使用 NVIDIA DGX Spark GB10、CUDA 13、aarch64、统一内存和独立 vLLM 进程；结合 Nemotron、Qwen、Gemma、FunASR、NVIDIA OpenShell 与 StepFun | 根 README 的“模型体系与 DGX Spark 单机并行架构”和 `infra/model-services/` |
| 演示效果 | 10% | 用一条可复核主线展示“材料进入 -> 解析 -> 证据 -> 多模态提问 -> 智能体协作 -> 财务校验 -> 引用回跳 -> OpenShell provenance” | 本文“Demo 主线”和“演示验收清单” |
| 赛事征文 / DGX Spark 黑客松“十日谈” | 5% | 记录从需求、硬件摸底、模型并行、证据链、智能体记忆、OpenShell 安全、并发修复到 Demo 打磨的工程决策和失败复盘 | 本文“十日谈记录框架”，每一天绑定 commit、截图、测试或运行 receipt |

## 各评分项的表达重点

### 1. 项目实用性、行业落地价值与技术创新性 - 25%

建议首先回答三个问题：

1. **谁会使用**：证券研究员、基金经理、一级市场投资经理、财务/法务/风控、投委会主席、投研数据和合规团队。
2. **解决什么高成本问题**：官方材料发现、跨格式解析、数字核对、证据回溯、多人协作、知识复用、合规审计和私有部署。
3. **为什么不是普通 RAG**：LLM-Wiki 不依赖 embedding/ranker，不把财报切成失去关系的匿名 chunk，而是使用 ResearchIdentity、主题路由、fact/relation/claim、主表/附注链接和 source coordinates 做逻辑跳转。

需要重点展示的创新：

- 六市场官方披露与市场隔离 identity。
- 高精度文档 artifact 与 page/table/cell/bbox/source map。
- LLM-Wiki 非切片知识工程。
- 财务报表勾稽、原值-准备-净额校验和确定性公式重算。
- 回答 citation、financial trace 和 answer audit。
- 自研 OpenShell+Hermes 公司级安全运行面。
- 从公开市场研究延伸到一级市场 Deal OS 和 R0-R4 投委会流程。

商业价值可用以下指标表达：

| 价值方向 | 建议指标 |
| --- | --- |
| 降低研究时间 | 单份报告处理时长、人工翻页次数、首稿时间 |
| 降低事实风险 | 引用覆盖率、财务校验通过率、人工复核问题数 |
| 复用组织知识 | 历史纠错复用率、重复研究减少量、记忆命中率 |
| 私有化交付 | 敏感材料外发量、本地可用率、模型替换成本 |
| 决策可回放 | 项目阶段完整率、争议记录覆盖率、审计回放时间 |

### 2. 智能体融合与模型优化技术深度 - 25%

智能体部分不能只展示多个角色名称，要展示职责合同和协同机制：

- 二级市场：assistant、analysis、factchecker、tracking、legal。
- 一级市场：coordinator、chairman、strategist、sector、finance、legal、risk。
- 每个 profile 共享证据底座，但拥有不同的禁止行为、输出路径和升级条件。
- 分析结果必须接受 factchecker、财务守卫和引用审计，不允许角色之间相互背书代替证据。

记忆管理应说明四层结构：

```text
Hermes 原生会话记忆
  + 本地临时任务记忆
  + PostgreSQL 权威长期记忆
  + Milvus 可重建语义索引
```

同时说明 `user_private`、`project_shared`、`system_shared`、ResearchIdentity、半衰期、rerank 和上下文预算。核心原则是“记忆提供连续性，证据决定事实”。

模型优化深度应展示：

- StepFun `Step-3.7 Flash` 与 Nemotron 双主模型选择和回退。
- Nemotron NVFP4、FP8 KV cache、256K context target、prefix cache 和受控并发。
- MinerU、Embedding、Reranker、FunASR 独立 GPU memory target。
- Reranker 1:N batch、单 EngineCore 并发保护和受控 503。
- 模型来源、fallback 和 runtime origin 可进入运行回执。

### 3. 项目完整性 - 20%

评委应能看到一套完整产品，而不是脚本集合：

```text
React Web
  -> FastAPI 控制面
  -> parser / market services / meeting / vector ingest
  -> LLM-Wiki / PostgreSQL / Milvus / MinIO / Redis
  -> Hermes 多智能体
  -> Nemotron / StepFun / MinerU / Qwen / FunASR
  -> OpenShell 安全运行面
```

完整性证明应包含：

- 登录、会话、附件归属和 source token。
- 长任务状态、SSE、取消、恢复和降级。
- 解析 artifact、质量门和导入合同。
- 报告展示、引用回跳和 validation cards。
- 模型 health、最小推理和业务质量分层检查。
- README、runbook、启动脚本归档和可执行验证命令。
- 已知限制明确呈现，不把未通过的生产门禁伪装成完成。

### 4. DGX Spark 平台适配性 - 15%

需要突出 DGX Spark 的作用不是“提供一张 GPU”，而是让以下工作负载在一台设备上高密度并行：

| 计算/服务 | DGX Spark 上的职责 |
| --- | --- |
| Nemotron | 生成、推理、工具调用、图片/音频/视频原生理解 |
| MinerU | PDF/扫描件布局、表格、公式和图片解析 |
| Qwen3-VL Embedding | 文本/图片/表格/法规/记忆向量化 |
| Qwen3-VL Reranker | Milvus 与 Agent memory 候选精排 |
| FunASR | Chat 语音和会议 ASR/VAD/说话人链路 |
| ARM CPU | FastAPI、Flask、规则校验、音频处理、文件 hash、OpenShell 控制面 |
| 统一内存 | 模型权重、KV cache、数据库 cache、Milvus segment 和 page cache 的共享资源池 |

应展示的 NVIDIA 技术元素：

- NVIDIA GB10 / CUDA 13 / aarch64。
- NVIDIA Nemotron 3 Nano Omni。
- NVIDIA OpenShell Gateway、Sandbox、Provider、Policy、Landlock、seccomp 和 service forwarding。
- vLLM continuous batching、prefix caching、FP8 KV、NVFP4。
- 多模型独立进程、独立端口、独立健康检查和资源预算。

StepFun 应明确作为云端主模型参与系统，而不是只出现在配置文件：展示 model selection、provider、实际来源回执和本地 Nemotron fallback/私有路径。

## Demo 主线

建议将现场 Demo 控制在一条闭环内，避免变成多个孤立菜单：

```text
1. 选择一家公司和一份官方年报
2. 展示 MinerU/VLM 恢复的页码、表格、图表和 source map
3. 展示 LLM-Wiki 按 company/report/topic 逻辑跳转，不经过传统切片 RAG
4. 输入一个财务问题，再上传一张图表或发送一段语音问题
5. Nemotron 本地视觉/语音链路与 StepFun 云端主模型按策略协同
6. Hermes analysis/factchecker 调用受控工具，生成 citation 和 financial trace
7. 展示资产负债勾稽、同比/占比确定性重算及异常状态
8. 打开原始 PDF 页码/表格回跳
9. 展示 runtime origin、OpenShell scope/generation 和审计回执
```

### Demo 叙事原则

- 先讲真实业务问题，再展示技术。
- 每一步只解释一个创新点。
- 不在现场等待大模型冷启动，提前完成模型 readiness。
- 不使用无法回跳的孤立答案作为成功结果。
- 不隐藏 fallback、warning、degraded 或 evidence gap。
- 把 DGX Spark 资源和本地模型状态作为系统证据，而不是背景图片。

### 演示验收清单

- [ ] Web、API、Hermes gateway 和演示公司数据 ready。
- [ ] Nemotron、MinerU、Embedding、Reranker、FunASR health 通过。
- [ ] 至少完成一次真实图片、文档、rerank 和授权音频最小请求。
- [ ] StepFun provider 可用，模型来源回执可展示。
- [ ] OpenShell 分析助手真实链路 ready，Host fallback 可解释。
- [ ] 演示问题提前固定，包含财务事实、派生计算和附注明细。
- [ ] PDF/source 回跳可用，页码和表格定位正确。
- [ ] financial trace 和 validation card 可展示。
- [ ] 屏幕录制分辨率、字体、网络和音频已预演。
- [ ] 准备本地录屏作为网络异常时的兜底材料。

## “十日谈”记录框架

参赛成果记录建议按十个工程主题组织，而不是只写功能完成列表：

| 日程主题 | 应记录的工程问题 | 建议绑定的证据 |
| --- | --- | --- |
| Day 1 | 投研场景、评审目标、DGX Spark 资源盘点 | 需求基线、硬件/驱动采样、初始架构图 |
| Day 2 | 官方披露、多市场身份和解析链路 | market adapter、下载 receipt、parser artifact |
| Day 3 | LLM-Wiki 知识抽取与逻辑跳转 | retrieval index、facts/claims、note/document links |
| Day 4 | 财务勾稽和确定性计算 | `financial_checks`、calculator trace、负向样例 |
| Day 5 | Hermes 岗位集群和记忆 ACL | profile contract、memory scope、协作输出 |
| Day 6 | Nemotron、MinerU、Qwen、FunASR 多 vLLM 并行 | 启动脚本、端口/health、DGX Spark 资源曲线 |
| Day 7 | OpenShell + Hermes 安全运行面 | policy、mount、Provider/Broker、sandbox provenance |
| Day 8 | 并发、恢复、Reranker 和服务故障修复 | lease/recovery receipt、并发回归、故障前后日志 |
| Day 9 | Web/API/应用中心整合和 Demo 叙事 | 前后端录屏、端到端测试、演示脚本 |
| Day 10 | 结果复盘、商业化表达和参赛材料 | final Demo、README、指标摘要、已知限制清单 |

### 每日文章建议结构

1. 当天要解决的问题。
2. 为什么现有方案不够。
3. 关键设计选择与取舍。
4. 在 DGX Spark 上的实际实现。
5. 遇到的失败、日志和根因。
6. 修复后的验证结果。
7. 对最终 Demo 或商业价值的影响。
8. 对应 commit、截图、测试和运行证据。

“十日谈”应保留真实工程复杂性，包括统一内存压力、模型并行、Reranker 并发故障、OpenShell 正式发布门与功能跑通的区别、FunASR 进程管理差异等。失败复盘能更有力地证明项目是经过真实工程验证的系统，而不是一次性拼装演示。

## 最终材料建议

| 材料 | 核心内容 |
| --- | --- |
| 项目 README | 产品定位、创新、架构、模型、OpenShell、记忆、高精度与启动验证 |
| Demo 视频 | 一条端到端闭环，展示证据、计算、模型来源和安全运行面 |
| 赛事征文 | 十日开发历程、关键失败、DGX Spark 价值和商业落地思考 |
| 架构图 | 多模型、多智能体、数据服务和 OpenShell 边界 |
| 测试/证据摘要 | health、最小推理、并发、财务、引用、OpenShell 与前后端回归 |
| 已知限制 | 生产发布门、容量余量、依赖服务和未覆盖场景 |

最终表达应坚持一个原则：**让每项创新都能在 Demo 中被看到，让每个结论都能在代码、测试或运行证据中被复核。**
