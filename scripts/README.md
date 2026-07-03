# SIQ 脚本目录

`scripts/` 保存 SIQ Research Engine 的运维、维护、市场 evidence package 构建、Hermes 辅助、评测和向量入库脚本。这里放可重复执行的薄脚本，不放应用源码、运行态数据、模型权重或生成报告。

## 目录职责

| 路径 | 职责 |
| --- | --- |
| `scripts/ops` | 本地健康检查、备份、服务巡检等运维辅助 |
| `scripts/maintenance` | 数据集生成、离线整理、批量维护、评测任务 |
| `scripts/hermes` | Hermes profile 路径解析和网关启动辅助 |
| `scripts/vector-index` | Milvus、知识库入库和向量检索相关工具 |
| `scripts/us-sec` | SEC evidence package、iXBRL/XBRL 提取、行业归类、批量入库 |
| `scripts/hk` | 港股 evidence package 构建和批处理 |
| `scripts/jp` | 日股 EDINET evidence package 构建和批处理 |
| `scripts/kr` | 韩股 DART evidence package 构建和批处理 |
| `scripts/eu` | 欧股 PDF/ESEF evidence package 构建和批处理 |

## 应用启动入口

| 入口 | 用途 |
| --- | --- |
| `start_all.sh` | 一键启动 Web、API、PDF 解析、通用文档解析、公告下载、多市场规则和 Hermes 网关 |
| `apps/api/start.sh` | 启动 API 聚合后端 |
| `apps/pdf-parser/run.sh` | 启动 PDF 解析服务 |
| `apps/document-parser/run.sh` | 启动通用文档解析服务 |
| `apps/web/package.json` | 前端开发、构建和预览命令 |

## 维护原则

- 脚本应可重复执行，并在失败时给出清晰错误。
- 涉及路径时优先读取 `SIQ_*` 环境变量。
- 涉及数据库、模型和外部 API 的脚本不写死密钥。
- 高风险脚本应提供 dry-run、limit 或确认参数。
- 运行输出、日志、缓存和大文件不提交到脚本目录。

## 常用检查

GitHub Actions 的 `CI` workflow 只固定稳定子集：脚本语法检查、API 聚焦测试、Web unit 和 frontend check。PDF parser、document-parser、market-report-finder、market-report-rules 和 contracts 相关扩展覆盖不默认进入 CI，需按变更范围用 `scripts/check_all.sh` 或对应目录测试补跑。

```bash
cd /home/maoyd/siq-research-engine
bash -n start_all.sh
find scripts -type f -name '*.sh' -print0 | xargs -0 -r bash -n
```

Async DB audit advisory 入口：

```bash
cd /home/maoyd/siq-research-engine
scripts/check_async_db_audit.sh
```

该入口默认使用 `apps/api/.venv/bin/python`，可通过 `API_PY` 覆盖；它只输出 `apps/api/scripts/audit_async_sync_session.py --summary` 的 advisory 摘要，既有 finding 不作为失败门禁。需要显式生成其他格式时，可用同一解释器运行 `apps/api/scripts/audit_async_sync_session.py --markdown --summary` 或 `--json --summary` 并自行重定向输出。

债务标记 advisory 扫描入口：

```bash
cd /home/maoyd/siq-research-engine
python3 scripts/scan_todo_fixme.py --markdown docs/architecture/2026-07-02-debt-marker-governance-report.md
```

该脚本按安全、运行时、架构、文档/质量规则分桶输出摘要，并可生成 Markdown 报告；默认跳过依赖、构建、运行态目录和 sourcemap，不作为失败门禁。

红灯 owner 收口门禁：

```bash
cd /home/maoyd/siq-research-engine
scripts/check_owner_migration.sh
```

该脚本聚合 Agent runtime streaming owner / preflight 护栏、PDF parser source/artifact、Web Node unit、`npm run check:frontend` 和提交前检查。其中 `git diff --check` 是失败门禁，`git status --short` 是收尾 review 输出。它是当前架构优化收口门禁，不替代 `scripts/check_all.sh` 的全量基础检查；`scripts/check_all.sh` 对齐 README 的合并前基础门禁，用于更重的全仓验证。
