# siq_analysis OpenShell 宽松 Canary 运行手册

该 lifecycle 用于尽快把真实 `siq_analysis` Hermes 链路放入 OpenShell，并用小流量
验证业务兼容性。它是独立的 `NOT_PRODUCTION_CANARY`，不进入正式 transaction，
不改变 V0.6 readiness，也不会自动替换宿主 `18651` 默认运行时。

## 权限边界

Canary 沿用正式候选镜像与固定安全结构：

- 七个 business mounts：Wiki 只读、当前公司 `analysis/` 可写、五个隔离 Hermes
  runtime state 挂载可写；
- 五个 OpenShell control mounts：全部只读，来源必须位于项目
  `var/openshell/`；
- 当前四个 provider：MiniMax、StepFun、Kimi、Tavily；
- `18792/18793` broker 必须启用短期、分 audience request identity；
- sandbox、Docker container、forward 和 guard 都使用 run ID、nonce、PID/start ticks
  及固定 label 交叉验证。

当前公司的既存 `analysis/` 是唯一业务可写根。智能体可以在其下按现有工作流创建、
修改、重命名和清理目录或文件，包括 `.work/`、解析中间目录、checkpoint、图表和
派生报告。启动前不要求 `.work/` 存在。lifecycle 可在已存在且无符号链接的公司根下
创建缺失的 `analysis/` 叶目录，但绝不创建公司根或项目父目录。

以下范围仍不可写：

- 当前公司 `company.json`、`reports/`、`metrics/` 等固化资产；
- 其他公司及市场目录；
- 项目代码、配置、Prompt、profile、skills、workflow 和 OpenShell policy；
- `.git`、凭据、Docker socket、宿主配置和 OpenShell 控制状态。

OpenShell 自身的 sandbox JWT 与 mTLS client key 是运行时例外：它们通过五个身份校验的
control mounts 只读提供给 sandbox，用于 supervisor/gateway 通信。智能体不能修改这些
文件，也不能访问其他宿主凭据根；日志、业务输出和可发布证据不得包含其内容。

正常删除没有被全局禁止。删除守卫仅在删除 `analysis_root` 本身、一次观察到超过
500 个基线文件删除，或至少删除 20 个且达到基线文件数一半时终止 sandbox 并恢复
已删除的基线文件。新建临时文件的创建和清理、少量旧产物清理继续放行。

## 非正式前置条件

1. `siq-openshell-dev` gateway、候选镜像及其离线 smoke 与当前代码一致。
2. 宿主 `siq_analysis` `127.0.0.1:18651` 健康且身份稳定。
3. `siq-minimax-cn-pool`、`siq-stepfun`、`siq-kimi-coding`、
   `siq-tavily-search` 已存在。
4. `18792/18793` brokers 均为 `request_identity_required=true`。
5. 目标公司普通目录和单链接普通文件 `company.json` 已由入库流程创建；`analysis/`
   可以已存在，也可以由 lifecycle 以固定权限创建叶目录。
6. gateway 当前没有其他 sandbox，`127.0.0.1:28651` 未被占用。

Exa、`8004`、`8006` 和 Milvus 正式 proof 不是该 canary 的启动条件。脚本不会删除其
固定网络白名单，也不会伪造或自动启动这些服务。正式 lifecycle 的严格门禁保持不变。

## 启动与检查

每次使用新的 12 位十六进制 run ID：

```bash
RUN_ID=canary-a17e4c9b620d

scripts/openshell/start_siq_analysis_canary.sh \
  --acknowledge-not-production-canary \
  --market cn \
  --company '600104-上汽集团' \
  --run-id "$RUN_ID"

scripts/openshell/status_siq_analysis_canary.sh --run-id "$RUN_ID"
scripts/openshell/probe_siq_analysis_canary.sh --run-id "$RUN_ID"
```

`probe` 只创建并清理一个唯一的隐藏测试目录；它验证 analysis 内
create/modify/rename/delete、固化资产和控制面拒写、跨公司拒写、provider placeholder、
broker identity、七加五挂载。结果只写入 owner-only 的
`probe.sanitized.json`，不记录文件正文、Prompt、模型回复或 credential。

Canary 的 Hermes 入口固定为 `http://127.0.0.1:28651/v1/runs`，Bearer key 仅存在于
owner-only run state。外部 API 只能在严格验证 active pointer、manifest、run path、
key SHA-256 和 `phase=running` 后使用该入口。每次启动使用 fresh runtime：只复制编译后的
无凭据配置，`state.db`、`response_store.db`、`sessions/`、`memories/`、`checkpoints/` 和
`cron/` 均不从 Host 复制，由 sandbox 在空的 owner-only 运行目录内自行初始化。

API 的运行选择使用 owner-only 热切换文件，不需要为切流重启整套服务：

```bash
scripts/openshell/switch_siq_analysis_runtime.sh openshell --session-mode all
scripts/openshell/switch_siq_analysis_runtime.sh status
```

`session_mode=all` 只表示所有新旧会话都可申请 OpenShell，并不放宽公司边界。请求的
`market/company` 必须与 active canary 精确匹配；缺失或不匹配的隐式请求继续走 Host，
显式 `runtime_target=openshell` 则拒绝。session namespace 同时绑定 canary run、profile、
market 和公司投影，其他 Hermes profile 始终使用 Host。完整报告请求在范围匹配时也进入
OpenShell，不再由 API 绕到 Host 的确定性报告子进程。

## 停止和回退

正常停止：

```bash
scripts/openshell/stop_siq_analysis_canary.sh --run-id "$RUN_ID"
```

需要明确表达“保留 host、撤销 canary”时：

```bash
scripts/openshell/switch_siq_analysis_runtime.sh host
scripts/openshell/rollback_siq_analysis_canary.sh --run-id "$RUN_ID"
```

先切 Host 再停止 sandbox，避免在清理窗口接收新 OpenShell 运行。`start_all.sh` 的环境基线
仍固定为 Host，但会显示热切换文件的实际选择；重启后 active canary 不存在时，隐式流量
自动留在 Host，不会发送到失效端口。

两条路径都会按记录身份依次停止 forward、删除已验证 sandbox、停止 guard、核对宿主
Hermes identity、移除 runtime snapshot、删除守卫 snapshot 和临时 credential，并删除
active pointer。停止动作先把 manifest 原子标记为 `stopping` 并更新 active 摘要，API
因此会先停止发送新 canary 请求，再清理执行资源。业务 `analysis/` 输出按原路径保留，
不做猜测式递归清理。

Start 任一阶段失败也执行同一清理顺序。若返回 `canary_rollback_incomplete`，表示至少
一个资源身份或清理结果无法证明，必须保留现场，禁止按名称强删。

## 状态契约

运行态仅写入：

```text
var/openshell/canary/siq-analysis/active.json
var/openshell/canary/siq-analysis/runs/<run-id>/
```

`active.json`、`canary.json`、`api.key` 和各进程 receipt 均为 owner-only 普通文件。
真实 key、nonce、broker token 和原始日志不得提交 Git；`probe.sanitized.json`、
`stop.sanitized.json`、`rollback.sanitized.json` 可按发布规则进一步导出或聚合。

Canary 通过只证明“当前业务链路在宽松策略下可运行”。它不证明完整 fallback、Exa、
本地模型、正式 A/B、长期并发稳定性或生产发布条件，所有结果固定为
`readiness_effect=none` 和 `result_is_formal_evidence=false`。
