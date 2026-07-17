# siq_analysis 宽松业务 Pilot 运行手册

该流程只用于证明 OpenShell 能在当前 SIQ 真实路径上读取固化数据、运行 Hermes、
写入受控派生目录并清理。它不切换默认流量，不改变 `start_all.sh`，也不会让
`check_v06_completion.py` 或 A/B 前置门禁变为 GO。

## 前置条件

1. 项目 gateway `siq-openshell-dev` 已连接，且没有其他 sandbox 或 `28651` 占用。
2. 正式候选镜像和离线 smoke 与当前代码 hash 一致。
3. 宿主 `siq_analysis` `18651` 健康且身份稳定。
4. MiniMax、StepFun、Kimi、Tavily 四个 provider 已配置。
5. `18792/18793` brokers 均处于 `request_identity_required=true`。
6. 目标公司存在常规文件 `company.json` 和常规目录 `analysis/.work`。

Exa、`8004/8006` 和 Milvus 正式 proof 不是本 pilot 的依赖，但会原样记录为正式
readiness blocker。脚本不会配置、启动或伪造这些能力。

## 固定流程

每次使用新的 12 位十六进制 pilot ID：

```bash
PILOT_ID=pilot-a17e4c9b620d

scripts/openshell/start_siq_analysis_wide_pilot.sh \
  --acknowledge-not-production-wide-pilot \
  --market cn \
  --company '600104-上汽集团' \
  --pilot-id "$PILOT_ID"

scripts/openshell/status_siq_analysis_wide_pilot.sh \
  --pilot-id "$PILOT_ID"

scripts/openshell/smoke_siq_analysis_wide_pilot.sh \
  --market cn \
  --company '600104-上汽集团' \
  --pilot-id "$PILOT_ID"

scripts/openshell/stop_siq_analysis_wide_pilot.sh \
  --pilot-id "$PILOT_ID"
```

无论 smoke 成功或失败，最后一条身份校验 stop 都必须执行。不得用 Docker 名称、
`pkill` 或手工删除 gateway state 代替 stop。

## Smoke 验收

`smoke` 必须同时通过：

1. guard、forward、sandbox 和 Hermes health 均健康；
2. 七个 business mounts 与正式 mount plan 完全一致，五个 OpenShell control mounts
   均为只读且来源位于项目 `var/openshell`；
3. sandbox 为 uid/gid 1000，独立 runtime state 可写；
4. 修改 `company.json` 和创建 sibling `.work` 文件均被 Landlock 拒绝；
5. 四个现有 provider 环境值仍为 OpenShell placeholder，Exa 不被伪造；
6. Tavily 真实搜索成功，仅返回脱敏的 result count；
7. `/v1/runs` 返回 202，SSE 包含一次 terminal `tool.started/tool.completed` 和
   `run.completed`；
8. 结果 JSON 的 pilot ID、schema、stock code 和源文件 SHA-256 精确匹配；
9. `company.json` 内容不变，结果文件与唯一 pilot 目录已删除。

成功的 `probe`、业务 contract 和 `stop` 会分别在 owner-only run 目录留下：

```text
probe.sanitized.json
contract.sanitized.json
stop.sanitized.json
```

三份 receipt 只包含布尔结论、计数、schema 和模式，不包含公司内容、Prompt、模型
响应、key、nonce 或 broker identity token。后四类临时身份文件必须在 stop 后消失。

## 失败处理

Start 在任何阶段失败都会按已掌握的 PID、nonce、gateway ID 和 Docker ID 尝试
回滚，并保持宿主 Hermes 不变。返回 `wide_pilot_rollback_incomplete` 表示身份或
清理结果不能证明，此时状态会保留供检查，禁止按名称强删。

Smoke 失败后立即运行固定 stop。若 stop 失败：

```bash
scripts/openshell/status_siq_analysis_wide_pilot.sh --pilot-id "$PILOT_ID"
scripts/openshell/run_cli.sh sandbox list -o json
ss -ltnp 'sport = :28651'
```

只查看以下 owner-only 状态，不打印 key、nonce、identity token 或原始日志：

```text
var/openshell/poc/siq-analysis-wide/active.json
var/openshell/poc/siq-analysis-wide/runs/<pilot-id>/pilot.json
var/openshell/poc/siq-analysis-wide/runs/<pilot-id>/*.process.json
var/openshell/poc/siq-analysis-wide/runs/<pilot-id>/guard.outcome.json
var/openshell/poc/siq-analysis-wide/runs/<pilot-id>/*.sanitized.json
```

常见稳定错误：

- `wide_pilot_provider_subset_missing`：四个 pilot provider 至少一个不存在；
- `wide_pilot_broker_preflight_no_go`：broker 未运行或未启用严格请求身份；
- `wide_pilot_policy_scope_invalid`：写权限没有收敛到唯一 pilot leaf；
- `wide_pilot_tavily_provider_probe_failed`：Tavily provider 路由或凭据调用失败；
- `wide_pilot_filesystem_boundary_failed`：只读/可写边界与预期不一致；
- `wide_pilot_host_hermes_identity_changed`：宿主基线在 pilot 期间发生变化；
- `wide_pilot_output_cleanup_unsafe`：输出目录含未知项，保留现场且拒绝递归删除；
- `wide_pilot_rollback_incomplete`：自动回滚无法完成身份闭环，需要人工复核。

## 结果边界

通过只证明单公司、单次确定性工具写入和当前 Tavily provider 可用。它不评估报告
质量、完整 fallback、长任务稳定性、并发任务或生产流量。下一步仍是满足正式
preflight 后，在同 Hermes、Prompt、模型、输入、数据和 fallback 顺序下运行至少
10 个 case、3 次重复的真实 A/B。
