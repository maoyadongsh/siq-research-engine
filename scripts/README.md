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

```bash
cd /home/maoyd/siq-research-engine
bash -n start_all.sh
find scripts -type f -name '*.sh' -print0 | xargs -0 -r bash -n
```
