# SIQ Research Engine - DGX Spark Hackathon 项目提交材料

> 本文是可公开的参赛说明与提交工作包，不属于项目 README。团队联系方式、
> 成员信息、合影和未发布视频链接通过组委会渠道单独维护，不写入公开仓库。

## 一、提交要求完成度

| 组委会要求 | 当前材料状态 | 提交前动作 |
| --- | --- | --- |
| 完整项目上传至 GitHub、码云等开源平台 | GitHub 远端已存在、代码已推送且匿名访问检查为 HTTP 200 | 提交前再次检查敏感信息、大文件和默认分支 |
| 以 URL 形式提交项目 | GitHub URL 已知 | 在组委会表单填写最终公开 URL，不使用本地路径 |
| 鼓励在 CSDN、知乎等记录历程 | 已有“十日谈”内容框架 | 完成文章发布后填写真实 URL |
| 600 字以上项目说明 | 本文已提供可直接修改的长版说明 | 团队核对产品名称、实测数据和对外口径 |
| 部署说明 | 本文已提供 DGX Spark、本地模型、数据服务和智能体部署流程 | 在干净环境至少复跑一次，记录版本与截图 |
| NVIDIA SDK、NVIDIA 模型及 StepFun 技术栈 | 本文已分类列明 | 不把未使用的 TensorRT、NeMo 等组件写入材料 |
| 作品演示视频 | 已提供视频脚本和镜头清单 | 完成录制、剪辑、字幕、上传并填写 URL |
| 团队合影 | 不进入公开代码仓库 | 由团队确认肖像授权后通过组委会渠道单独提交 |

## 二、提交链接与团队信息

| 项目 | 内容 |
| --- | --- |
| 项目名称 | SIQ Research Engine |
| 代码仓库 | https://github.com/maoyadongsh/siq-research-engine |
| 仓库公开访问检查 | 2026-07-20 已验证仓库页面与根级 LICENSE 无需登录即可访问，提交前仍需复查 |
| 开源许可证 | [Apache License 2.0](../../LICENSE)；另见 [NOTICE](../../NOTICE) 和 [第三方许可清单](../../THIRD_PARTY_LICENSES.md) |
| CSDN / 知乎文章 | 发布 URL 由团队在组委会表单中填写；未发布地址不在公开仓库预填 |
| 演示视频 | 发布 URL 由团队在组委会表单中填写；上传后台信息不进入公开仓库 |
| 团队名称与成员 | 由团队确认后通过组委会渠道提交，公开仓库不保存未确认个人信息 |
| 团队合影 | 取得全员展示授权后通过组委会渠道单独提交 |
| 联系方式 | 仅在组委会要求的受控渠道提供 |

## 三、项目说明正文（600 字以上）

### 作品概述

SIQ Research Engine 是一套面向证券研究、资产管理、一级市场尽调和投委会决策的可审计智能研究生产线。传统大模型投研工具通常以“上传 PDF、切片、向量召回、生成回答”为主，虽然可以快速生成文本，但容易在财务报表、跨页表格、报告期间、金额单位、主表与附注关系等关键场景中出现事实漂移。SIQ 的目标不是让模型写出一篇看起来像研报的文章，而是让每一个重要数字、判断、风险提示、公式和引用都能回到官方披露、PDF 页码、表格单元格、XBRL fact、Markdown 行、数据库记录或会议时间轴，并在证据不足时明确拒绝伪精确回答。

项目形成了二级市场投研分析智能体集群、一级市场投研决策智能体集群和应用中心三大产品域。二级市场覆盖通用问答、深度分析、事实核查、持续跟踪和法律合规；一级市场通过 coordinator、chairman、strategist、sector expert、finance auditor、legal scanner 和 risk controller 等 Hermes profiles 组织 R0-R4 尽调与投委会流程；应用中心则提供官方材料下载、PDF/通用文档解析、会议转写和向量入库能力。所有角色共享同一套证据、身份、权限、质量门和审计语言，但拥有不同职责、禁止行为和输出路径，因此不是简单的多角色 Prompt 包装。

