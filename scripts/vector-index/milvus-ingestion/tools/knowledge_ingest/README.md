# SIQ 知识库入库系统 V5.0

Gradio Web UI + 异步入库引擎，支持将 PDF / DOCX / MD / TXT 文档导入 Milvus 向量库。

## 快速启动

```bash
# 使用 coordinator venv（已包含所有依赖）
cd tools/knowledge_ingest
unset ALL_PROXY all_proxy  # 避免 socks 代理导致 httpx 报错
/home/maoyd/.openclaw/workspace/ic_master_coordinator_workspace/.venv/bin/python knowledge_ingest_ui.py
```

访问 http://localhost:7860

## 功能特性

### 📦 Collection 管理
- 自动发现所有已有 collection
- 一键新建 / 删除 collection
- 点击表格行自动选中 collection

### ⚙️ 入库参数
- 选择文件格式：PDF / DOCX / Markdown / TXT
- 选择 Embedding 引擎：本地 vLLM 或 DashScope 云端
- 可选重置 collection（清空后重新入库）
- 断点续传（按文件粒度）

### 🔍 检索测试
- 实时测试入库效果
- 支持 Top-K 调节
- 返回文件名 + 片段预览

### 📊 实时监控
- 1 秒定时刷新：总文件数 / 已处理 / 入库向量数 / 失败数
- 实时日志输出

## 与 V4.0 的关键改进

| 改进项 | V4.0 | V5.0 |
|--------|------|------|
| Embedding 接口 | DashScope 云端 | 本地 vLLM + DashScope 可选 |
| 字段名 | `batch_tag` | `project_tag`（与现有 collection 对齐） |
| Metric | `IP`（脚本）vs `L2`（实际） | 统一 `IP`（cosine 近似） |
| HNSW 参数 | M=16, efConstruction=128 | M=32, efConstruction=256 |
| 切块大小 | 800 字符 | 480 字符（法规场景优化） |
| 切块策略 | 固定长度 | 条款感知（优先在"第X条"边界切分） |
| 标题前缀 | 无 | 每块加上法规文件名前缀（提升 title 匹配） |
| metadata 存原文 | 否 | ✅ 存储 text 字段（检索可直接返回片段） |
| project_tag 索引 | 无 | INVERTED 倒排索引（支持按标签过滤） |
| 用户界面 | CLI 交互 | Gradio Web UI |

## 环境要求

- Milvus: localhost:19530
- vLLM: localhost:8000 (Qwen3-VL-Embedding-2B, 1024 维)
- DashScope API Key（可选，环境变量 DASHSCOPE_API_KEY）
- Python 3.12 + coordinator venv
