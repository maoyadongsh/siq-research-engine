# Gemma4 部署与调用技术文档

本档面向工程与运维人员，详述如何在本项目中部署、启动并调用 Gemma 4（通过 vLLM 服务化），包含启动脚本参数解析、原生函数调用（Native Function Calling）、多模态处理链路、端侧/边缘部署要点与调优建议。

---

## 关键文件

- 启动脚本（vLLM + Gemma4）: [modles_setup/start_gemma4_26b_a4b_nvfp4_vllm.sh](modles_setup/start_gemma4_26b_a4b_nvfp4_vllm.sh#L1)
- 项目主入口: [siq/backend/main.py](siq/backend/main.py#L1)
- 聊天路由与多模态集成: [siq/backend/routers/chat.py](siq/backend/routers/chat.py#L1)

阅读以上文件可帮助理解服务如何被启动、前端如何与模型服务与多模态后端（VLM/MinerU）交互。

---

## 概要与模型选型理由

1. 选型结论：选择 Gemma-4-26B（A4B NVFP4 优化变体）作为核心模型，借助 vLLM 将模型以服务化方式对外提供。

2. 理由：
   - 模型规模与能力平衡：26B 级模型在推理质量上比小型模型有显著提升，同时在成本、显存占用与并发之间具备合理折中。
   - 端侧/加速硬件适配：脚本使用 `petit_nvfp4` 量化与 `bfloat16` 类型，配合 NVFP4（NVIDIA FP4 类后端）可在支持 NVFP4 的 GPU /推理后端上实现更高吞吐与更低显存占用，适合边缘与私有集群部署。
   - 可组合性：通过 vLLM 的 `--enable-auto-tool-choice`、`--tool-call-parser gemma4` 等能力，可以启用模型对外工具调用（Native Function Calling）与思考链路（thinking）扩展，方便实现工具调用、调用 VLM 等多模态能力。

---

## 启动脚本参数详解（逐项）

脚本路径：[modles_setup/start_gemma4_26b_a4b_nvfp4_vllm.sh](modles_setup/start_gemma4_26b_a4b_nvfp4_vllm.sh#L1)

- `MODEL_DIR`：模型快照路径（默认指向本地 HF 缓存下的 Gemma-4 快照）。确保目录包含模型权重与相应 tokenizer/config。
- `SERVED_MODEL_NAME`：对外暴露的模型名，vLLM Serve 会用到该名称作为服务路由标识。
- `HOST` / `PORT`：监听地址与端口，默认 `127.0.0.1:8006`。
- `MAX_MODEL_LEN`：模型最大上下文长度（默认 131072），用于控制最大可处理的 token 长度，适合长上下文场景。
- `GPU_MEMORY_UTILIZATION`：vLLM 尝试使用的 GPU 内存比例（0-1），脚本默认 `0.27`，用于多模型或显存受限时避免 OOM。
- `MAX_NUM_BATCHED_TOKENS` / `MAX_NUM_SEQS`：控制推理并发与批次大小的参数，影响吞吐与延迟。
- `DTYPE`：数值类型，此处为 `bfloat16`，能在保留精度的同时减少显存占用（前提：硬件/运行时支持）。
- `QUANTIZATION`：量化方案 `petit_nvfp4`，适配 NVFP4 加速。用于显著降低显存占用并提升吞吐（以牺牲极少精度换取工程效率）。
- `MOE_BACKEND`：稀疏专家（MoE）后端，默认 `marlin`（如模型使用 MoE 时可配置）。
- `ENFORCE_EAGER`：是否强制 eager 模式（对某些后端/功能需要强制变更执行策略）。
- `ENABLE_AUTO_TOOL_CHOICE`：开启 vLLM 自动工具选择（影响 Native Function Calling 行为）。
- `TOOL_CALL_PARSER` / `REASONING_PARSER` / `DEFAULT_CHAT_TEMPLATE_KWARGS`：用于配置推理时的解析器（解析工具调用、chain-of-thought 等），脚本默认启用 `gemma4` 解析器，并可通过 `DEFAULT_CHAT_TEMPLATE_KWARGS` 传递模板参数（如 enable_thinking）。
- `LOG_FILE`：服务日志文件（脚本会把 vLLM 的 stdout/stderr 重定向到此文件）。
- `CONDA_ENV` / `VLLM_BIN`：指定 Python 环境与 vllm 二进制，确保依赖的 CUDA、torch 与 vllm 版本一致。
- `LD_LIBRARY_PATH` / `CUDA_LIB_DIRS`：脚本按候选路径构建 `LD_LIBRARY_PATH`，确保运行时可以加载 CUDA / cuDNN /后端共享库（对 NVFP4 加速非常关键）。
- `--trust-remote-code`：vLLM 启动时允许加载模型自带的自定义代码，这在某些 HF 模型实现需要自定义组件时是必要的，但会引入信任与安全风险。

---

## 服务化与原生函数调用（Native Function Calling）

1. vLLM 参数说明与意图：
   - `--enable-auto-tool-choice`：允许在对话推理过程中，模型自动选择并触发外部工具（例如调用 VLM / MinerU、检索器、数据库等）。这使得模型能够以“函数调用”形式把复杂动作交由后端执行。
   - `--tool-call-parser gemma4` 与 `--reasoning-parser gemma4`：为 Gemma4 定制的工具调用与推理解析器，保证模型输出的“函数调用”语法可被后端正确解析并执行。
   - `--default-chat-template-kwargs '{"enable_thinking": true}'`：开启思考链（thinking）相关模板参数，使模型在需要时产出中间推理（可用于审计或调试）。

2. 在本项目内的调用链路：
   - 前端/API 调用 → 由 `siq/backend/routers/chat.py` 处理用户消息。
   - 当消息需要模型推理时，后端通过 HTTP 调用 vLLM 服务（listen 在 `HOST:PORT`），并将会话上下文传入。
   - 如果模型发起工具调用（Tool Call），vLLM 会把调用信息以结构化方式返回，后端据此触发相应动作（例如调用 VLM API、MinerU 后端，或读取数据库）。

3. 示范：向 vLLM 发起对话请求（伪代码）

```python
import httpx

payload = {
  "model": "Gemma-4-26B-A4B-it-NVFP4",
  "input": "请帮我分析以下现金流数据...",
  "max_tokens": 512
}

resp = httpx.post("http://127.0.0.1:8006/v1/complete", json=payload, timeout=60)
print(resp.json())
```

（实际端点与请求字段以运行中的 vLLM 版本为准；脚本通过 `VLLM_BIN serve` 启动标准 vLLM 服务。）

---

## 多模态处理（VLM / MinerU 集成）

- 在 `siq/backend/routers/chat.py` 中可见：
  - `MINERU_API_BASE` 与 `VLM_API_BASE` 环境变量用于指定外部多模态/文本抽取后端（默认 `http://127.0.0.1:8003` 和 `http://127.0.0.1:8002`）。这表明聊天路由会把 PDF 上传到 MinerU/VLM，等待解析结果，并将解析出的 markdown/图片/中间产物写入磁盘。
  - 通过 `/_submit_pdf_attachment_to_mineru` 等异步任务，后端会以 HTTP 上传文件并轮询任务状态，最终把结构化文本（md）与图像产物归档供模型使用。

能力亮点：
  - Gemma4 用于语言理解与生成；VLM/MinerU 用于将文档/图像/表格转为模型可消费的结构化文本，构成多模态流水线。
  - 当模型需要额外证据或工具时，Native Function Calling 允许模型发出“调用 VLM 解析 PDF”的意图，后端完成解析并把结果回填到会话上下文中。

---

## 端侧（Edge）部署要点

- 量化与数据类型：使用 `petit_nvfp4` 与 `bfloat16` 在支持的硬件上可大幅降低显存占用与延迟，适合在边缘设备或私有推理节点部署。
- LD_LIBRARY_PATH 与运行时依赖：脚本收集了若干可能的 CUDA 库路径（`torch/lib`、`nvidia/cu13/lib`、`nvidia/cudnn/lib`、`/usr/local/lib/ollama/cuda_v12`），确保在不同环境下加载正确库。
- 资源配置：根据显卡记忆体与并发需求调整 `GPU_MEMORY_UTILIZATION`、`MAX_NUM_BATCHED_TOKENS` 与 `MAX_NUM_SEQS`。

建议：启用监控（GPU 利用率、显存、请求延迟），并在 CI/部署前在代表性硬件上做压力测试以验证吞吐/延迟目标。

---

## 安全、可靠性与运维建议

- `--trust-remote-code`：提升兼容性但带来风险；仅在受信任模型与内部环境中使用。
- 日志：脚本将日志写入 `LOG_FILE`（默认 `/home/maoyd/logs/gemma4_26b_a4b_nvfp4_vllm.log`），应把该路径纳入集群日志收集与告警。
- 凭证与网络：确保 `MINERU_API_BASE` / `VLM_API_BASE` 的访问控制与 TLS，避免未经授权的外部访问。

---

## 调优建议与常见故障排查

- OOM 或显存不足：降低 `GPU_MEMORY_UTILIZATION`，或启用更强的量化（确认精度要求接受范围）；减少 `MAX_NUM_SEQS`。
- 性能低于预期：检查 `LD_LIBRARY_PATH` 是否包含硬件加速库，确认 `VLLM_NVFP4_GEMM_BACKEND` 是否正确配置为 `marlin` 或目标后端。
- 多模态任务超时：调整 `CHAT_PDF_PARSE_SUBMIT_TIMEOUT` / `CHAT_PDF_PARSE_STATUS_TIMEOUT` 与轮询次数，或优化 MinerU 后端并行能力。

---

## 快速上手（步骤）

1. 确认模型文件已位于 `MODEL_DIR` 指定目录。
2. 准备 Conda 环境并确保 `vllm` 可执行：`$CONDA_ENV/bin/vllm`。
3. 设置环境变量（示例）：

```bash
export HF_HOME=/home/maoyd/hf_cache_new
export CONDA_ENV=/home/maoyd/miniconda3/envs/vllm-gemma4-nvfp4
export MINERU_API_URL=http://127.0.0.1:8003
export VLM_API_URL=http://127.0.0.1:8002
```

4. 启动服务：

```bash
bash modles_setup/start_gemma4_26b_a4b_nvfp4_vllm.sh
tail -f /home/maoyd/logs/gemma4_26b_a4b_nvfp4_vllm.log
```

5. 用示例脚本调用 vLLM（或通过项目 API `siq` 的聊天端点触发推理）。

---

## 结语

本方案利用 Gemma4 的大模型能力与 vLLM 的服务化、工具调用支持，结合 VLM/MinerU 的多模态预处理能力，构建了一个可扩展、可审计的企业级部署路径。针对边缘部署与私有化部署，重点靠量化与运行时库适配实现性能/成本的平衡。

如果需要，我可以：
- 生成一份包含命令和 CI 步骤的部署剧本（Ansible / systemd / Docker Compose）。
- 根据目标硬件自动调优 `GPU_MEMORY_UTILIZATION` 与并发参数。
