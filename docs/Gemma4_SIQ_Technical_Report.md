# Gemma4 在 SIQ 中的部署、调用与技术优势报告

生成日期：2026-06-10

本文基于以下本地材料整理：

- Gemma4 vLLM 启动脚本：`/home/maoyd/modles_setup/start_gemma4_26b_a4b_nvfp4_vllm.sh`
- SIQ 项目：`/home/maoyd/siq-research-engine`
- Gemma4 模型快照：`/home/maoyd/hf_cache_new/hub/models--bg-digitalservices--Gemma-4-26B-A4B-it-NVFP4/snapshots/a15dd6f161881b62db952303a5bfb7be118ed15e`
- Hermes profiles：`/home/maoyd/.hermes/profiles/siq_*`

报告目标是说明 SIQ 为什么选择 `Gemma-4-26B-A4B-it-NVFP4`，如何以 vLLM 本地服务部署，项目里哪些代码已经调用或承接 Gemma4，并重点展示 Native Function Calling、多模态处理与端侧/私有化部署能力。

## 1. 结论摘要

SIQ 当前把 Gemma4 定位为“本地、长上下文、可工具调用、多模态友好”的核心推理底座：

- 部署层使用 vLLM OpenAI-compatible server，默认监听 `127.0.0.1:8006`，对外模型名为 `Gemma-4-26B-A4B-it-NVFP4`。
- 模型层采用 26B 级 MoE + NVFP4 量化变体。本地模型元数据表明其架构为 `Gemma4ForConditionalGeneration`，文本侧启用 MoE block，含 `128` 个 experts、每 token 路由 `top_k_experts=8`，并包含视觉塔、图像/视频/音频 special token。
- 工具调用层通过 vLLM 参数 `--enable-auto-tool-choice` 与 `--tool-call-parser gemma4` 打通 OpenAI tools/native function calling。模型 tokenizer 与 chat template 原生包含 `<|tool>`、`<|tool_call>`、`<|tool_response>` 等结构化工具 token。
- 多模态层在项目中形成两条路径：图片附件构造成 OpenAI 多模态 `image_url` 消息；PDF 附件经 MinerU/VLM 解析为 Markdown、content_list、图片等结构化证据，再交给 Agent/Gemma4 推理。
- 私有化部署层通过 `petit_nvfp4` / ModelOpt NVFP4、`bfloat16`、受控显存比例、批处理上限、CUDA/cuDNN 动态库路径和本地 HF cache，实现可在内网 GPU 节点上运行的低成本推理服务。

核心判断：Gemma4 在 SIQ 里的优势不只是“替换一个聊天模型”，而是把长文档理解、工具调用、多模态证据接入、私有化部署和审计式输出连接成同一条可控链路。

## 2. 模型规格与选型理由

### 2.1 本地模型元数据

从模型快照 `config.json` 可确认：

| 维度 | 本地配置观察 |
| --- | --- |
| 架构 | `Gemma4ForConditionalGeneration` |
| 模型类型 | `model_type: gemma4` |
| 文本子模型 | `model_type: gemma4_text` |
| 视觉子模型 | `model_type: gemma4_vision` |
| 数值类型 | `dtype: bfloat16` |
| 文本最大位置 | `max_position_embeddings: 262144` |
| 当前服务上下文 | 启动脚本配置 `MAX_MODEL_LEN=131072` |
| 文本层数 | `num_hidden_layers: 30` |
| 注意力结构 | sliding attention 与 full attention 混合 |
| sliding window | `1024` |
| MoE | `enable_moe_block: true` |
| experts | `num_experts: 128` |
| 每 token 激活专家 | `top_k_experts: 8` |
| 视觉层数 | `vision_config.num_hidden_layers: 27` |
| 每图像 soft tokens | `vision_soft_tokens_per_image: 280` |
| 量化 | `quant_algo: NVFP4`, producer 为 ModelOpt |
| special tokens | image/audio/video/tool/thinking/tool_response 等 token |
| 本地缓存体积 | 模型缓存目录约 `16G` |

这些字段说明该模型并非纯文本 LLM，而是包含多模态处理器、视觉塔与工具调用模板的条件生成模型。SIQ 处理的是 PDF 年报、扫描图、表格、附件、证据链和 Agent 工具，因此该规格与业务形态天然匹配。

### 2.2 为什么选择 26B-A4B-NVFP4

SIQ 的主任务不是短问答，而是财报级别的长上下文阅读、语义增强、证据引用和多 Agent 协作。选择该规格的理由如下：

1. 26B 级别提供更强的财报语义归纳能力

   年报任务需要识别经营驱动、风险、行业地位、现金流质量、主表和附注关系。相比小模型，26B 级别更适合承担“结构化事实之后的高层语义判断”。

2. A4B/MoE 结构提升有效计算密度

   本地配置显示模型启用 MoE，拥有 `128` 个 experts，每 token 路由 `8` 个 experts。MoE 的价值在于总参数容量较高，但推理时只激活部分专家，使模型在能力和推理成本之间取得平衡。对 SIQ 这类企业内部分析平台而言，这比简单堆大 dense 模型更适合长期在线服务。

3. NVFP4 量化降低端侧/私有化部署门槛

   模型配置包含 ModelOpt NVFP4 量化，启动脚本使用 `--quantization petit_nvfp4`。这让 26B 级模型能以更低显存占用运行，为单机 GPU、多模型共存或内网边缘节点部署创造空间。

4. 长上下文适配财报场景

   模型元数据文本最大位置为 `262144`，服务侧配置 `131072`。SIQ 的 Wiki 语义层、报告片段、附件解析结果和多轮历史都可能很长，长上下文能减少过度切片导致的证据丢失。

5. Native Function Calling 适合可审计工具链

   财务分析不能让模型自由编造结果。Gemma4 的工具 token、response schema、chat template 与 vLLM `gemma4` parser 让模型可以用结构化 tool call 请求检索、读取文件、解析 PDF、查询数据库，再把工具结果纳入最终回答。

6. 多模态 token 让图像/PDF流水线更自然

   tokenizer 和 processor 配置包含 image/video/audio token，视觉处理器每图像 `280` soft tokens。即使当前 PDF 主链路采用 MinerU/VLM 先解析为文本，系统仍保留直接图片理解和未来视频/音频扩展的架构空间。

## 3. vLLM 启动脚本参数详解

脚本路径：`/home/maoyd/modles_setup/start_gemma4_26b_a4b_nvfp4_vllm.sh`

### 3.1 当前默认参数

