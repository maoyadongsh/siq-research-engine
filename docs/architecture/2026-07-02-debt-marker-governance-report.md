# TODO/FIXME 治理报告

生成时间：2026-07-03 06:47:09 UTC
扫描根目录：`/home/maoyd/siq-research-engine`

## 摘要

| 分桶 | 数量 |
| --- | ---: |
| 安全 | 0 |
| 运行时 | 0 |
| 架构 | 1 |
| 文档/质量规则 | 3 |
| 合计 | 4 |

默认排除目录/文件：`*.map`, `*.pyc`, `*.pyo`, `*debt-marker-governance-report.md`, `*todo-fixme-governance-report.md`, `.git`, `.pytest_cache`, `.ruff_cache`, `.venv`, `__pycache__`, `artifacts`, `build`, `coverage`, `data`, `dist`, `node_modules`, `playwright-report`, `runtimes`, `scan_todo_fixme.py`, `test-results`, `var`, `venv`

本报告是非阻断 advisory 输出，只用于后续治理分诊，不接入硬 CI。

## 安全

暂无发现。

## 运行时

暂无发现。

## 架构

| 文件 | 行 | 标记 | 内容 |
| --- | ---: | --- | --- |
| `scripts/vector-index/milvus-ingestion/tools/knowledge_ingest/knowledge_ingest_ui.py` | 492 | TODO | # TODO: 图像 embedding 走 DashScope multimodal API |

## 文档/质量规则

| 文件 | 行 | 标记 | 内容 |
| --- | ---: | --- | --- |
| `agents/hermes/profiles/siq_legal/rules/quality_gate.md` | 14 | TODO | - 出现明显的占位符 `{xxx}`、`TODO`、`待补充`。 |
| `agents/hermes/profiles/siq_legal/scripts/validate_legal_opinion.py` | 8 | TODO | - No unresolved placeholder ({{...}}, TODO, 待补充). |
| `agents/hermes/profiles/siq_legal/scripts/validate_legal_opinion.py` | 47 | TODO | PLACEHOLDER_RE = re.compile(r"\{\{[^}]+\}\}\|TODO\|待补充\|<占位>") |