### 核心创新

第一项创新是自研 LLM-Wiki 知识组织体系。LLM-Wiki 不使用 embedding、reranker 或 Milvus 作为内部查询机制，也不依赖固定长度切片。系统先冻结 market、company、filing 和 parse run 身份，再从解析产物中抽取 segment、fact、relation、claim、metric、evidence、note link 和 document link，并通过 `retrieval_index.json` 中的主题别名、优先文件和对象 ID 进行逻辑跳转。查询财务指标时直接进入带期间、单位、币种和证据 ID 的结构化对象；查询科目构成时沿主表到附注、附注标题和附注表格关系跳转；需要复核时再回到 `report.md`、`document_full.json` 和原始 PDF。该方案避免传统 RAG 因切片边界造成的表格截断、单位丢失、相似报告期混用和引用漂移。

第二项创新是财务数据和回答结果的双层校验。数据生产阶段检查三大表完整性、资产负债恒等式、流动与非流动分项、归母与少数股东权益、毛利桥、净利润桥、现金流和期末现金关系；问答阶段使用确定性 `Decimal` 计算器重新计算同比、占比、CAGR、人均和单位换算，并验证公式输入的 evidence ID、ResearchIdentity、期间、币种和单位。对于商誉、应收、存货、固定资产等问题，系统还能执行原值减准备等于净额的勾稽。模型正文声称“已经核验”不能替代后端工具回执，缺证据或计算失败时必须显示 warning、degraded、N/A 或 evidence gap。

第三项创新是自研 NVIDIA OpenShell + Hermes 安全智能体运行面。项目没有引入 NemoClaw，而是直接使用 OpenShell Gateway、Sandbox、Provider、service forwarding、Landlock、seccomp、网络策略和凭据隔离能力，并在其上实现公司级 scope、对话 sandbox generation、资源池、lease、single writer、fencing、API 重启恢复、空闲 TTL 回收、Host fallback 和运行来源回执。`siq_analysis` 分析助手已经通过真实前端链路验证：有效公司上下文可以自动创建只读事实、受控写入的公司沙箱，同一对话切换公司时生成隔离代际，请求完成且写入静默后释放租约。安全边界、业务质量和生产切流被建模为三个独立状态，避免“沙箱能运行”被误报为“生产质量已经达标”。

### DGX Spark 平台价值

SIQ 在一台 NVIDIA DGX Spark 上并行运行多种职责不同的模型，而不是用一个通用大模型包办全部任务。本地主模型 NVIDIA Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4 提供私有推理、长上下文、工具调用和原生图片/音频/视频理解；MinerU2.5-Pro 负责 PDF、扫描件、版面、表格、公式和图片解析；Qwen3-VL Embedding 负责文本、图片、表格、法规和智能体记忆的向量化；Qwen3-VL Reranker 负责 Milvus 和记忆候选精排；FunASR 负责短语音和会议识别；StepFun `Step-3.7 Flash` 作为云端主模型承担复杂生成和推理，并与本地 Nemotron 形成可选择、可回退、可审计的双主模型结构。

DGX Spark 的 GB10、CUDA 13、aarch64 和大容量统一内存使这些模型与 FastAPI、Flask、PostgreSQL、Milvus、MinIO、Redis、LLM-Wiki 和 OpenShell 控制面能够在单机协同。系统通过 NVFP4 权重、FP8 KV cache、独立 GPU memory target、vLLM continuous batching、prefix caching、context/sequence/token 上限、独立端口和健康检查控制资源竞争。GPU 负责生成、视觉编码、embedding、rerank 和 ASR，ARM CPU 同时承担 API、规则校验、文件 hash、数据库、音频处理和安全控制。统一内存也带来真实工程挑战，因此系统区分进程存活、HTTP readiness、最小推理和业务质量四个层次，并对长上下文、批量解析和并发会议进行限流和压力测试。

### 工程完整性与行业价值

