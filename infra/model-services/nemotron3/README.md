# Nemotron 3 Nano Omni 模型服务

SIQ 使用本机 OpenAI 兼容服务 `http://127.0.0.1:8007/v1`，对外模型名为 `nemotron_3_nano_omni`。该模型服务主要用于会议转写、长上下文理解、多模态实验和与 NVIDIA 模型服务栈相关的本地能力验证。

当前产品口径已进一步明确：Nemotron 同时是 SIQ 的**本地主模型**和 Chat 图片原生理解模型；“多模态实验”只描述其早期用途，不代表当前仍停留在未接入业务的实验状态。会议原始 ASR 由 FunASR/meeting-speech 负责，Nemotron 主要消费文本 transcript 做理解与纪要，并可在其他入口原生接收 image/audio/video。

隔离的 vLLM runtime 与已下载权重不进入仓库。本目录只提供项目侧管理入口：

```bash
infra/model-services/nemotron3/manage_nemotron3_vllm.sh status
infra/model-services/nemotron3/manage_nemotron3_vllm.sh restart
infra/model-services/nemotron3/manage_nemotron3_vllm.sh test
```

如果 runtime 脚本安装在其他路径，可通过 `NEMOTRON3_RUNTIME_SCRIPT` 覆盖默认位置。运维时应优先使用上述脚本，保证健康检查、重启和轻量测试口径一致。

## 在 SIQ 中的角色

`Nemotron 3 Nano Omni` 是 SIQ 本地多模态模型栈的核心执行器之一，而不是系统事实层。当前项目侧对接包含：

| 能力 | 对接点 | 用途 |
| --- | --- | --- |
| 原生图片理解 | `apps/api/services/agent_runtime_attachments.py` | 直接接收 OpenAI vision 格式的 `image_url` data URL，读取文字、数字、表格、图表与关键对象 |
| Hermes 本地模型 | `apps/api/services/hermes_model_control.py` | 作为可选 `nemotron` mode 服务问答、分析、工具调用和长上下文任务 |
| 会议后处理模型候选 | meeting Hermes target pool / model catalog | 对 stable transcript 做修正、滚动纪要、最终纪要和行动项，不直接接触声纹/音频 |
| 本地私有化基线 | `infra/systemd-user/siq-research-engine.service` | 默认配置 `SIQ_IMAGE_MODEL=nemotron_3_nano_omni` 与 `8007/v1` |

机器级启动脚本实际加载 `nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4`，固定 ARM64/CUDA 13 的 vLLM `0.20.0` 镜像。默认使用 `max_model_len=262144`、`gpu_memory_utilization=0.27`、`max_num_seqs=6`、`max_num_batched_tokens=32768`、FP8 KV cache、prefix caching、Nemotron v3 reasoning parser 和自动 tool choice。单 prompt 默认最多各包含 1 个 image/video/audio，多模态限制与允许的本地媒体根由启动脚本控制。

这些参数体现 DGX Spark 上的协同取舍：NVFP4 与 FP8 KV 为 MinerU、Embedding、Reranker 和 FunASR 留出统一内存空间，6 路 sequence/continuous batching 提供交互并发，256K context 支撑长材料；但超长 context、图片/音频和并发序列会共同放大 KV/预处理压力，不能把配置上限等同于任意组合下的 SLA。

API 对图片不是先做外部 OCR 再把文本交给模型，而是将图片二进制编码为 data URL，与用户问题一起发送到 `/chat/completions`。这保留了版面、图表、颜色、空间关系和非文本对象，是模型原生视觉理解链路。

## 图片问答流程

```text
PNG/JPEG/WebP/GIF
  -> API MIME/大小/用户归属/安全路径校验
  -> data:image/...;base64,...
  -> Nemotron /chat/completions
       text prompt + image_url
       temperature=0.1
       max_tokens=1200
       thinking disabled for稳定首轮识别
  -> 图片初步分析
  -> Hermes 结合当前问题、历史附件、Wiki/PostgreSQL 证据回答
  -> 财务数字继续经过 citation/financial guard
```

模型提示要求优先提取可见文字、数字、表格、图表结构和财务/合规相关信息，并对无法确定内容明确标注。图片分析失败会产生显式日志并回退到 Hermes 附件路径，不会把空文本当成功结果。

## 配置合同

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `SIQ_IMAGE_MODEL_ENABLED` | `true` | 开关 Chat 图片原生理解 |
| `SIQ_IMAGE_MODEL_BASE_URL` | `http://127.0.0.1:8007/v1` | OpenAI-compatible 服务根 |
| `SIQ_IMAGE_MODEL` | `nemotron_3_nano_omni`（规范部署） | 模型名；为空时可从 `/models` 发现首个模型 |
| `SIQ_IMAGE_MODEL_TIMEOUT_SECONDS` | `90` | 单次图片推理超时，限制在 5-600 秒 |
| `NEMOTRON3_RUNTIME_SCRIPT` | `/home/maoyd/modles_setup/start_nemotron3_nano_omni_vllm.sh` | 仓库外 runtime 管理脚本 |

Hermes 模型控制中的 Nemotron context length 配置为 `262144`、temperature 为 `0.2`；这是 SIQ 的调用合同，不应被理解为所有硬件、量化和输入组合下都无条件达到同样有效上下文质量。

## 事实与安全边界

- Nemotron 输出是模型解释，不替代 `document_full`、source map、XBRL fact、PostgreSQL row 或原始图片。
- 图片中的财务数字必须与可定位的表格/页图或结构化事实交叉验证后才能进入确定性计算。
- 模型权重、vLLM runtime、HF cache 和本机日志不进入仓库；这里只版本化管理入口和 SIQ 调用合同。
- 本地部署减少材料外发，但仍需 API 鉴权、附件用户归属、路径白名单和日志脱敏。
- 模型升级或量化变化后要重跑真实图片、结构化输出、工具调用、长上下文和财务问答评测，不能只检查 `/health`。

## 运维与验证

```bash
cd /home/maoyd/siq-research-engine
infra/model-services/nemotron3/manage_nemotron3_vllm.sh status
infra/model-services/nemotron3/manage_nemotron3_vllm.sh test
curl -s http://127.0.0.1:8007/v1/models
```

`test` 是轻量连通性检查。正式放量前还需要至少一张含文字/表格的授权测试图片，验证 `/chat/completions` 的 vision payload、中文输出、不确定性标记和超时行为。

## 技术与商业价值

Nemotron 为 SIQ 提供一条完全本地的图片理解与长上下文模型路径，使财务截图、扫描附件、合同图片、图表和会议材料能在内网被智能体直接理解。其商业价值在于敏感材料无需上传第三方视觉 SaaS，同时业务层仍可通过 OpenAI-compatible 接口替换模型。真正的系统壁垒来自它与 parser source map、证据合同、财务守卫、记忆和 Hermes 岗位规则的组合，而不是单个模型名称。
