# SIQ 本地模型服务脚本

## 目录职责

`infra/model-services` 保存 SIQ 依赖的本地模型服务启动脚本和 systemd user units。这里维护的是“如何把模型服务拉起来”的工程脚本，而不是模型权重、缓存或运行日志。

## 产品归属与 NVIDIA 技术栈

本目录支撑 SIQ 的私有化 AI 基础设施，重点服务二级市场分析、一级市场投委会智能体和应用中心解析/会议/检索能力。

| 能力 | 对接产品面 | 价值 |
| --- | --- | --- |
| vLLM / OpenAI-compatible 文本模型 | 二级市场、一级市场 | 支撑 Hermes 分析、核查、法务、风控、主席裁决和工具调用 |
| Nemotron 3 Nano Omni / Qwen / Gemma 等本地模型 | 二级市场、一级市场、应用中心 | 提供本地 GPU 推理、低延迟私有化和模型可替换性 |
| Embedding / reranker | 应用中心、智能体记忆、法律/知识检索 | 支撑 Milvus 召回、精排、半衰期记忆召回和 profile knowledge |
| MinerU / VLM | 应用中心、二级市场 | 支撑财报和通用文档的版面解析、表格/图片理解 |
| Meeting Speech | 应用中心、一级市场 | 支撑会议转写、说话人、术语、纪要和行动项 |

## 在系统中的位置

```text
本地模型与检索服务
  -> infra/model-services
     -> MinerU / vLLM / embedding / reranker / systemd-user
     -> parser / legal retrieval / vector ingest / Hermes
```

## 核心内容

| 分组 | 目录 | 用途 |
| --- | --- | --- |
| MinerU | `mineru/` | PDF 解析上游 API 与联动启动脚本 |
| Qwen 3.6 | `qwen3.6/` | OpenAI-compatible vLLM 文本模型服务 |
| Nemotron 3 Nano Omni | `nemotron3/` | OpenAI-compatible vLLM 多模态推理与工具调用服务 |
| Qwen VL retrieval | `qwen-vl-retrieval/` | embedding 与 reranker 服务 |
| Gemma4 | `gemma4-26b/` | Gemma4 文本模型启动脚本 |
| systemd user | `systemd-user/` | 用户级服务定义 |

## DGX Spark 当前并行拓扑

当前部署在同一台 NVIDIA DGX Spark（GB10、aarch64、CUDA 13、约 128 GB 统一内存）上并行运行多个独立模型服务。项目目录保存稳定对接入口，机器级 source-of-truth 管理脚本位于 `/home/maoyd/modles_setup/`：

| 服务 | 管理脚本 | 端口 | 资源/运行隔离 |
| --- | --- | ---: | --- |
| Nemotron 3 Nano Omni 本地主模型 | `start_nemotron3_nano_omni_vllm.sh` | `8007` | 固定 vLLM 0.20 ARM64 image、NVFP4、27% GPU memory target、FP8 KV、独立 Docker/cache |
| MinerU2.5-Pro 文档 VLM | `MinerU2.5-Pro-2604-1.2B_up.py` | `8002` / API `8003` | 12% target、独立 Conda + API venv、systemd user 双服务 |
| Qwen3-VL Embedding | `Qwen3-VL-Embedding-2B_up.py` | `8013` | 8% target、BF16 pooling、1024 维 Matryoshka、独立 Docker |
| Qwen3-VL Reranker | `Qwen3-VL-Reranker-2B_up.py` | `8001` | 10% target、8192 context、独立 HTTP wrapper/Docker |
| FunASR Nano | `start_funasr_vllm.sh` | `8899` | 5% target、BF16/eager、VAD/speaker、独立 Conda/PID/log/systemd |

StepFun `step-3.7-flash` 是云端主模型，通过 Hermes custom provider / OpenShell Provider 接入，与本地 Nemotron 构成双主模型。业务侧不依赖某个启动脚本的进程身份，只依赖 OpenAI-compatible、embedding、rerank、ASR 和 MinerU HTTP 合同。

这些 memory target 是调度上限目标而非静态占用。统一内存同时被模型权重、KV cache、CUDA graph、ARM CPU 进程、PostgreSQL cache、Milvus segment 和文件 page cache 使用，容量规划需要把整机视为一个资源系统。默认模型预算约束、量化与独立进程提供可运行基线；峰值并发、超长上下文和大批量 ingestion 仍需限流、队列和压力测试。