项目已经形成 React Web、FastAPI 控制面、Hermes gateway、多市场服务、解析服务、会议服务、模型服务和本地数据服务的完整链路。前端可以展示聊天、分析、核查、跟踪、法务、Deal OS、文档解析、会议和向量入库；后端统一处理鉴权、附件归属、任务状态、SSE、停止、恢复、source token、financial trace、answer audit 和 runtime receipt。模型、向量库和安全运行面都可以替换或重建，但 LLM-Wiki/PostgreSQL 中的权威身份、证据和审计链保持稳定。

对于投研机构，SIQ 可以减少寻找官方材料、人工翻页、重复抽取和手工核数的时间，降低单位误读、跨期混用、引用不支持结论和模型幻觉造成的风险；对合规与管理者，系统提供证据覆盖率、财务校验、运行来源、智能体职责和决策过程回放；对私有化客户，本地 Nemotron、MinerU、Qwen、FunASR、Milvus 与 OpenShell 可以减少敏感材料外发。项目的可复制壁垒不是某一个 Prompt，而是市场 adapter、解析 artifact、证据合同、财务规则、记忆 ACL、岗位智能体和安全运行面共同积累的工程资产。

## 四、技术架构

```text
官方披露 / 尽调材料 / 图片 / 语音
  -> 市场身份与文件 hash
  -> MinerU / PDF / XBRL / 通用文档解析
  -> document_full / source_map / tables / figures / quality
  -> LLM-Wiki 逻辑知识对象 + PostgreSQL 精确事实
  -> 独立 Milvus 多模态/记忆语义索引
  -> Qwen3-VL Reranker 候选精排
  -> Hermes 岗位智能体 + 分层记忆
  -> Nemotron 本地模型 / StepFun 云端主模型
  -> OpenShell 受控工具、Provider 和数据 Broker
  -> financial trace / citation / answer audit
  -> Web 报告、原文回跳、会议纪要和投委会记录
```

### 架构设计原则

1. **证据层与模型层解耦**：模型可替换，证据身份和审计链不随模型变化。
2. **权威层与语义索引解耦**：LLM-Wiki/PostgreSQL 是事实层，Milvus 是可重建索引。
3. **安全与质量解耦**：OpenShell 证明执行边界，财务/引用门证明回答质量。
4. **岗位与权限绑定**：智能体 profile、用户、项目、公司和会话共同决定可见数据与工具。
5. **动态状态显式化**：ready、warning、degraded、fallback 和 NO_GO 不被静默隐藏。

## 五、DGX Spark 本地部署说明

### 1. 基础环境

当前验证环境：

| 项目 | 配置 |
| --- | --- |
| 设备 | NVIDIA DGX Spark / NVIDIA GB10 |
| 架构 | Linux aarch64 |
| CUDA | 13.x |
| 内存 | 约 128 GB 统一内存 |
| 容器 | Docker + NVIDIA Container Toolkit |
| 推理 | vLLM 独立进程 / OpenAI-compatible HTTP |
| 控制面 | Python、FastAPI、Flask、Hermes、OpenShell |

部署前需要准备模型权重、Conda/venv、Docker GPU runtime 和服务所需环境变量。模型权重、API key、数据库密码和 TLS 私钥不进入代码仓库。

### 2. 启动本地模型

项目已将启动脚本归档至 `infra/model-services/`。当前工作机仍可以使用机器级脚本，项目归档用于审计和恢复。

```bash
git clone https://github.com/maoyadongsh/siq-research-engine.git
cd siq-research-engine
export SIQ_PROJECT_ROOT="$(pwd)"

# Nemotron 本地主模型，默认 8007
infra/model-services/nemotron3/start_nemotron3_nano_omni_vllm.sh status

# MinerU VLM 8002 + API 8003
python3 infra/model-services/mineru/MinerU2.5-Pro-2604-1.2B_up.py status

# Qwen3-VL Embedding 8013
python3 infra/model-services/qwen-vl-retrieval/Qwen3-VL-Embedding-2B_up.py status

# Qwen3-VL Reranker 8001
python3 infra/model-services/qwen-vl-retrieval/Qwen3-VL-Reranker-2B_up.py status

# FunASR 8899
infra/model-services/funasr/start_funasr_vllm.sh status
systemctl --user status siq-funasr-vllm.service --no-pager
```