| 参数/环境变量 | 当前默认值 | 技术含义 |
| --- | --- | --- |
| `MODEL_DIR` | Gemma4 本地 HF snapshot | 避免运行时联网下载，适合内网/边缘节点 |
| `SERVED_MODEL_NAME` | `Gemma-4-26B-A4B-it-NVFP4` | OpenAI-compatible API 中的 `model` 名 |
| `HOST` | `127.0.0.1` | 默认仅本机访问，降低暴露面 |
| `PORT` | `8006` | SIQ/Hermes 的 Gemma4 端口 |
| `MAX_MODEL_LEN` | `131072` | 服务侧上下文窗口，上限低于模型元数据以换取稳定性 |
| `GPU_MEMORY_UTILIZATION` | `0.27` | 控制 vLLM 申请显存比例，适合与其他服务共存 |
| `MAX_NUM_BATCHED_TOKENS` | `4096` | 单批次 token 上限，控制 prefill 吞吐和显存 |
| `MAX_NUM_SEQS` | `4` | 并发序列上限，偏向稳定低延迟 |
| `DTYPE` | `bfloat16` | 非量化模块/激活使用 bfloat16 |
| `QUANTIZATION` | `petit_nvfp4` | 加载 ModelOpt/NVFP4 权重 |
| `MOE_BACKEND` | `marlin` | 面向 MoE/低精度内核的加速后端 |
| `ENFORCE_EAGER` | `0` | 默认启用编译/图优化，必要时可切 eager 调试 |
| `ENABLE_AUTO_TOOL_CHOICE` | `1` | 允许 OpenAI tools 中的 `tool_choice: auto` |
| `TOOL_CALL_PARSER` | `gemma4` | 使用 Gemma4 专属工具调用解析器 |
| `ENABLE_THINKING` | `0` | 默认关闭显式 thinking |
| `REASONING_PARSER` | 空，按需设为 `gemma4` | 开启 thinking 时解析 reasoning |
| `DEFAULT_CHAT_TEMPLATE_KWARGS` | 空，thinking 开启后为 `{"enable_thinking": true}` | 透传给模型 chat template |
| `LOG_FILE` | `/home/maoyd/logs/gemma4_26b_a4b_nvfp4_vllm.log` | vLLM stdout/stderr 落盘 |
| `CONDA_ENV` | `/home/maoyd/miniconda3/envs/vllm-gemma4-nvfp4` | 独立推理环境 |
| `VLLM_BIN` | `$CONDA_ENV/bin/vllm` | vLLM 可执行文件 |
| `PYTHON_OVERRIDE_DIR` | 空 | 允许临时注入 Python 覆盖代码 |

脚本最终构造的核心命令等价于：

```bash
/home/maoyd/miniconda3/envs/vllm-gemma4-nvfp4/bin/vllm serve \
  /home/maoyd/hf_cache_new/hub/models--bg-digitalservices--Gemma-4-26B-A4B-it-NVFP4/snapshots/a15dd6f161881b62db952303a5bfb7be118ed15e \
  --served-model-name Gemma-4-26B-A4B-it-NVFP4 \
  --host 127.0.0.1 \
  --port 8006 \
  --max-model-len 131072 \
  --gpu-memory-utilization 0.27 \
  --max-num-batched-tokens 4096 \
  --max-num-seqs 4 \
  --dtype bfloat16 \
  --quantization petit_nvfp4 \
  --trust-remote-code \
  --moe-backend marlin \
  --enable-auto-tool-choice \
  --tool-call-parser gemma4
```

若开启 thinking：

```bash
ENABLE_THINKING=1 bash /home/maoyd/modles_setup/start_gemma4_26b_a4b_nvfp4_vllm.sh
```

脚本会自动补充：

```bash
--reasoning-parser gemma4 \
--default-chat-template-kwargs '{"enable_thinking": true}'
```

### 3.2 CUDA/NVFP4 运行时设计

脚本会设置：

```bash
export HF_HOME=/home/maoyd/hf_cache_new
export VLLM_NVFP4_GEMM_BACKEND=marlin
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
```

并按存在性拼接以下 CUDA 动态库路径：

```text
$CONDA_ENV/lib/python3.12/site-packages/torch/lib
$CONDA_ENV/lib/python3.12/site-packages/nvidia/cu13/lib
$CONDA_ENV/lib/python3.12/site-packages/nvidia/cudnn/lib
/usr/local/lib/ollama/cuda_v12
```

这体现了端侧部署的两个关键思路：

- 运行时自包含：优先使用 Conda 环境中的 torch/CUDA/cuDNN，减少系统级 CUDA 差异造成的加载失败。
- NVFP4 后端明确化：`VLLM_NVFP4_GEMM_BACKEND=marlin` 与 `--quantization petit_nvfp4` 配合，避免低精度权重落到低效或不兼容路径。

### 3.3 参数调优依据

历史 vLLM 日志显示，旧参数曾使用 `gpu_memory_utilization=0.5`、`max_num_batched_tokens=16384`、`max_num_seqs=32`，在启动时因可用显存不足失败：

```text
Free memory on device cuda:0 (52.37/121.69 GiB) on startup is less than desired GPU memory utilization (0.5, 60.85 GiB).
```

当前脚本把显存比例降到 `0.27`，把批处理 token 和并发序列分别收敛到 `4096` 与 `4`。这是面向多服务共存的保守配置：优先保证 Gemma4 能稳定驻留，再根据实际 GPU 空闲情况上调。

调优建议：

| 场景 | 建议调整 |
| --- | --- |
| OOM 或启动失败 | 降低 `GPU_MEMORY_UTILIZATION`、`MAX_NUM_BATCHED_TOKENS`、`MAX_NUM_SEQS` |
| 长文档 prefill 慢 | 在显存足够时上调 `MAX_NUM_BATCHED_TOKENS` |
| 多用户并发 | 上调 `MAX_NUM_SEQS`，同时监控 p95/p99 延迟 |
| 单请求超长上下文 | 保持 `MAX_MODEL_LEN=131072`，优先通过语义层/RAG减少无关上下文 |
| 调试解析器或远端模型代码 | 临时设置 `ENFORCE_EAGER=1` |
| 展示 reasoning 能力 | 设置 `ENABLE_THINKING=1`，并在前端/日志区分 reasoning 与最终答案 |

### 3.4 Gemma4 启动参数逐项深度解释

本节对 `/home/maoyd/modles_setup/start_gemma4_26b_a4b_nvfp4_vllm.sh` 中的启动参数逐项解释。该脚本的核心目标是：在本地 GPU 上以 vLLM OpenAI-compatible server 形式稳定加载 `Gemma-4-26B-A4B-it-NVFP4`，同时启用 NVFP4 量化、长上下文、Native Function Calling 和可选 thinking/reasoning 能力。

#### 3.4.1 脚本安全与执行模式

```bash
#!/usr/bin/env bash
set -euo pipefail
```

| 设置 | 意义 | 为什么这样设置 |
| --- | --- | --- |
| `#!/usr/bin/env bash` | 使用环境中的 bash 解释脚本 | 保证数组、`[[ ]]`、参数展开等 bash 特性可用 |
| `set -e` | 任一命令失败即退出 | 避免在模型目录、日志目录或环境变量异常时继续启动半残服务 |
| `set -u` | 访问未定义变量时报错 | 防止关键变量拼错后被空值吞掉 |
| `set -o pipefail` | 管道中任一步失败都算失败 | 增强启动脚本的可观测性和故障定位能力 |

