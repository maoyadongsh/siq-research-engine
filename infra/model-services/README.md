# SIQ 本地模型服务脚本

`infra/model-services` 保存 SIQ Research Engine 使用的本地模型服务启动脚本和 systemd 用户服务样例。这里仅保存轻量脚本和服务定义，不保存模型权重、Conda 环境、Docker 镜像、缓存或运行日志。

## 服务分组

| 分组 | 目录 | 用途 |
| --- | --- | --- |
| MinerU | `mineru/` | PDF 解析上游服务和 PDF 解析服务联动启动脚本 |
| Qwen3.6 35B | `qwen3.6/` | OpenAI-compatible vLLM 文本模型服务 |
| Qwen VL 检索 | `qwen-vl-retrieval/` | Embedding 与 reranker 服务，供法规和知识库检索使用 |
| Gemma4 26B | `gemma4-26b/` | Gemma4 26B A4B NVFP4 vLLM 启动脚本 |
| systemd user units | `systemd-user/` | 用户级服务定义，便于开机或手动管理 |

## 文件清单

```text
mineru/
  MinerU2.5-Pro-2604-1.2B_up.py
  start_pdf2md_services.sh

qwen3.6/
  Qwen3.6-35B-A3B-FP8_vllm_up.py
  serve_qwen36_fp8_vllm.sh
  serve_qwen36_fp8_vllm_newenv.sh
  setup_qwen36_vllm_env.sh

qwen-vl-retrieval/
  Qwen3-VL-Embedding-2B_up.py
  Qwen3-VL-Reranker-2B_up.py

gemma4-26b/
  start_gemma4_26b_a4b_nvfp4_vllm.sh

systemd-user/
  mineru-api.service
  mineru-vllm.service
  qwen36-vllm.service
```

## 与 SIQ 的关系

| SIQ 模块 | 依赖模型服务 |
| --- | --- |
| `apps/pdf-parser` | MinerU API、VLM / vLLM |
| `apps/document-parser` | 复用 `apps/pdf-parser` 的 PDF 能力和上游模型 |
| `siq_legal` | Qwen3-VL Embedding、Qwen3-VL Reranker、Milvus |
| Hermes profiles | MiniMax、Kimi、Qwen3.6、Gemma4 等 provider 或 fallback |
| 向量入库 | Embedding 服务、Milvus |

## 使用方式

先按本机模型路径、显存、Python 环境和端口修改脚本中的变量，再启动对应服务。示例：

```bash
cd /home/maoyd/siq-research-engine/infra/model-services/qwen3.6
bash serve_qwen36_fp8_vllm.sh
```

systemd 用户服务可按需安装到用户服务目录并管理：

```bash
systemctl --user daemon-reload
systemctl --user start qwen36-vllm.service
systemctl --user status qwen36-vllm.service --no-pager
```

## 维护原则

- 模型权重、Hugging Face 缓存、Conda 环境、PID 文件和日志不放入本目录。
- 脚本中的主机路径应视为本机默认值，跨机器运行时通过环境变量或脚本变量覆盖。
- 新增模型服务时记录端口、模型名、API 兼容格式、显存要求和依赖环境。
- 服务被 Web/API/Agent 引用前，应提供健康检查或最小推理 smoke test。