归档一致性检查：

```bash
cd "$SIQ_PROJECT_ROOT/infra/model-services"
sha256sum -c launcher-sources.sha256
```

### 3. 启动本地数据服务

```bash
cd "$SIQ_PROJECT_ROOT"
docker compose -f infra/docker/docker-compose.yml \
  --env-file infra/env/local.env up -d
```

本地并行服务包括 PostgreSQL、Milvus、MinIO、Redis 以及项目 API/解析相关容器。正式演示前应分别检查容器、TCP、HTTP readiness 和最小业务请求。

### 4. 启动项目

```bash
cd "$SIQ_PROJECT_ROOT"
./start_all.sh
```

启动脚本会管理 Web/API/Hermes 相关入口，并按配置启动或复用 SIQ 专用 OpenShell Gateway 与 Broker。OpenShell 正式生产门禁与 Demo 功能跑通是不同状态，演示材料必须准确说明当前运行来源和发布边界。

### 5. StepFun 配置

云端主模型使用 StepFun `step-3.7-flash`，通过 Hermes custom provider、SIQ
设置页或 OpenShell Provider 配置：

| 配置项 | 公开说明 |
| --- | --- |
| API Base | `https://api.stepfun.com/v1` |
| 模型 | `step-3.7-flash` |
| 认证信息 | 仅从安全环境或 OpenShell Provider 注入，不写入文档、命令行或仓库 |

正式演示需要显示实际 model/provider 来源回执，并准备 Nemotron 本地路径作为隐私场景或云端失败时的可解释替代方案。

## 六、模型与推理优化方案

| 优化点 | 实现 | 目标 |
| --- | --- | --- |
| 模型职责拆分 | Nemotron、MinerU、Embedding、Reranker、FunASR 独立服务 | 避免一个模型承担所有精度和延迟目标 |
| 量化 | Nemotron NVFP4，部分本地模型 FP8 | 降低权重占用，为并行模型留出空间 |
| KV 优化 | FP8 KV cache、prefix caching | 降低长上下文成本和重复前缀计算 |
| 并发控制 | `max_num_seqs`、batched tokens、API timeout、候选数量限制 | 防止统一内存和队列失控 |
| Reranker 批处理 | 单次 1:N score，单 EngineCore 并发锁 | 提高吞吐并避免线程并发破坏 EngineCore |
| 文档精度 | MinerU/VLM + source map + table relation | 保留版面与证据定位，不只输出纯文本 |
| 事实精度 | LLM-Wiki 逻辑跳转 + PostgreSQL 精确查询 | 避免向量相似度替代权威事实 |
| 模型路由 | StepFun/Nemotron 双主模型、显式 fallback | 平衡云端质量、本地隐私和可用性 |
| 安全推理 | OpenShell Provider、Broker、network/filesystem policy | Agent 获得能力但不直接获得密钥和宿主权限 |

## 七、技术栈说明

### NVIDIA 与 DGX Spark

| 技术 | 实际用途 |
| --- | --- |
| NVIDIA DGX Spark / GB10 | 单机承载多模型、多数据服务、多智能体与安全控制面 |
| CUDA 13 | 本地 GPU 模型推理运行时 |
| NVIDIA Container Toolkit | Docker `--gpus all` GPU 容器接入 |
| NVIDIA Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4 | 本地多模态主模型、长上下文、工具调用、图片/音频/视频理解 |
| NVIDIA OpenShell `v0.0.83` | Gateway、Sandbox、Provider、Policy、Landlock、seccomp、service forwarding |
| NVFP4 / FP8 KV | 权重和 KV cache 资源优化 |