这组设置对生产部署很重要：Gemma4 启动失败通常会占用显存、端口或产生误导性日志，fail-fast 能减少隐性错误。

#### 3.4.2 模型路径与服务身份参数

```bash
MODEL_DIR="${MODEL_DIR:-/home/maoyd/hf_cache_new/hub/models--bg-digitalservices--Gemma-4-26B-A4B-it-NVFP4/snapshots/a15dd6f161881b62db952303a5bfb7be118ed15e}"
SERVED_MODEL_NAME="${SERVED_MODEL_NAME:-Gemma-4-26B-A4B-it-NVFP4}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8006}"
```

| 参数 | 默认值 | 设置意义 | 调优/注意事项 |
| --- | --- | --- | --- |
| `MODEL_DIR` | 本地 Hugging Face snapshot | 指向模型权重、`config.json`、`tokenizer_config.json`、`processor_config.json` 和 `chat_template.jinja` | 建议固定到具体 snapshot hash，而不是浮动目录，便于回滚和复现实验 |
| `SERVED_MODEL_NAME` | `Gemma-4-26B-A4B-it-NVFP4` | vLLM 对外暴露的模型名，客户端请求中的 `model` 必须匹配 | Hermes profile 和 SIQ 设置页都使用该名称，生产中不要随意改名 |
| `HOST` | `127.0.0.1` | 仅监听本机回环地址 | 默认安全；如需跨机器访问，应放在内网网关、鉴权和限流之后 |
| `PORT` | `8006` | Gemma4 本地服务端口 | SIQ 约定 Qwen3.6 在 `8004`，Gemma4 在 `8006`，MinerU/VLM 分别使用其他端口，避免混淆 |

这组参数决定“模型从哪里加载”和“服务以什么身份对外出现”。其中 `SERVED_MODEL_NAME` 是上层系统稳定依赖的接口契约，`MODEL_DIR` 是底层模型版本契约。

#### 3.4.3 长上下文与调度参数

```bash
MAX_MODEL_LEN="${MAX_MODEL_LEN:-131072}"
GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.27}"
MAX_NUM_BATCHED_TOKENS="${MAX_NUM_BATCHED_TOKENS:-4096}"
MAX_NUM_SEQS="${MAX_NUM_SEQS:-4}"
```

| 参数 | 默认值 | 作用 | 为什么这样设置 |
| --- | --- | --- | --- |
| `MAX_MODEL_LEN` | `131072` | vLLM 允许的最大上下文 token 数 | 模型元数据支持更长位置，但 131072 已能覆盖财报长文档、多轮上下文和证据链，同时降低 KV cache 压力 |
| `GPU_MEMORY_UTILIZATION` | `0.27` | vLLM 启动时目标显存使用比例 | 历史日志显示 `0.5` 会在当前 121.69 GiB GPU 上因剩余显存不足失败；`0.27` 更适合多服务共存 |
| `MAX_NUM_BATCHED_TOKENS` | `4096` | 单批次最大 token 数，影响 prefill 吞吐 | 保守值，降低长上下文和多并发时的峰值显存 |
| `MAX_NUM_SEQS` | `4` | 同时调度的序列数量 | 面向稳定在线服务而非极限压测，避免多个超长请求争抢 KV cache |

这四个参数共同决定“长上下文能力”和“在线服务稳定性”的平衡。它们不是越大越好：

- `MAX_MODEL_LEN` 越大，单请求可容纳上下文越长，但 KV cache 潜在占用越高。
- `GPU_MEMORY_UTILIZATION` 越高，vLLM 可用缓存越多，但越容易与其他模型、数据库、前端服务争抢显存。
- `MAX_NUM_BATCHED_TOKENS` 越高，prefill 吞吐可能越好，但长文档请求的显存尖峰更明显。
- `MAX_NUM_SEQS` 越高，并发能力越强，但每条序列的 KV cache 和调度开销也会上升。

推荐调优策略：

| 部署目标 | 参数方向 |
| --- | --- |
| 稳定演示/参赛现场 | 使用当前默认值，优先保证服务可启动、可复现 |
| 单用户长财报分析 | 保持 `MAX_MODEL_LEN=131072`，`MAX_NUM_SEQS` 可维持较低 |
| 多用户在线问答 | 在显存充足时逐步上调 `MAX_NUM_SEQS`，观察 p95/p99 延迟 |
| 批量语义增强 | 上调 `MAX_NUM_BATCHED_TOKENS` 前先确认显存余量 |
| 与其他大模型共存 | 降低 `GPU_MEMORY_UTILIZATION`，让出显存 |

#### 3.4.4 精度、量化与 MoE 后端参数

```bash
DTYPE="${DTYPE:-bfloat16}"
QUANTIZATION="${QUANTIZATION:-petit_nvfp4}"
MOE_BACKEND="${MOE_BACKEND:-marlin}"
```

| 参数 | 默认值 | 作用 | 技术意义 |
| --- | --- | --- | --- |
| `DTYPE` | `bfloat16` | 指定 vLLM 加载和计算中使用的数据类型 | bfloat16 具备更大的指数范围，适合大模型推理，兼顾稳定性与显存 |
| `QUANTIZATION` | `petit_nvfp4` | 指定 vLLM 使用 NVFP4/ModelOpt 权重量化加载路径 | 将 26B 级模型压到更低显存区间，是本地部署的关键 |
| `MOE_BACKEND` | `marlin` | 指定 MoE/低精度相关内核后端 | 配合 MoE 与低精度权重，优化专家路由和矩阵乘法性能 |

本地模型 `config.json` 中可见：

```json
"quantization_config": {
  "quant_algo": "NVFP4",
  "quant_method": "modelopt"
}
```

因此启动脚本中的 `--quantization petit_nvfp4` 并不是普通压缩选项，而是与模型权重格式匹配的必要加载参数。vLLM 日志也会将其解析到 ModelOpt FP4/NVFP4 路径。

风险与边界：

- NVFP4/ModelOpt 格式在 vLLM 日志中被提示为实验性格式，升级 vLLM 或 ModelOpt 前需要做回归。
- `DTYPE=bfloat16` 不代表全部权重都以 bfloat16 存储；量化权重由 `QUANTIZATION` 决定，bfloat16 更多影响未量化模块、激活和部分计算路径。
- `MOE_BACKEND=marlin` 与具体 vLLM 版本强相关。如果升级后参数名或可选值变化，需要重新验证。

#### 3.4.5 执行模式、工具调用与 reasoning 参数

```bash
ENFORCE_EAGER="${ENFORCE_EAGER:-0}"
ENABLE_AUTO_TOOL_CHOICE="${ENABLE_AUTO_TOOL_CHOICE:-1}"
TOOL_CALL_PARSER="${TOOL_CALL_PARSER:-gemma4}"
ENABLE_THINKING="${ENABLE_THINKING:-0}"
REASONING_PARSER="${REASONING_PARSER:-}"
DEFAULT_CHAT_TEMPLATE_KWARGS="${DEFAULT_CHAT_TEMPLATE_KWARGS:-}"
```

