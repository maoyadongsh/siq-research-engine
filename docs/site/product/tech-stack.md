# 技术栈

## 全栈选型一览

| 层 | 选型 | 作用 |
| --- | --- | --- |
| 前端 | React 19、React Router 7、Vite 8、TypeScript 6、Tailwind CSS 4、Radix UI、lucide-react | Web 工作台、二级市场、一级市场、应用中心和系统管理 |
| 控制面 | FastAPI、SQLModel、SSE Starlette、Uvicorn、Redis、JWT / HttpOnly cookie | 鉴权、任务编排、Agent stream、source access、Deal OS、会议、系统状态 |
| 解析面 | Flask、pypdf、MinerU bridge、VLM 上游、table relation、schema extraction | PDF 和通用文档解析、质量产物、表格/页图/source map |
| 市场服务 | FastAPI、Pydantic、market adapters、shared contracts | 官方披露发现、下载、market rules、financial checks、load plan |
| 数据层 | PostgreSQL、SQLite、Milvus、文件系统 Wiki、artifact hash | 权威事实账本、结构化查询、语义索引、文件型证据包 |
| 智能体 | Hermes profiles、`/v1/runs` gateway、Hermes 原生记忆、本地临时记忆、PostgreSQL/Milvus memory、reranker | 多角色分析、核查、跟踪、法务和投委会协作 |
| NVIDIA / GPU 运行面 | NVIDIA OpenShell `v0.0.83`、BYOC 沙箱、Landlock、Provider/Broker、范围自动创建、沙箱代际、vLLM、Nemotron 3 Nano Omni、Gemma NVFP4、Qwen FP8/VL 检索 | 安全执行隔离、本地/私有模型服务、GPU 推理、embedding、reranking |
| 运维 | Docker Compose、systemd user units、shell scripts、OpenShell runbooks、sanitized artifacts | 本地私有化启动、模型服务管理、安全证据和回滚 |

## 基础环境与测试情况

SIQ 面向本地私有化和单机/内网部署设计，推荐从 Linux + Docker + Python + Node 的基础环境起步。

| 项目 | 当前采样 | 说明 |
| --- | --- | --- |
| OS / Kernel | Linux aarch64，kernel `6.17.0-1014-nvidia` | 当前开发机带 NVIDIA kernel 变体，适合本地 GPU / vLLM / OpenShell 验证 |
| Python | `3.13.12` | 项目服务要求 Python `>=3.11`，部分运行环境可使用独立 venv |
| Node / npm | Node `v22.22.2`，npm `10.9.7` | 前端与 iOS meeting capture 合同使用 TypeScript / Vite / Capacitor |
| uv | `0.11.7` | Python 服务推荐使用 uv 管理依赖和测试 |
| Docker | `29.1.3` | Compose、OpenShell BYOC、模型服务和 sandbox 验证依赖 Docker |
| OpenShell | 固定 NVIDIA OpenShell `v0.0.83` | 项目内使用独立 gateway、patched supervisor、BYOC image 和脱敏证据目录 |

## 测试资产规模

| 测试资产 | 当前数量 | 覆盖重点 |
| --- | ---: | --- |
| Python 测试文件 | 469 | API、parser、market services、contracts、db imports、Hermes、OpenShell、model-services |
| TypeScript / Playwright / Node 测试文件 | 115 | Web 路由、工作台交互、meeting 前端协议、E2E smoke、iOS capture 合同 |
| Shell 脚本 | 69 | 启动、运维、OpenShell、Hermes、模型服务和 smoke 入口 |
| OpenShell 专项回归 | 最新状态文档记录 `78 passed` | 运行面选择、资源池绑定、租约、范围自动创建、对话沙箱代际、TTL、恢复、Host 回退 |

## 依赖管理

| 语言 | 工具 | 锁文件位置 |
| --- | --- | --- |
| Python（apps/api、services、packages） | `uv` | `uv.lock` |
| Python（apps/pdf-parser、apps/document-parser） | `pip` + `constraints.txt` | `requirements.txt` |
| TypeScript（apps/web） | `npm` | `package-lock.json` |
| TypeScript（apps/ios-meeting-capture） | `npm` + Capacitor | `package-lock.json` |

## 容器与编排

| 工具 | 用途 |
| --- | --- |
| Docker Compose | 本地一键启动所有服务 |
| Dockerfile | 每个应用和服务都有独立 Dockerfile |
| `infra/docker/docker-compose.yml` | 主编排文件 |
| `infra/env/local.example` | 环境变量样例 |
| systemd user units | Linux 上模型服务和 OpenShell 的运行单元 |

## 模型服务

| 服务 | 路径 | 用途 |
| --- | --- | --- |
| MinerU | `infra/model-services` | PDF 解析上游 |
| vLLM | `infra/model-services` | 本地 GPU 推理 |
| embedding | `infra/model-services` | Milvus 语义索引 |
| reranker | `infra/model-services` | Agent memory 召回重排 |
| Nemotron 3 Nano Omni | `infra/model-services` | NVIDIA 本地模型 |
| Gemma NVFP4 | `infra/model-services` | NVIDIA 量化模型 |
| Qwen FP8/VL | `infra/model-services` | 检索增强模型 |
| meeting-speech | `infra/model-services` | 会议转写 ASR |

## 质量工具

| 工具 | 用途 |
| --- | --- |
| `ruff` | Python 代码检查和格式化 |
| `mypy` | Python 类型检查 |
| `pytest` | Python 测试框架 |
| `ESLint` | TypeScript 代码检查 |
| `Playwright` | 前端 E2E 测试 |
| `actionlint` | GitHub Actions workflow 校验 |
| `shellcheck` | Shell 脚本检查 |
| `hadolint` | Dockerfile 检查 |
| `gitleaks` | Secret 扫描 |
| `trivy` | 文件系统安全扫描 |
| `pip-audit` | Python 依赖漏洞审计 |