说明：vLLM 是项目使用的开源推理框架，不应在材料中误写为 NVIDIA SDK；项目没有使用的 TensorRT、NeMo、NIM 等组件不得为了丰富技术栈而加入。

### StepFun 阶跃星辰

| 模型 / 接入 | 实际用途 |
| --- | --- |
| StepFun `Step-3.7 Flash` | 云端主模型、复杂推理、报告生成和工具任务 |
| OpenAI-compatible API | 与 Hermes、SIQ 设置和 Provider 控制面协同 |
| OpenShell Provider | 凭据隔离、请求路径控制和运行审计 |

### 其他模型与框架

| 类别 | 技术 |
| --- | --- |
| 文档解析 | MinerU2.5-Pro-2604-1.2B、pypdf、XBRL/iXBRL adapters |
| 向量模型 | Qwen3-VL-Embedding-2B |
| 精排模型 | Qwen3-VL-Reranker-2B |
| 语音模型 | Fun-ASR-Nano-2512、FSMN VAD、ERes2NetV2 speaker |
| 推理框架 | vLLM、Transformers、PyTorch |
| 智能体 | Hermes profiles、工具合同、分层记忆 |
| 前端 | React、TypeScript、Vite、Tailwind CSS、Radix UI |
| 后端 | FastAPI、Flask、SQLModel、SSE、Uvicorn |
| 数据 | PostgreSQL、Milvus、MinIO、Redis、SQLite、文件型 LLM-Wiki |
| 运维 | Docker Compose、systemd user units、Shell、OpenShell runbooks |

## 八、演示视频方案

### 视频目标

视频必须让评委直接看到项目解决了什么问题、为什么需要 DGX Spark、智能体如何协作，以及最终结果为什么可信。不要把视频做成静态 PPT 朗读或功能菜单快速浏览。

### 建议镜头顺序

| 镜头 | 画面 | 解说重点 |
| --- | --- | --- |
| 1. 问题与产品 | SIQ 主界面、官方年报 | 投研材料复杂、模型答案难追溯 |
| 2. DGX Spark | 系统/模型状态页 | 单机并行 Nemotron、MinerU、Qwen、FunASR 与数据服务 |
| 3. 文档解析 | PDF、表格、图片、source map | MinerU 保留版面与证据坐标 |
| 4. LLM-Wiki | company/report/topic、facts、links | 非传统切片 RAG，逻辑跳转到权威事实 |
| 5. 多模态提问 | 文本、图片或语音输入 | Nemotron 原生视觉、FunASR 和 StepFun 协同 |
| 6. 智能体协作 | analysis、factchecker 或 IC profiles | 岗位合同、共享证据、不同职责 |
| 7. 财务校验 | financial trace、勾稽和重算 | 数字、公式、单位、期间可验证 |
| 8. 证据回跳 | PDF 页码、表格、bbox | 结论可以回到原始材料 |
| 9. OpenShell | runtime origin、scope、generation | Agent 在公司级最小权限沙箱中工作 |
| 10. 价值总结 | 报告、审计、跟踪或投委会结果 | 缩短研究周期、降低事实风险、支持私有化 |

### 录制前检查

- [ ] 模型和数据服务提前完成 readiness 与最小推理。
- [ ] StepFun API、网络和 fallback 路径经过预演。
- [ ] 演示公司、报告和问题固定，不临场随机选择。
- [ ] 图片和音频素材已取得使用授权。
- [ ] source link、financial trace、runtime origin 可正常展示。
- [ ] 浏览器缩放、字体、录屏分辨率和系统通知已处理。
- [ ] 准备完整本地录屏作为现场网络异常的兜底。
- [ ] 视频结尾显示项目名称、GitHub URL 和团队名称。

## 九、技术文章 / “十日谈”发布计划