| 参数 | 默认值 | 作用 | 对 SIQ 的意义 |
| --- | --- | --- | --- |
| `ENFORCE_EAGER` | `0` | 是否强制 eager 执行 | 默认允许 vLLM 使用编译、CUDA graph 等优化；排查兼容问题时可设为 `1` |
| `ENABLE_AUTO_TOOL_CHOICE` | `1` | 是否启用自动工具选择 | Hermes/OpenAI tools 的 `tool_choice: "auto"` 必须依赖它 |
| `TOOL_CALL_PARSER` | `gemma4` | 指定工具调用解析器 | 将 Gemma4 原生 `<|tool_call>` 输出解析成 OpenAI-compatible `tool_calls` |
| `ENABLE_THINKING` | `0` | 是否开启 thinking 模板 | 默认关闭，避免 reasoning 泄露到终端用户 |
| `REASONING_PARSER` | 空 | reasoning 解析器 | `ENABLE_THINKING=1` 时脚本自动设为 `gemma4` |
| `DEFAULT_CHAT_TEMPLATE_KWARGS` | 空 | 默认 chat template 参数 | thinking 开启时自动设为 `{"enable_thinking": true}` |

这组参数是展示 Gemma4 创新能力的重点。Native Function Calling 依赖：

```bash
--enable-auto-tool-choice
--tool-call-parser gemma4
```

如果缺少这两个参数，Hermes 工具调用会失败，历史日志中已经出现过：

```text
"auto" tool choice requires --enable-auto-tool-choice and --tool-call-parser to be set
```

thinking 相关参数的逻辑是：

```bash
if [[ "$ENABLE_THINKING" == "1" || "$ENABLE_THINKING" == "true" || "$ENABLE_THINKING" == "TRUE" ]]; then
  if [[ -z "$REASONING_PARSER" ]]; then
    REASONING_PARSER="gemma4"
  fi
  if [[ -z "$DEFAULT_CHAT_TEMPLATE_KWARGS" ]]; then
    DEFAULT_CHAT_TEMPLATE_KWARGS='{"enable_thinking": true}'
  fi
fi
```

这样设计的好处是：

- 默认模式更安全，前端不会意外展示长 reasoning。
- 演示或调试时只需设置 `ENABLE_THINKING=1`。
- parser 与 chat template 参数自动保持一致，减少人工配置错误。

#### 3.4.6 日志、环境与运行时路径参数

```bash
LOG_FILE="${LOG_FILE:-/home/maoyd/logs/gemma4_26b_a4b_nvfp4_vllm.log}"
CONDA_ENV="${CONDA_ENV:-/home/maoyd/miniconda3/envs/vllm-gemma4-nvfp4}"
VLLM_BIN="${VLLM_BIN:-$CONDA_ENV/bin/vllm}"
PYTHON_OVERRIDE_DIR="${PYTHON_OVERRIDE_DIR:-}"
```

| 参数 | 默认值 | 作用 | 运维意义 |
| --- | --- | --- | --- |
| `LOG_FILE` | `/home/maoyd/logs/gemma4_26b_a4b_nvfp4_vllm.log` | vLLM 日志输出文件 | OOM、parser、量化加载、CUDA 后端问题都需要从这里排查 |
| `CONDA_ENV` | Gemma4 专用 Conda 环境 | 隔离 vLLM、Torch、CUDA Python 包 | 避免与 Qwen、MinerU、后端 Python 依赖互相污染 |
| `VLLM_BIN` | `$CONDA_ENV/bin/vllm` | 指定 vLLM 可执行文件 | 可通过覆盖该变量测试不同 vLLM 版本 |
| `PYTHON_OVERRIDE_DIR` | 空 | 注入额外 Python path | 用于临时覆盖 parser、模型适配代码或热修复，生产慎用 |

脚本会先执行：

```bash
mkdir -p "$(dirname "$LOG_FILE")"
```

确保日志目录存在。生产环境建议继续补充：

- 日志轮转，避免单文件无限增长。
- 日志采集，监控 `ERROR`、`OOM`、`BadRequestError`。
- readiness 检查失败时自动读取最近日志片段。

#### 3.4.7 HF cache、NVFP4 GEMM 与 CUDA 动态库参数

```bash
export HF_HOME="${HF_HOME:-/home/maoyd/hf_cache_new}"
export VLLM_NVFP4_GEMM_BACKEND="${VLLM_NVFP4_GEMM_BACKEND:-marlin}"
export PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}"
```

| 环境变量 | 默认值 | 作用 |
| --- | --- | --- |
| `HF_HOME` | `/home/maoyd/hf_cache_new` | 固定 Hugging Face cache，确保模型从本地加载 |
| `VLLM_NVFP4_GEMM_BACKEND` | `marlin` | 指定 NVFP4 GEMM 后端 |
| `PYTORCH_CUDA_ALLOC_CONF` | `expandable_segments:True` | 减少 CUDA 内存碎片，提高长时间服务稳定性 |

动态库路径：

```bash
CUDA_LIB_DIRS=(
  "$CONDA_ENV/lib/python3.12/site-packages/torch/lib"
  "$CONDA_ENV/lib/python3.12/site-packages/nvidia/cu13/lib"
  "$CONDA_ENV/lib/python3.12/site-packages/nvidia/cudnn/lib"
  "/usr/local/lib/ollama/cuda_v12"
)
```

脚本会检查目录是否存在，并拼接到 `LD_LIBRARY_PATH`。这样做的意义是：

- 优先使用 Conda 环境内的 Torch/CUDA/cuDNN 动态库。
- 兼容本机已有的 Ollama CUDA runtime。
- 避免系统默认 CUDA 与 vLLM 编译/安装时依赖的 CUDA 版本不一致。

#### 3.4.8 vLLM 参数数组构造逻辑

脚本使用 bash 数组构造参数：

```bash
VLLM_EXTRA_ARGS=()
if [[ -n "$MOE_BACKEND" ]]; then
  VLLM_EXTRA_ARGS+=(--moe-backend "$MOE_BACKEND")
fi
...
VLLM_CMD=(
  "$VLLM_BIN" serve "$MODEL_DIR"
  --served-model-name "$SERVED_MODEL_NAME"
  ...
  "${VLLM_EXTRA_ARGS[@]}"
)
```

这种写法比拼接字符串更稳健：

- 路径中存在特殊字符时更安全。
- JSON 参数如 `{"enable_thinking": true}` 不容易被 shell 错误拆分。
- 可选参数只在需要时加入，配置更清晰。

最终进程启动：

```bash
if command -v setsid >/dev/null 2>&1; then
  nohup setsid "${VLLM_CMD[@]}" >> "$LOG_FILE" 2>&1 &
else
  nohup "${VLLM_CMD[@]}" >> "$LOG_FILE" 2>&1 &
fi
```