### 并行协同而非模型串联

- 文档入库时，MinerU 负责生成结构 artifact；PostgreSQL load plan、Milvus chunk 准备和质量校验可在 task 层并发推进。
- 问答时，精确 SQL、LLM-Wiki 逻辑跳转、Milvus、长期记忆和图片理解可以并发执行；Qwen reranker 只精排 Milvus/记忆等向量候选，LLM-Wiki 不使用 embedding/ranker，其按 ResearchIdentity、主题、对象 ID 和主表/附注关系命中的权威事实直接进入证据优先级组装。
- 语音时，FunASR/meeting-speech 与音频持久化、speaker tracking、Hermes 纪要分离，实时 partial 不阻塞最终高精度窗口。
- StepFun 与 Nemotron 的切换/fallback 保留模型来源；OpenShell 只控制执行安全，不改变业务证据和输出合同。

独立端口、容器/环境和健康检查形成故障隔离，但也引入版本、端口、显存、超时和启动次序协调成本。`start_all.sh`、systemd user units、Docker health、模型 manager 和 API system status 共同提供运维面。

### 2026-07-20 本机核验备注

- Nemotron、MinerU VLM/API、Qwen3-VL Embedding 与 systemd 管理的 FunASR 当前 ready。
- Qwen3-VL Reranker 已修复并发线程同时调用离线 `LLM.score()` 导致 EngineCore 解码线程退出的问题：wrapper 现在以锁保护单引擎调用，并将同批 documents 合并为一次 1:N vLLM score；空/不完整输出改为受控 503。重启后 `/health` 正常、最小排序正确，6 路并发 `/v1/rerank` 全部为 200，当前服务 ready；调用方仍保留 rerank error/degraded 路径。
- FunASR 的独立 manager 仍按自己的 PID 文件判断，而当前真实进程由 `siq-funasr-vllm.service` 托管；PID 文件陈旧时 manager 会误报。修复前以 systemd 状态和 `8899/openapi.json` 为准。
- 当前统一内存与 swap 接近满载。多模型同机并行已经成立，但这也意味着峰值任务需要 admission control，不能同时无界放大 256K 生成、PDF 批处理、向量入库和会议 finalization。

这些是动态运维采样，不替代脚本配置合同；其价值是提醒维护者始终分开检查 process、readiness、最小推理与业务质量。

## 当前最新状态

| 能力 | 对接模块 | 价值 |
| --- | --- | --- |
| MinerU / PDF2MD 上游 | `apps/pdf-parser`、`apps/document-parser` | 支撑财报 PDF 和通用 PDF 的版面解析 |
| vLLM 文本模型 | Hermes gateway、分析/核查/法务 profile | 支撑本地化生成和私有部署，不把研究材料外发 |
| Embedding / reranker | Milvus ingest、智能体记忆、知识检索 | 支撑混合召回、精排和证据检索质量 |
| systemd user units | 本地长期运行 | 让模型服务像基础设施一样可启动、可检查、可重启 |

这部分的商业价值是私有化能力：投研材料通常无法随意发送到外部 SaaS，SIQ 通过本地模型服务把解析、检索、生成和 rerank 留在客户控制的机器或内网里。

## 多模态模型协作图

```text
Chat 图片 -----------------> Nemotron 3 Nano Omni vision ---------> 图片初步分析
PDF/扫描件/图表 -----------> MinerU + VLM ------------------------> layout/table/figure/source map
文本/图片 evidence --------> Qwen3-VL Embedding -> Milvus --------> 多模态候选召回
候选 evidence -------------> Qwen3-VL Reranker -------------------> 精排结果
Chat 短语音 ---------------> FFmpeg -> FunASR 8899 ---------------> 用户问题文本
会议 PCM/录音 -------------> meeting-speech/Paraformer/VAD ------> stable transcript
证据 + 记忆 + transcript --> Hermes + Nemotron/Qwen/Gemma --------> 问答/报告/纪要
```

这里的关键不是用一个 Omni 模型包办全部任务，而是让模型各自承担可测量的职责：解析模型恢复版面，vision 模型解释图片，embedding/reranker 找证据，ASR 模型恢复语音，LLM/Hermes 组织结论。最终事实仍受 evidence、财务规则和回答审计约束。

