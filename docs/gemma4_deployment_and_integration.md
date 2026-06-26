# Gemma4 部署与集成技术文档

## 概览

该文档描述如何在本地/端侧部署 Gemma 4（通过 vLLM/本地推理服务），并将其与 SIQ 项目进行集成。内容包含：
- 启动脚本参数逐项说明
- 在 `siq` 项目中的调用示例（包括原生函数调用和多模态示例）
- 设计取舍与模型选型理由
- 部署/监控/性能调优建议

相关文件：
- 启动脚本：[modles_setup/start_gemma4_26b_a4b_nvfp4_vllm.sh](modles_setup/start_gemma4_26b_a4b_nvfp4_vllm.sh)
- 项目后端关键路由：[siq/backend/routers/chat.py](backend/routers/chat.py)

---

## 启动脚本参数详解

脚本位置：[modles_setup/start_gemma4_26b_a4b_nvfp4_vllm.sh](modles_setup/start_gemma4_26b_a4b_nvfp4_vllm.sh)

主要环境变量与参数（来自脚本默认值）：

- `MODEL_DIR`：模型快照路径，指向 Gemma-4 的权重与配置文件。必须保证该路径可访问且包含模型所需文件。
- `SERVED_MODEL_NAME`：服务注册的模型名，便于多模型部署场景下路由请求。
- `HOST` / `PORT`：监听地址与端口（脚本默认 `127.0.0.1:8006`）。生产中建议使用绑定到内部网卡并放置在 API 网关后。
- `MAX_MODEL_LEN`：最大上下文长度（此脚本默认为 `131072`），用于支持超长上下文场景（大文档/多轮对话）。
- `GPU_MEMORY_UTILIZATION`：显存占用目标比例（`0.27`），用于内存分配与动态调度。
- `MAX_NUM_BATCHED_TOKENS` / `MAX_NUM_SEQS`：并发/批处理控制，影响吞吐与延迟。
- `DTYPE`（`bfloat16`）：权重/激活数值类型，bfloat16 在大模型上常用于在精度可接受的前提下节省显存并提高吞吐。
- `QUANTIZATION`（`petit_nvfp4`）：表示使用 NVFP4 类似的低位量化以节约显存并实现端侧或多实例运行。量化带来精度/速度权衡，默认配置已经在脚本中选择为兼顾性能的方案。
- `MOE_BACKEND`（`marlin`）：如果模型使用 Mixture-of-Experts，一些后端（如 marlin）可以提供高效调度。
- 自动化能力相关：`ENABLE_AUTO_TOOL_CHOICE`, `TOOL_CALL_PARSER`, `ENABLE_THINKING`, `REASONING_PARSER`, `DEFAULT_CHAT_TEMPLATE_KWARGS` ——这些参数控制推理时是否启用工具调用解析、推理中间过程（thinking）和可配置的模板参数。

脚本也会设置运行时库搜索路径（`LD_LIBRARY_PATH`）以包含 conda 环境中 PyTorch 与 CUDA 的库，并可通过 `PYTHON_OVERRIDE_DIR` 注入自定义 Python 代码覆盖。

启动命令示例（脚本内部构建的）：

```bash
# 等同于运行脚本，示例展示关键参数
vllm serve /path/to/model_snapshots \
  --served-model-name Gemma-4-26B-A4B-it-NVFP4 \
  --host 127.0.0.1 --port 8006 \
  --max-model-len 131072 --gpu-memory-utilization 0.27 \
  --max-num-batched-tokens 4096 --max-num-seqs 4 \
  --dtype bfloat16 --quantization petit_nvfp4 --trust-remote-code
```

说明：`--trust-remote-code` 在部分自定义模型/插件需要时启用（请评估安全性）。

---

## 在 SIQ 项目中的集成点

关键后端入口：[siq/backend/routers/chat.py](backend/routers/chat.py)（项目已使用 `VLM_API_BASE` 与 `MINERU_API_BASE` 环境变量来访问本地或对接的多模态服务）。

建议映射：将 Gemma4 vLLM 服务的地址设置为 `VLM_API_URL` 或 `VLM_API_BASE`，例如：

```bash
export VLM_API_URL=http://127.0.0.1:8006
# 或在 docker-compose / systemd 环境中设置相应 env
```