| 启动方式 | 意义 |
| --- | --- |
| `nohup` | 终端退出后服务继续运行 |
| `setsid` | 脱离当前 session，减少被父进程信号影响 |
| `>> "$LOG_FILE" 2>&1` | stdout/stderr 统一写日志 |
| `&` | 后台运行 |

生产建议：当前方式适合手工启动和演示；正式部署建议使用 systemd/supervisor 管理生命周期、重启策略、日志轮转和健康检查。

#### 3.4.9 推荐启动配置组合

| 场景 | 推荐配置 |
| --- | --- |
| 参赛演示稳定版 | 使用脚本默认值 |
| 展示 Native Function Calling | 保持 `ENABLE_AUTO_TOOL_CHOICE=1`、`TOOL_CALL_PARSER=gemma4` |
| 展示 thinking/reasoning | `ENABLE_THINKING=1`，并确保前端区分 reasoning 与最终回答 |
| 显存紧张 | `GPU_MEMORY_UTILIZATION=0.20 MAX_NUM_SEQS=2 MAX_NUM_BATCHED_TOKENS=2048` |
| 吞吐优先 | 在确认显存余量后逐步提高 `MAX_NUM_BATCHED_TOKENS` 和 `MAX_NUM_SEQS` |
| 兼容性调试 | `ENFORCE_EAGER=1`，关闭部分编译优化方便定位问题 |
| 多机访问 | `HOST=0.0.0.0` 或内网 IP，但必须放在网关和鉴权之后 |

#### 3.4.10 启动参数与 Gemma4 能力的对应关系

| Gemma4 能力 | 相关启动参数 | 说明 |
| --- | --- | --- |
| 长上下文财报理解 | `MAX_MODEL_LEN=131072` | 支持长年报片段、多轮对话和证据链输入 |
| 本地低成本部署 | `QUANTIZATION=petit_nvfp4`、`DTYPE=bfloat16` | NVFP4 降显存，bfloat16 保持推理稳定 |
| MoE 高效推理 | `MOE_BACKEND=marlin` | 配合 A4B/MoE 结构优化专家计算 |
| Native Function Calling | `ENABLE_AUTO_TOOL_CHOICE=1`、`TOOL_CALL_PARSER=gemma4` | 让 Gemma4 原生 tool call 进入 OpenAI-compatible `tool_calls` |
| 可选推理过程展示 | `ENABLE_THINKING=1`、`REASONING_PARSER=gemma4` | 用于调试和演示 reasoning，但默认关闭以保护输出边界 |
| 端侧稳定性 | `GPU_MEMORY_UTILIZATION=0.27`、`MAX_NUM_SEQS=4` | 控制显存峰值，适合与 SIQ 其他服务共存 |
| CUDA 兼容性 | `LD_LIBRARY_PATH`、`VLLM_NVFP4_GEMM_BACKEND` | 确保加载正确 GPU 动态库和低精度 GEMM 后端 |

## 4. SIQ 中的 Gemma4 调用链路

### 4.1 总体架构

```text
浏览器 / Vite 前端
  -> FastAPI 聚合后端 /api/*
     -> 设置页 LLM 测试：backend/services/llm_settings.py
        -> http://127.0.0.1:8006/v1/chat/completions
     -> PDF/Wiki workflow：backend/routers/workflow.py
        -> /home/maoyd/wiki/wikiset/llm_semantic_enrichment.py
        -> http://127.0.0.1:8006/v1/chat/completions
     -> Agent 聊天：backend/services/agent_chat_runtime.py
        -> Hermes Runs API
        -> Hermes profile custom:gemma4-local
        -> http://127.0.0.1:8006/v1/chat/completions
     -> 附件解析：backend/routers/chat.py
        -> MinerU / VLM 服务
        -> Markdown/content_list/images
        -> Agent/Gemma4 继续分析
```

### 4.2 设置页和直接连通性测试

文件：`backend/services/llm_settings.py`

SIQ 默认本地 provider 已指向 Gemma4：

```python
LOCAL_GEMMA4_PROVIDER = {
    "enabled": True,
    "providerName": "本地 vLLM / Gemma4",
    "baseUrl": (
        os.environ.get("SIQ_LOCAL_LLM_BASE_URL")
        or os.environ.get("SIQ_GEMMA4_LLM_BASE_URL")
        or "http://127.0.0.1:8006/v1"
    ),
    "model": (
        os.environ.get("SIQ_LOCAL_LLM_MODEL")
        or os.environ.get("SIQ_GEMMA4_LLM_MODEL")
        or "Gemma-4-26B-A4B-it-NVFP4"
    ),
    "temperature": 0.2,
    "maxTokens": 8192,
    "timeoutSeconds": 600,
    "chatTemplateKwargs": {"enable_thinking": False},
}
```

连通性测试使用 OpenAI Chat Completions 格式：

```python
resp = await client.post(
    _endpoint(provider["baseUrl"], "/chat/completions"),
    headers=headers,
    json={
        "model": provider["model"],
        "messages": [
            {"role": "system", "content": "You are a connectivity checker. Reply concisely."},
            {"role": "user", "content": request.message},
        ],
        "temperature": provider["temperature"],
        "max_tokens": min(provider["maxTokens"], 64),
        "stream": False,
        "chat_template_kwargs": provider.get("chatTemplateKwargs") or {},
    },
)
```

这说明 Gemma4 在 SIQ 里不是离线示例，而是设置页可切换、可测试、可持久化的本地 LLM provider。

### 4.3 Wiki 语义增强中的 Gemma4 调用

文件：

- `backend/routers/workflow.py`
- `/home/maoyd/wiki/wikiset/llm_semantic_enrichment.py`

workflow 会把设置页里的本地 provider 注入到语义增强脚本：

```python
env["SIQ_LOCAL_LLM_BASE_URL"] = str(local_provider["baseUrl"])
env["SIQ_LOCAL_LLM_MODEL"] = str(local_provider["model"])
env["SIQ_LLM_SEMANTIC_TIMEOUT"] = str(local_provider["timeoutSeconds"])
env["SIQ_LLM_SEMANTIC_MAX_TOKENS"] = str(local_provider["maxTokens"])
env["SIQ_LLM_SEMANTIC_TEMPERATURE"] = str(local_provider["temperature"])
env["SIQ_LLM_SEMANTIC_CHAT_TEMPLATE_KWARGS"] = json.dumps(
    local_provider["chatTemplateKwargs"],
    ensure_ascii=False,
)
```

语义增强脚本随后调用本地 OpenAI-compatible Gemma4：

```python
body = {
    "model": provider["model"],
    "messages": [
        {"role": "system", "content": system_prompt()},
        {"role": "user", "content": user_prompt(request_payload)},
    ],
    "temperature": provider.get("temperature", 0.2),
    "max_tokens": provider.get("maxTokens", 8192),
    "stream": False,
    "chat_template_kwargs": provider.get("chatTemplateKwargs") or {"enable_thinking": False},
}

req = urllib.request.Request(
    endpoint(provider["baseUrl"], "/chat/completions"),
    data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
    headers=headers,
    method="POST",
)
```

