# Operations

## 配置基线

- 模型顺序：Kimi -> MiniMax -> 本地 Qwen3.6。
- `agent.gateway_timeout: 1800`。
- `terminal.timeout: 300`，与 `profile.yaml` 保持一致。
- 禁用 memory/session_search/skills/browser，适合只读分析任务。

## 路径基线

- Wiki 根目录固定为 `/home/maoyd/wiki`。
- 公司目录固定为 `/home/maoyd/wiki/companies/<company_id>`，不得从 `.hermes`、profile home、当前工作目录或相对路径推断。
- 分析报告输出固定为 `/home/maoyd/wiki/companies/<company_id>/analysis/<stock_code>-<short_name>-<year>-analysis.{md,json,html}`。
- 阶段检查点固定为 `/home/maoyd/wiki/companies/<company_id>/analysis/.work/<stock_code>-<short_name>-<year>-analysis/`。
- 若路径不存在，先调用 `resolve_company.py` 获取 `paths.company_dir.path`；不得反复扫描 `.hermes/wiki` 或 `profiles/siq_analysis/home/wiki`。
- 已有 `final_validation.json.ok=true` 或 `pipeline_result.stage=completed` 时，默认视为完成；除非用户明确说“强制重建/覆盖重建”，不得重复运行完整生成流程。

## 轻量运维

- 定期归档 `sessions/` 中长历史会话。
- 定期对 `state.db` 执行 vacuum，避免状态膨胀影响诊断速度。
- curator 可在空闲时启用；报告生成任务执行中不要让 curator 修改工作目录。
- `models_dev_cache.json`、`state.db-wal` 膨胀时优先做备份和 vacuum，不直接删除。

维护入口：

```bash
/home/maoyd/.hermes/profiles/siq_analysis/scripts/maintain_profile.py --archive-sessions-older-than-days 30 --vacuum
```

首次执行建议先加 `--dry-run` 查看会归档哪些会话。

## 工具纪律

- 完整年度报告优先使用主流水线；单个脚本只用于调试或定向修复。
- 只读查询和确定性脚本优先；避免反复扫描同一大文件。
- PostgreSQL 只用于补缺、交叉校验和补页码。
- 同一工具连续 2 次失败后，必须降级为基于已有检查点继续生成，并把失败项写入 `quality_report.review_queue`。
- `read_file` 或 `execute_code` 失败后，不得凭上一轮 `execute_code` 中的变量继续写下一段代码；Hermes 的每次 `execute_code` 调用都是全新进程，必须在同一次代码块内重新读取文件、定义变量、执行保存和验证。
- 若连续读取检查点文件失败，先用 `run_analysis_report.py --reuse-checkpoint` 或 `recover_report_from_workdir.py` 做确定性恢复；不要继续围绕同一路径反复 `read_file`。
- 若用户要求“强制重建/覆盖重建”，仍必须先用 `resolve_company.py` 或 `run_analysis_report.py` 输出确认真实 `work_dir` 和 `output_prefix`；不得把当前打开的 HTML、测试前缀或旧 session 中的路径当成目标文件。
- 同一分析请求已完成且输入未变化时，必须返回已完成状态和产物路径；不得再次创建新 run 或重跑恢复命令。
