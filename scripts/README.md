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