该脚本的设计非常适合 Gemma4：

- 输入不是裸 PDF，而是规则层生成的 segments、facts、claims、evidence。
- prompt 要求模型“只依据输入 JSON”，不得使用外部知识。
- 输出必须是严格 JSON，并绑定 `source_segment_ids` 与 `evidence_ids`。
- 后处理会校验 ID 是否属于 allowed set，不合规则转入 `review_queue`。
- 输出层写入 `semantic/llm/<report_id>/`，不会覆盖规则事实层。

这是一种“规则事实层 + Gemma4 语义层”的组合范式：确定性代码负责数值与证据，Gemma4 负责业务画像、风险、事件和经营驱动归纳。

### 4.4 Hermes Agent 中的 Gemma4

Hermes profile `siq_assistant` 已把主模型设为 Gemma4：

```yaml
model:
  default: Gemma-4-26B-A4B-it-NVFP4
  provider: custom:gemma4-local
  base_url: http://127.0.0.1:8006/v1
  api_mode: openai_chat
  context_length: 131072
  temperature: 0.2
```

同一 profile 内还注册了 custom provider：

```yaml
custom_providers:
- name: Gemma4 Local
  base_url: http://127.0.0.1:8006/v1
  model: Gemma-4-26B-A4B-it-NVFP4
  api_mode: openai_chat
  context_length: 131072
  models:
    Gemma-4-26B-A4B-it-NVFP4:
      context_length: 131072
  temperature: 0.2
```

`siq_legal` 也使用 Gemma4 作为默认模型；`siq_factchecker` 把 Gemma4 作为 fallback provider。这说明 Gemma4 已经进入多 Agent 的生产配置，而不仅是备用实验模型。

### 4.5 后端对工具调用事件的承接

文件：`backend/services/hermes_client.py` 与 `backend/services/agent_chat_runtime.py`

Hermes Runs API 会把工具调用、reasoning 和输出 delta 转为 SSE 事件。SIQ 后端将其统一成：

```python
class StreamEvent:
    type: str  # "delta" | "tool.started" | "tool.completed" | "reasoning" | "done" | ...
    text: str = ""
    tool: str = ""
    preview: str | None = None
    duration: float | None = None
    error: bool = False
```

流式运行时会处理：

- `tool.started`：展示正在执行的工具名和输入预览。
- `tool.completed`：展示工具耗时和错误状态。
- `reasoning`：展示模型推理进度。
- 重复工具调用保护：同一工具连续重复会自动停止。
- 连续工具失败保护：同一工具连续失败会自动停止。

这意味着 Gemma4 的 Native Function Calling 不会直接失控执行，而是进入 SIQ/Hermes 的白名单工具、事件审计、循环保护和前端进度展示链路。

## 5. Native Function Calling 设计与示例

### 5.1 为什么启动脚本必须开启 tool parser

Hermes 旧日志中出现过如下错误：

```text
HTTP 400: "auto" tool choice requires --enable-auto-tool-choice and --tool-call-parser to be set
```

当前脚本通过以下参数修复这一问题：

```bash
--enable-auto-tool-choice \
--tool-call-parser gemma4
```

这两个参数的作用分别是：

- `--enable-auto-tool-choice`：允许 OpenAI 请求中的 `tool_choice: "auto"`。
- `--tool-call-parser gemma4`：让 vLLM 按 Gemma4 tokenizer/template 中的 `<|tool_call>` 格式解析模型输出，并转换为 OpenAI-compatible `tool_calls`。

Gemma4 的 tokenizer 配置中定义了工具调用响应 schema：

```json
{
  "tool_calls": {
    "items": {
      "properties": {
        "function": {
          "properties": {
            "arguments": {
              "type": "object",
              "x-parser": "gemma4-tool-call"
            },
            "name": {"type": "string"}
          },
          "x-regex": "call\\:(?P<name>\\w+)(?P<arguments>\\{.*\\})"
        },
        "type": {"const": "function"}
      }
    },
    "x-regex-iterator": "<\\|tool_call>(.*?)<tool_call\\|>"
  }
}
```

chat template 也会把 OpenAI `tools` 渲染为 Gemma4 原生工具声明：

```jinja
{%- if tools -%}
  {%- for tool in tools %}
    {{- '<|tool>' -}}
    {{- format_function_declaration(tool) | trim -}}
    {{- '<tool|>' -}}
  {%- endfor %}
{%- endif -%}
```

因此该链路是真正的原生函数调用，而不是让模型在文本里“假装输出 JSON”。

### 5.2 OpenAI-compatible 工具调用请求示例

```python
import json
import httpx

GEMMA4_BASE_URL = "http://127.0.0.1:8006/v1"
GEMMA4_MODEL = "Gemma-4-26B-A4B-it-NVFP4"

tools = [
    {
        "type": "function",
        "function": {
            "name": "query_financial_metric",
            "description": "查询公司某个财务指标及其证据来源",
            "parameters": {
                "type": "object",
                "properties": {
                    "company": {"type": "string", "description": "公司简称或股票代码"},
                    "metric": {"type": "string", "description": "财务指标，例如经营现金流净额"},
                    "period": {"type": "string", "description": "报告期，例如 2025 年报"},
                },
                "required": ["company", "metric", "period"],
            },
        },
    }
]

payload = {
    "model": GEMMA4_MODEL,
    "messages": [
        {
            "role": "system",
            "content": "你是 SIQ 财报分析助手。需要数据时优先调用工具，不要编造。",
        },
        {
            "role": "user",
            "content": "请查询赛力斯 2025 年报的经营现金流净额，并给出证据来源。",
        },
    ],
    "tools": tools,
    "tool_choice": "auto",
    "temperature": 0.2,
    "max_tokens": 1024,
    "chat_template_kwargs": {"enable_thinking": False},
}

with httpx.Client(timeout=120) as client:
    resp = client.post(f"{GEMMA4_BASE_URL}/chat/completions", json=payload)
    resp.raise_for_status()
    data = resp.json()

message = data["choices"][0]["message"]
print(json.dumps(message.get("tool_calls", []), ensure_ascii=False, indent=2))
```

模型可能返回：

```json
[
  {
    "type": "function",
    "function": {
      "name": "query_financial_metric",
      "arguments": {
        "company": "赛力斯",
        "metric": "经营现金流净额",
        "period": "2025 年报"
      }
    }
  }
]
```

### 5.3 SIQ 白名单执行器示例

