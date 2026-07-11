# SIQ 脚本目录

## 目录职责

`scripts/` 保存 SIQ 的运维、批处理、市场 evidence package 构建、Hermes 冒烟、向量入库和回归辅助脚本。这里放的是“可重复执行的工程脚本”，而不是应用主源码或运行态数据。

## 在系统中的位置

```text
开发 / 运维 / 批处理任务
  -> scripts/
     -> 服务启动辅助 / 批处理 / 回归 / evidence package / vector ingest / Hermes smoke
```

这些脚本承担的是研究生产线的“工具层”和“工程收口层”职责：当系统需要批量处理、离线维护、健康巡检或验证时，优先落在 `scripts/`，而不是散落在命令历史或临时 notebook 里。

## 核心内容

| 路径 | 作用 |
| --- | --- |
| `scripts/ops` | 健康检查、备份、下载任务辅助和运行维护 |
| `scripts/maintenance` | 数据集生成、评测运行、批量整理 |
| `scripts/hermes` | Hermes gateway 启动、profile 定位与冒烟 |
| `scripts/vector-index` | 向量入库、Milvus 工具和知识库 UI |
| `scripts/us-sec` | 美股 SEC evidence package 与批量处理 |
| `scripts/hk` | 港股 evidence package 与批处理 |
| `scripts/jp` | 日股 package 构建、迁移与批处理 |
| `scripts/kr` | 韩股 package 构建与批处理 |
| `scripts/eu` | 欧股 PDF / ESEF package 构建与批处理 |

## 当前最新状态

| 方向 | 入口 | 说明 |
| --- | --- | --- |
| 全量检查 | `scripts/check_all.sh` | 聚合关键 Python/前端/脚本语法检查，并包含 workflow hygiene、大文件变更、touched Python advisory 和 market contract gate |
| 大文件观察 | `scripts/maintenance/observe_large_files.py` | 输出源码大文件 top list，默认跳过 data/var/artifacts/runtimes，仅作 observe 报告 |
| 变更大文件防护 | `scripts/maintenance/check_large_file_changes.py` | 只检查新增/变更文件，阻止媒体、压缩包、数据库和 `.superpowers` 本地审查产物继续进入源码历史 |
| 非 A 市场重复入库分析 | `db/imports/analyze_market_document_full_duplicates.py` | 只读列出 HK/JP/KR/EU/US 同一 filing 下多 parse_run 的历史重复候选，并输出 cleanup dry-run 命令 |
| 非 A 市场重复入库清理 | `db/imports/cleanup_market_document_full_parse_runs.py` | dry-run 优先，按 parse_run/company/filing/older-than 清理 HK/JP/KR/EU/US 历史重复入库；默认拒绝 A 股/CN |
| 二级市场评测 | `scripts/maintenance/run_market_ingestion_eval.py` | 读取 `datasets/market_ingestion`，输出 market ingestion 指标与 Markdown 报告 |
| SEC package | `scripts/us-sec/*` | 美股 SEC evidence package、XBRL facts、Wiki 迁移和指标规范化 |
| 多市场批处理 | `scripts/hk`、`scripts/jp`、`scripts/kr`、`scripts/eu` | 官方样本下载、parser result ingestion、company Wiki 迁移和 package 构建 |
| Hermes 冒烟 | `scripts/hermes/*` | profile gateway 健康、R1 agent workflow、记忆入库等智能体运维入口 |
| 向量入库 | `scripts/vector-index/*` | Milvus collection 初始化、market evidence chunks、document chunks 和 Gradio KB |

脚本层的商业价值是可重复性：客户演示、回归评测、批量导入和模型/向量运维都应该能从脚本重跑，而不是依赖某次人工操作。

## 多市场 Wiki 迁移

日本市场旧版 `data/wiki/jp_reports/<ticker>/<year>/<report>_doc/` 只作历史兼容来源。需要把旧包迁入公司级 Wiki 主路径时运行：

`PYTHONPATH=scripts/jp:services/market-report-rules/src:scripts/hk python3 scripts/jp/migrate_jp_reports_to_company_wiki.py --force`

日本市场如果只有 PDF parser 产物、没有旧 `jp_reports` package，可直接从已完成的 parser 结果重建公司级 Wiki 包：

`PYTHONPATH=scripts/jp:services/market-report-rules/src:scripts/hk python3 scripts/jp/ingest_jp_parser_results.py --force`

## 典型用法

### 基础脚本健全性检查

```bash
cd /home/maoyd/siq-research-engine
bash -n start_all.sh
find scripts -type f -name '*.sh' -print0 | xargs -0 -r bash -n
```

### Hermes gateway 冒烟

```bash
cd /home/maoyd/siq-research-engine
scripts/hermes/smoke_gateway_health.sh siq_ic_chairman 20
scripts/hermes/smoke_r1_agent_workflow.py --all-r1-profiles
```

### 工程审计与 debt 扫描

```bash
cd /home/maoyd/siq-research-engine
scripts/check_async_db_audit.sh
python3 scripts/scan_todo_fixme.py --markdown docs/architecture/2026-07-02-debt-marker-governance-report.md
python3 scripts/maintenance/observe_large_files.py --limit 20
python3 scripts/maintenance/check_large_file_changes.py
```

### 非 A 市场重复入库清理

```bash
cd /home/maoyd/siq-research-engine
python3 db/imports/analyze_market_document_full_duplicates.py --market HK --json
python3 db/imports/cleanup_market_document_full_parse_runs.py --market HK --filing-id <filing_id>
python3 db/imports/cleanup_market_document_full_parse_runs.py --market HK --filing-id <filing_id> --older-than 2026-07-01T00:00:00+00:00 --apply
```

### 二级市场 MVP 静态评测

```bash
cd /home/maoyd/siq-research-engine
python3 scripts/maintenance/run_market_ingestion_eval.py \
  --case-root datasets/market_ingestion \
  --output artifacts/eval-runs/2026-07-06-secondary-market-mvp/market_ingestion_eval_report.json \
  --markdown artifacts/eval-runs/2026-07-06-secondary-market-mvp/market_ingestion_eval_report.md
```

## 关键边界或治理规则

- `scripts/` 负责批处理和工程操作，不替代 `apps/` 或 `services/` 的主业务入口。
- 涉及数据库、模型、密钥和外部 API 的脚本必须通过环境变量读取敏感信息。
- 高风险脚本应尽量提供 dry-run、limit、seed 或只读模式。
- 脚本输出、临时状态、日志和大文件不应写回源码目录。
- 日本市场等迁移脚本需要特别明确“旧路径兼容”和“新主路径落点”的差异。

## 维护建议

- 新增重复性操作时优先收敛为脚本，并同步补 README。
- 脚本命名尽量反映市场、动作和对象，避免出现含义模糊的工具名。
- 对关键冒烟脚本，优先确保失败时错误清晰、退出码可靠。
- 当脚本成为稳定工作流的一部分时，应补最小测试或至少补校验入口。