调用范例（非项目中现成函数，而是可复制到 `routers/chat.py` 或 agent runtime 的示例）：

```python
import httpx

VLM = "http://127.0.0.1:8006"

async def call_gemma4_chat(prompt: str, functions: list | None = None):
    # 假设 vLLM 或上层适配器提供一个接受 JSON 的对话/生成接口
    payload = {
        "input": prompt,
        "max_tokens": 512,
        "stream": False,
    }
    if functions is not None:
        payload["functions"] = functions

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(f"{VLM}/v1/generate", json=payload)
        resp.raise_for_status()
        return resp.json()

```

注：根据 vLLM 或 Gemma4 运行的具体框架，HTTP 路径可能为 `/v1/generate`、`/v1/outputs`、或自定义 RPC。请以实际暴露的 API 为准。

---

## 原生函数调用（Native Function Calling）示例与设计

原生函数调用是指模型生成一个结构化的“调用器”请求（例如 JSON 描述），由后端捕获并执行具体动作（数据库查询、外部 API、文件读写等），然后把执行结果反馈给模型继续推理或生成最终回答。

示例：将函数列表传递给模型，模型返回 `function_call` 指令。

请求示例（传给模型）：

```json
{
  "input": "请帮我查询公司 A 的最新现金流情况，并把结果保存到数据库。",
  "functions": [
    {
      "name": "query_company_cashflow",
      "description": "查询并返回公司现金流摘要",
      "parameters": {
        "type": "object",
        "properties": {
          "company_name": {"type": "string"}
        },
        "required": ["company_name"]
      }
    }
  ]
}
```

当模型返回如下结构：

```json
{
  "type": "function_call",
  "name": "query_company_cashflow",
  "arguments": {"company_name": "公司 A"}
}
```

后端应当解析 `type == function_call`，在受控、安全的沙箱中调用对应本地函数：

```python
def query_company_cashflow(company_name: str) -> dict:
    # 在项目中，这可以映射到已有的数据层/SQL 查询
    # 示例返回格式
    return {"company_name": company_name, "cashflow": {"operating": 12345, "investing": -234, "financing": 1500}}

# 后端执行流程
result = query_company_cashflow("公司 A")
# 将 result 回传给模型作为上下文继续生成
```

核心要点：
- 仅接受白名单中的函数名与参数以防注入或滥用。
- 所有函数调用均应有权限、速率与资源限制。
- 将函数执行的原始输入/输出写入日志与审计（方便回溯）。

在 `siq` 中，已有的 agent runtime 与 `services/agent_chat_runtime` 是实现该流程的自然位置（可参照 `routers/chat.py` 中如何收集/流式返回聊天结果）。

---

## 多模态（Multimodal）集成

Gemma4 在多模态能力上（文本+图像/文档）可用于：图像理解、文件（PDF）解析、表格识别、截图解释等。

在 `siq` 中已有的模式：
- `routers/chat.py` 使用 `MINERU_API_BASE` 与 `VLM_API_BASE` 将 PDF/图像提交至解析服务（见 `_submit_pdf_attachment_to_mineru`）。

建议的多模态调用流程：

1. 前端上传图片或 PDF（文件以 multipart 或 base64 传输）。
2. 后端保存临时文件并调用 Gemma4 的视觉/多模态接口或通过中间服务（如 `mineru`）做预处理（OCR、表格提取）。
3. 将处理后的文本或图像特征作为上下文传给 Gemma4，或者直接把图像二进制作为二进制字段提交（取决于本地服务的 API）。

示例：把图片以 base64 放入生成请求（如果服务支持）：

```json
{
  "input": "请描述这张图片的要点",
  "image": "data:image/png;base64,iVBORw0KGgoAAAANS...",
  "max_tokens": 256
}
```

或者先调用一个专门的视觉预处理服务，得到 `image_caption` 或 `ocr_text`，再传给 Gemma4：

```python
ocr_text = call_ocr_service(image_path)
reply = await call_gemma4_chat(f"图片文字识别结果：{ocr_text}\n请做摘要并提取关键数值。")
```

---

## 端侧（Edge）部署建议

目标：在受限算力或内网环境部署 Gemma4，实现低延迟与隐私保护。

关键手段：