```python
ALLOWED_TOOLS = {
    "query_financial_metric": query_financial_metric,
}

def execute_tool_call(tool_call: dict) -> dict:
    function = tool_call.get("function") or {}
    name = function.get("name")
    arguments = function.get("arguments") or {}
    if name not in ALLOWED_TOOLS:
        return {"error": f"tool not allowed: {name}"}
    if not isinstance(arguments, dict):
        return {"error": "tool arguments must be an object"}
    return ALLOWED_TOOLS[name](**arguments)

def query_financial_metric(company: str, metric: str, period: str) -> dict:
    # 实际实现可接入 Wiki semantic/facts、PostgreSQL pdf2md schema 或 Hermes file/search 工具。
    return {
        "company": company,
        "metric": metric,
        "period": period,
        "value": "示例值",
        "evidence": [
            {
                "evidence_id": "ev_xxx",
                "pdf_page_number": 86,
                "md_line_start": 1204,
                "quote": "示例证据片段",
            }
        ],
    }
```

推荐生产约束：

- 工具名必须白名单。
- 参数必须按 JSON Schema 校验。
- 工具执行必须记录输入、输出、耗时、异常。
- 工具返回应包含可追踪证据 ID，而不是只返回自然语言。
- 对写操作工具引入权限、幂等键和人工确认。
- 对循环调用设置硬停止阈值。SIQ 当前运行时已经包含重复工具调用与连续工具失败保护。

## 6. 多模态处理设计与示例

### 6.1 图片附件到多模态消息

文件：`backend/services/agent_chat_runtime.py`

当用户上传图片时，SIQ 会把图片转成 data URL，并构造 OpenAI 多模态消息：

```python
parts = [{"type": "text", "text": text}]
for item in image_attachments:
    data_url = _image_attachment_data_url(item)
    if data_url:
        parts.append({"type": "image_url", "image_url": {"url": data_url}})

return [{"role": "user", "content": parts}]
```

对应测试 `backend/tests/test_agent_chat_runtime_attachments.py` 已验证：

```python
assert content[1]["type"] == "image_url"
assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
```

这条链路适合财报截图、图表截图、扫描页局部截图等场景。Gemma4 模型元数据中的 `vision_config`、`image_token_id`、`Gemma4ImageProcessor` 和每图像 `280` soft tokens，为后续直接视觉理解提供模型基础。

### 6.2 PDF 附件到 MinerU/VLM 结构化证据

文件：`backend/routers/chat.py`

当附件是 PDF 时，聊天路由会调用 MinerU 解析任务：

```python
data = {
    "backend": "hybrid-http-client",
    "parse_method": "auto",
    "formula_enable": "true",
    "table_enable": "true",
    "server_url": VLM_API_BASE,
    "return_md": "true",
    "return_middle_json": "true",
    "return_model_output": "true",
    "return_content_list": "true",
    "return_images": "true",
    "response_format_zip": "false",
    "return_original_file": "false",
    "lang_list": "ch",
}
```

解析完成后保存：

- `result.md`
- `middle.json`
- `model_output.json`
- `content_list.json`
- `images/`
- `metadata.json`

随后 `agent_chat_runtime._document_attachment_context()` 会把这些信息注入对话上下文：

```text
- MinerU 直连解析任务: <task_id>
- 状态接口: <MINERU_API_BASE>/tasks/<task_id>
- 结果接口: <MINERU_API_BASE>/tasks/<task_id>/result
- 独立解析目录: ...
- MinerU Markdown: .../result.md
- MinerU content_list: .../content_list.json
```

这条链路将“PDF 多模态输入”转化为“可引用、可回放、可审计的结构化证据”。Gemma4 负责后续推理和总结，MinerU/VLM 负责 PDF 版面、OCR、表格和图片抽取。

注意：当前 `VLM_API_BASE` 默认是 `http://127.0.0.1:8002`，用于 MinerU 的视觉后端；Gemma4 本地 LLM 默认是 `http://127.0.0.1:8006/v1`。技术文档中应避免把二者混成同一个服务。

### 6.3 直接多模态调用示例

在 vLLM/Gemma4 multimodal 路径可用时，可直接请求：

```python
import base64
import httpx

image_bytes = open("/home/maoyd/siq-research-engine/backend/data/chat_uploads/smoke_sample.png", "rb").read()
image_url = "data:image/png;base64," + base64.b64encode(image_bytes).decode("ascii")

payload = {
    "model": "Gemma-4-26B-A4B-it-NVFP4",
    "messages": [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "请识别图片中的财务图表信息，并提取关键指标。"},
                {"type": "image_url", "image_url": {"url": image_url}},
            ],
        }
    ],
    "temperature": 0.2,
    "max_tokens": 1024,
    "stream": False,
}

resp = httpx.post("http://127.0.0.1:8006/v1/chat/completions", json=payload, timeout=120)
resp.raise_for_status()
print(resp.json()["choices"][0]["message"]["content"])
```

多模态能力在 SIQ 的典型应用包括：

- 年报截图或公告图片中的表格识别。
- 图表趋势说明与异常点解释。
- PDF 页面的版面理解。
- 管理层讨论与图文混排章节的摘要。
- 通过工具调用触发 OCR/表格解析，再让模型对结构化结果进行审计式回答。

## 7. 端侧/私有化部署架构

### 7.1 部署命令

```bash
bash /home/maoyd/modles_setup/start_gemma4_26b_a4b_nvfp4_vllm.sh
```

查看日志：

```bash
sed -n '1,220p' /home/maoyd/logs/gemma4_26b_a4b_nvfp4_vllm.log
```

连通性检查：

```bash
curl -sS http://127.0.0.1:8006/v1/models
```

文本请求：

```bash
curl -sS http://127.0.0.1:8006/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "Gemma-4-26B-A4B-it-NVFP4",
    "messages": [
      {"role": "system", "content": "你是 SIQ 本地模型连通性检查器。"},
      {"role": "user", "content": "请只回复 OK"}
    ],
    "max_tokens": 32,
    "temperature": 0.2,
    "stream": false
  }'
```

### 7.2 端侧部署价值

| 目标 | Gemma4 + vLLM 方案 |
| --- | --- |
| 数据安全 | 年报、内部分析、用户提问不出内网 |
| 低延迟 | 本机或同机房请求，无公网 API 往返 |
| 成本可控 | NVFP4 量化降低单位推理成本 |
| 可运维 | vLLM 日志、端口、模型名、显存比例均可配置 |
| 可回滚 | `MODEL_DIR` 指向具体 snapshot，支持版本冻结 |
| 可扩展 | OpenAI-compatible API 可被 Hermes、设置页、脚本和未来服务复用 |

### 7.3 生产部署建议

1. 使用 systemd/supervisor 托管启动脚本

   脚本目前使用 `nohup setsid ... &` 后台启动。生产环境建议用 systemd 或 supervisor 管理进程、重启策略和日志轮转。

2. 加健康检查

   对 `http://127.0.0.1:8006/v1/models` 和一个小型 `/chat/completions` 请求做 readiness 检查。

3. 用网关隔离访问

   当前默认绑定 `127.0.0.1` 是正确的。若跨机器访问，建议绑定内网 IP，并通过 API 网关、鉴权、限流和审计暴露。

