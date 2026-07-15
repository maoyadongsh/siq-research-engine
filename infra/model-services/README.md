# SIQ 本地模型服务脚本

## 目录职责

`infra/model-services` 保存 SIQ 依赖的本地模型服务启动脚本和 systemd user units。这里维护的是“如何把模型服务拉起来”的工程脚本，而不是模型权重、缓存或运行日志。

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

## 当前最新状态

| 能力 | 对接模块 | 价值 |
| --- | --- | --- |
| MinerU / PDF2MD 上游 | `apps/pdf-parser`、`apps/document-parser` | 支撑财报 PDF 和通用 PDF 的版面解析 |
| vLLM 文本模型 | Hermes gateway、分析/核查/法务 profile | 支撑本地化生成和私有部署，不把研究材料外发 |
| Embedding / reranker | Milvus ingest、智能体记忆、知识检索 | 支撑混合召回、精排和证据检索质量 |
| systemd user units | 本地长期运行 | 让模型服务像基础设施一样可启动、可检查、可重启 |

这部分的商业价值是私有化能力：投研材料通常无法随意发送到外部 SaaS，SIQ 通过本地模型服务把解析、检索、生成和 rerank 留在客户控制的机器或内网里。

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