| 日程 | 主题 | 核心证据 |
| --- | --- | --- |
| Day 1 | 投研问题与 DGX Spark 资源盘点 | 硬件采样、初始架构 |
| Day 2 | 多市场官方披露与身份体系 | adapter、下载和 package |
| Day 3 | LLM-Wiki 非切片知识组织 | retrieval index、facts、links |
| Day 4 | 财务勾稽和确定性计算 | checks、trace、负向测试 |
| Day 5 | Hermes 多智能体和记忆 ACL | profiles、memory scope |
| Day 6 | 多模型 vLLM 并行 | 启动脚本、端口和资源预算 |
| Day 7 | OpenShell + Hermes 安全执行 | policy、sandbox、Provider/Broker |
| Day 8 | 并发与故障修复 | Reranker、lease、recovery 日志 |
| Day 9 | 前后端集成与 Demo 打磨 | E2E、录屏、演示脚本 |
| Day 10 | 商业价值与复盘 | 最终 Demo、指标和限制 |

文章发布后，由团队在组委会表单和受控提交清单中记录 CSDN、知乎或其他平台
的真实公开 URL。公开仓库不保留尚未发布的占位地址。

## 十、团队资料

### 团队信息提交方式

团队名称、成员姓名、分工和联系方式由团队确认后写入组委会表单，不在公开
仓库中预填未确认的个人信息。公开技术材料只描述项目职责和工程贡献类型，不以
虚构姓名或占位符替代正式团队资料。

### 团队合影要求

- 使用清晰原图，避免聊天软件多次压缩。
- 确认所有成员同意用于赛事提交和可能的公开展示。
- 同时保存原图和适合表单上传的压缩版本。
- 文件名建议：`siq-team-dgx-spark-hackathon-original.jpg`、`siq-team-dgx-spark-hackathon-submit.jpg`。
- 不要把包含身份证、电话、工牌敏感信息的照片直接公开上传。

## 十一、最终提交前检查

### 开源项目

- [x] GitHub 仓库无需登录即可访问（2026-07-20 已验证，提交前复查）。
- [x] 根级 Apache License 2.0、NOTICE 和第三方许可清单已添加。
- [x] README、部署说明和启动脚本指向仓库内有效路径。
- [ ] 不包含 API key、密码、TLS 私钥、用户数据或受限业务材料。
- [x] 模型权重的获取方式和许可证边界清楚，仓库不包含大模型权重。
- [x] 默认分支包含当前公开版本，提交 commit 可定位。

### 说明文档

- [x] 已有 600 字以上项目说明草案。
- [x] 已说明作品特点、核心亮点、技术方案和架构设计。
- [x] 已说明 DGX Spark 本地算力部署和模型优化。
- [x] 已列明 NVIDIA、StepFun 和其他关键技术栈。
- [ ] 团队已复核所有数字、状态和对外口径。

### 演示与传播

- [ ] 演示视频已录制、剪辑、加字幕并上传。
- [ ] 视频 URL 在未登录环境可访问。
- [ ] CSDN、知乎或其他技术文章已发布并填写 URL。
- [ ] 团队合影已准备并确认授权。
- [ ] 组委会提交表单已由第二位成员复核。

## 十二、提交风险提示

1. **源码许可证不覆盖全部外部资产**：模型权重、容器镜像、数据和云端服务仍按各自条款管理。
2. **公开访问需要临近提交再次验证**：2026-07-20 的 HTTP 200 检查不能替代截止日前复核。
3. **模型权重不属于项目源码**：部署者需要从官方来源取得权重并遵守对应模型许可证。
4. **动态运行状态不能写成永久能力**：演示前重新检查模型、数据库、OpenShell 和 StepFun。
5. **生产门禁与 Demo 跑通不同**：OpenShell 分析助手链路已验证，但正式生产质量门的状态应按实际检查结果陈述。
6. **视频和合影涉及授权**：音频、图片、公司材料和成员肖像都需要确认可用于赛事展示。

真实文章、视频和团队资料 URL 由团队填写到组委会表单。公开技术材料不得为了
丰富技术栈而声称使用项目中没有实际采用的 NVIDIA SDK 或模型。