4. 按业务分层调参

   - 设置页测试：低 `max_tokens`、短 timeout。
   - 语义增强：`maxTokens=8192`、较长 timeout。
   - Agent 聊天：需要 SSE、工具调用和循环保护。
   - 批量报告生成：应限制并发，避免拖垮在线问答。

5. 监控指标

   - GPU 显存、GPU 利用率、KV cache 使用。
   - 请求吞吐、p50/p95/p99 延迟。
   - tool call 成功率、失败率、循环停止次数。
   - PDF 解析任务耗时和失败率。

## 8. 架构创新点

### 8.1 规则事实层 + Gemma4 语义层

SIQ 没有把财务数值抽取完全交给 LLM。规则层先抽取 facts、claims、segments、evidence，Gemma4 再做高层语义增强。这种设计降低幻觉风险，并保留模型的归纳能力。

创新点在于：Gemma4 的输出被强制绑定已有 `segment_id` 和 `evidence_id`，不合规内容转入人工复核队列。模型负责“理解”，系统负责“约束”。

### 8.2 原生工具调用驱动可审计 Agent

Gemma4 的工具 token 和 vLLM parser 让工具调用以结构化事件进入 Hermes/SIQ，而不是自然语言猜测。后端可记录每次 `tool.started`、`tool.completed`、reasoning、delta 和 final answer，形成可回放的 Agent 执行轨迹。

### 8.3 多模态证据流水线

PDF、图片、表格、Markdown、content_list 和 bbox/page 信息被统一接入上下文。Gemma4 不需要盲读原始文件，而是在必要时读取解析产物、调用工具或消费图片输入。

### 8.4 长上下文与证据索引结合

Gemma4 服务侧提供 `131072` token 上下文，但系统并不无脑塞全文，而是通过 Wiki 语义层、证据 ID、文档链接和附件上下文选择高价值材料。长上下文作为上限能力，证据索引作为精度保障。

### 8.5 NVFP4 面向本地推理的工程优势

ModelOpt NVFP4 + vLLM + Marlin 后端使 26B 级模型进入单机/边缘节点部署区间。对企业而言，这意味着模型能力、隐私合规和推理成本可以同时优化。

## 9. 风险与注意事项

| 风险 | 说明 | 建议 |
| --- | --- | --- |
| `--trust-remote-code` | 允许加载模型自定义代码，有供应链风险 | 只使用可信 snapshot；固定 hash；限制运行权限 |
| ModelOpt NVFP4 实验性 | vLLM 日志提示格式可能变化 | 固定 vLLM/ModelOpt 版本；升级前做回归 |
| 超长上下文成本 | 131072 上下文会增加 prefill 和 KV cache 压力 | 优先 RAG/语义层筛选，不把整库直接塞入 |
| 工具调用失控 | 模型可能重复调用同一工具或调用失败循环 | 保留现有循环保护；工具白名单；设置超时 |
| VLM 与 LLM 服务混淆 | MinerU 的 `VLM_API_BASE` 默认不是 Gemma4 LLM | 文档和配置中明确 8002/8003/8006 分工 |
| 多模态直连兼容 | OpenAI `image_url` 到 vLLM/Gemma4 的兼容依赖运行时版本 | 用 smoke image 测试；必要时走 MinerU/VLM 预处理 |
| thinking 泄露 | reasoning 适合调试，但不一定适合直接展示给终端用户 | 默认关闭；按场景脱敏/摘要展示 |

## 10. 推荐展示话术

可以用下面这段作为项目汇报中的核心技术亮点：

SIQ 选择 `Gemma-4-26B-A4B-it-NVFP4`，不是单纯因为模型规模，而是因为它同时满足企业级财报智能分析的四个关键条件：第一，26B 级 MoE 结构提供足够的长文档理解与复杂语义归纳能力；第二，NVFP4 量化让模型可以在本地 GPU 节点稳定部署，兼顾隐私、延迟与成本；第三，Gemma4 原生工具调用模板与 vLLM `gemma4` parser 能把模型意图转化为可审计的 function call，接入数据库、文件、PDF 解析和证据检索工具；第四，模型元数据具备视觉、图像、视频、音频 token 和 processor，为财报 PDF、截图、表格和多媒体附件分析提供统一的多模态接口。

在架构上，SIQ 采用“规则事实层 + Gemma4 语义层 + Hermes 工具执行层”的组合。规则层负责数值抽取、勾稽和证据定位；Gemma4 负责业务画像、风险、事项和经营驱动归纳；Hermes/后端负责工具调用、流式事件、循环保护和审计记录。这让系统既能发挥 Gemma4 的推理与多模态能力，又能满足金融场景对准确性、可追溯和本地化部署的要求。

## 11. 关键文件索引

| 文件 | 作用 |
| --- | --- |
| `/home/maoyd/modles_setup/start_gemma4_26b_a4b_nvfp4_vllm.sh` | Gemma4 vLLM 启动脚本 |
| `backend/services/llm_settings.py` | 本地 Gemma4 provider 默认配置与连通性测试 |
| `backend/routers/settings.py` | 设置页 LLM API |
| `backend/routers/workflow.py` | 语义增强 workflow，向外部脚本传递本地 LLM 配置 |
| `/home/maoyd/wiki/wikiset/llm_semantic_enrichment.py` | 调用 Gemma4 `/chat/completions` 生成 LLM 语义层 |
| `backend/routers/chat.py` | 图片/PDF 附件上传、MinerU 解析任务提交 |
| `backend/services/agent_chat_runtime.py` | 多模态消息构造、Hermes run 输入、工具事件/循环保护 |
| `backend/services/hermes_client.py` | Hermes Runs API 客户端，承接 tool/reasoning/delta 事件 |
| `/home/maoyd/.hermes/profiles/siq_assistant/config.yaml` | Gemma4 作为主 Agent 模型 |
| `/home/maoyd/.hermes/profiles/siq_legal/config.yaml` | Gemma4 作为法务 Agent 主模型 |
| `/home/maoyd/.hermes/profiles/siq_factchecker/config.yaml` | Gemma4 作为事实核查 fallback provider |
| 模型 `config.json` | Gemma4 架构、MoE、vision、NVFP4 量化配置 |
| 模型 `tokenizer_config.json` | 工具调用 response schema 与 special tokens |
| 模型 `chat_template.jinja` | tools、tool_calls、tool_responses、thinking、多模态 token 模板 |

## 12. 验证结果

- 已执行 `bash -n /home/maoyd/modles_setup/start_gemma4_26b_a4b_nvfp4_vllm.sh`，脚本语法检查通过。
- 已核对当前启动脚本默认值、vLLM 历史日志、SIQ 后端设置、workflow、Hermes profile、模型 `config.json`、`tokenizer_config.json`、`processor_config.json` 与 `chat_template.jinja`。
- 未在本次报告生成过程中重启 Gemma4 服务，也未修改生产配置。