## 精度、性能与私有化权衡

| 运行决策 | 主要收益 | 必须控制的风险 |
| --- | --- | --- |
| 本地 OpenAI-compatible LLM/VLM | 敏感材料不出内网、API 稳定、模型可替换 | 显存、并发、冷启动、chat template 与模型版本漂移 |
| NVFP4/FP8 量化 | 降低显存与单位推理成本 | 数值推理、OCR/vision 细节和长上下文质量需独立评测 |
| 多模型分工 | 每类任务使用更适合的模型 | 端口、健康、超时、fallback 与 trace 需要统一治理 |
| 本地 embedding/reranker | 低延迟、可控语义检索 | 向量维度、collection schema、模型升级后的全量重建 |
| 本地 ASR/声纹 | 会议与语音数据不外发 | 授权音频评测、CER/DER、声纹 consent、留存与删除 |

模型健康不能只看进程和 `/models`。vision 至少要做真实图片请求，embedding 要验证维度和归一化，reranker 要检查排序，ASR 要在授权音频上评测，生成模型要验证工具调用、结构化输出和长上下文。

## 典型用法

### 启动某个模型服务

```bash
cd /home/maoyd/siq-research-engine/infra/model-services/qwen3.6
bash serve_qwen36_fp8_vllm.sh
```

### 使用 systemd user 管理服务

```bash
systemctl --user daemon-reload
systemctl --user start qwen36-vllm.service
systemctl --user status qwen36-vllm.service --no-pager
```

Hermes 标准 profile 使用 `hermes-gateway-siq@.service`，实例名与
`scripts/hermes/run_gateway.sh` 的 canonical profile 对齐：

```bash
ln -sfn \
  /home/maoyd/siq-research-engine/infra/systemd-user/hermes-gateway-siq@.service \
  /home/maoyd/.config/systemd/user/hermes-gateway-siq@.service
systemctl --user daemon-reload
systemctl --user enable --now hermes-gateway-siq@assistant.service
```

不要让同一端口上的旧 `finsight_*` gateway 与 canonical `siq_*` gateway
同时运行；迁移前先停止并禁用对应旧服务。

## 关键边界或治理规则

- 本目录只存启动脚本和服务定义，不存模型权重、HF 缓存、虚拟环境和日志。
- 脚本中的路径通常是本机默认值，跨机器时应通过环境变量或局部修改覆盖。
- Web、parser、legal retrieval、Milvus ingest 等模块依赖这些服务时，应在各自 README 里说明依赖关系，但不复制这里的全部细节。
- 模型服务是否健康，不应只看进程是否存在，还要看 `/health` 或最小推理是否通过。

## 维护建议

- 新增模型服务时记录端口、模型名、维度、显存要求和对接模块。
- systemd user unit 与 shell 启动脚本尽量保持一致的环境变量语义。
- 对关键依赖服务，建议保留最小 smoke test 或 health check 入口。
- 在 README 中始终提醒：模型服务是上游依赖，不是业务事实层。

## 技术创新与部署价值

本目录把模型视为可替换基础设施，而不是写死在业务代码中的能力。业务服务依赖 OpenAI-compatible、embedding、reranker、MinerU 或 meeting-speech 合同，模型名称、量化方式和硬件编排留在部署层。

| 服务类型 | 典型能力 | 部署价值 |
| --- | --- | --- |
| 文本/工具模型 | Hermes 分析、核查、法务、投委会推理与工具调用 | 研究材料不出内网，模型可按任务分级 |
| 多模态模型 | 页面、表格、图片理解 | 弥补纯文本解析对复杂版面的信息损失 |
| Embedding/Reranker | Milvus 召回与精排 | 形成可控、低延迟的私有检索栈 |
| MinerU | PDF 版面解析 | 将高成本解析能力独立扩缩容 |
| Meeting Speech | ASR、说话人及会议语音处理 | 让会议链路与通用 LLM 解耦 |

主要难点是 GPU 显存、模型并发、冷启动、量化精度、端口和健康状态的一致治理。systemd user 单元与脚本为单机私有化提供可操作基线，生产部署仍需容量评估、监控、故障转移与模型版本冻结。