- 量化（`petit_nvfp4`）：通过 NVFP4/类似低精度格式减小显存占用，允许在单 GPU 或嵌入式 GPU 上运行更大模型。
- DTYPE 使用 `bfloat16`：在支持的硬件上（如 A100/某些 NVIDIA 平台）可兼顾数值稳定性和性能。
- 分片/分布式：对于极大模型，采用模型并行或 MoE 后端（脚本中 `MOE_BACKEND=marlin`）能增强扩展性。
- 本地缓存与热重载：把常用 prompt 模板和 tokenizer 映射常驻内存以减少 cold start。
- 限制并发与批次尺寸：使用 `MAX_NUM_SEQS` 和 `MAX_NUM_BATCHED_TOKENS` 控制延迟/吞吐的平衡。

硬件建议：带有充足显存的推理卡（例如 H100 / A100 / 专用推理卡）或多卡服务器。对于边缘设备，优先使用量化 & 小版本模型。

---

## 模型选型理由（为何选择 Gemma4 特定规格）

1. 大上下文支持：Gemma4 的高上下文长度（脚本中配置到 131072）适合处理整份年报、长篇财务文档和多轮对话的场景，这是 SIQ 的主要需求。
2. 多模态与推理能力：Gemma4 在多模态任务上表现优秀，便于把 OCR/表格/图片与文本分析统一到单一模型链路，简化系统设计。
3. 可控制的推理行为（原生函数调用）：Gemma4 可以通过结构化函数调用与系统工具对接，实现可审计、可回放的外部操作，是安全敏感场景的关键能力。
4. 可量化与端侧部署：通过 NVFP4 等量化方案在边缘或资源受限环境部署大型模型成为可能，兼顾隐私与延迟。

权衡说明：
- 量化与低精度会带来一定精度损失，需要在业务场景上验证关键指标（如财务数值抽取准确率）。
- 超长上下文会增加内存与计算开销，需通过分段/检索增强（RAG）或外部记忆策略优化成本。

---

## 生产化与运维要点

- 日志与审计：详细记录模型输入、函数调用、外部 API 调用与模型输出摘要（脱敏后存储）。
- 健康检查：暴露 `/health` 与模型状态接口，并在服务管理中加入自动重启策略（见 `supervisord.conf` / systemd）。
- 性能监控：采集延迟、吞吐、GPU 利用率、显存分配和批次大小指标。
- 安全策略：对外暴露接口时使用 API 网关、认证、速率限制与白名单函数调用策略。
- 回滚机制：保留可回退的模型快照与配置，支持快速切换 `MODEL_DIR` 以回滚模型版本。

---

## 示例：把 Gemma4 与 `siq` 的 chat 流程对接（步骤）

1. 在部署环境中启动模型服务：运行 `modles_setup/start_gemma4_26b_a4b_nvfp4_vllm.sh`。
2. 确认服务可达并映射环境变量：`export VLM_API_URL=http://127.0.0.1:8006`。
3. 在 `siq` 的运行配置（docker-compose / systemd / env 文件）中加入上述变量。
4. 在 `routers/chat.py` 的 chat runtime 中按需调用生成接口，并实现 `function_call` 的白名单与执行器。

---

## 附录：示例 API 调用与调试命令

检查脚本启动日志：

```bash
tail -n 200 /home/maoyd/logs/gemma4_26b_a4b_nvfp4_vllm.log
```

简单的本地测通（假设服务提供 `/v1/generate`）：

```bash
curl -sS -X POST http://127.0.0.1:8006/v1/generate \
  -H 'Content-Type: application/json' \
  -d '{"input": "测试：请输出一句简单的问候。", "max_tokens": 32}' | jq
```

如果服务采用 streaming 或 websocket 协议，请使用相应的客户端实现流式消费。

---

## 结语与建议

该文档给出在 `siq` 项目中集成 Gemma4 的端到端视图。下一步建议：

- 在测试环境部署脚本并设置 `VLM_API_URL`，用现有 `routers/chat.py` 做端到端功能测试（文本生成、函数调用与 PDF 多模态流程）。
- 进行量化后精度回归测试（关键业务场景），调整 `QUANTIZATION` 与 `DTYPE` 设置以找到最佳折中点。

如果你希望，我可以：
- 生成一个示例的 `docker-compose` 服务段用于运行 Gemma4（vLLM）并与 `siq` 做联调；
- 在 `siq` 中添加示例的 `function_call` 白名单与执行器实现（PR）。
