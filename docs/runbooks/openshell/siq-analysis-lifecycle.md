# siq_analysis 正式 OpenShell Lifecycle 开发报告

状态：正式 lifecycle、transaction v2、镜像 smoke 和 provider-independent live security probe 已完成；正式 start 现在会在创建 transaction 前执行必需服务连通性、数据库安全 proof 和双 broker preflight。Exa 为切流后配置的 deferred 搜索工具，`8004/8006` 为当前禁用的 optional fallback，三者不再阻断正式 sandbox；其模板和白名单仍保留。其余正式证据缺口仍失败关闭，`start_all.sh` 只接入 gateway/brokers，不接入 Hermes 流量。

## 固定入口

```bash
scripts/openshell/run_hermes_gateway.sh \
  --profile siq_analysis \
  --market cn \
  --company '600104-上汽集团' \
  --run-id task-001

scripts/openshell/status_hermes_gateway.sh \
  --profile siq_analysis \
  --run-id task-001

scripts/openshell/stop_hermes_gateway.sh \
  --profile siq_analysis \
  --run-id task-001

scripts/openshell/rollback_to_host.sh \
  --profile siq_analysis \
  --run-id task-001

scripts/openshell/recover_hermes_gateway.sh \
  --profile siq_analysis \
  --run-id task-001

# 等价的运维入口：只恢复身份绑定的 transaction，不猜测清理资源
scripts/openshell/repair_hermes_gateway.sh \
  --profile siq_analysis \
  --run-id task-001
```

入口只允许 `siq_analysis`、六个固定市场和单家公司直接 `analysis/`
目录。sandbox 名称、namespace、forward 地址和端口均由代码生成，不能由
调用方覆盖。

## 启动事务

启动操作持有项目 maintenance lock，并在 transaction v2 journal 中按资源记录意图和回执摘要。长驻 guard/forward 不继承该启动锁；guard 只有在触发动作时、watchdog 只有在恢复时重新取得一个有时限的独立锁。journal 不包含 API key、nonce 或业务正文，并按以下顺序 fail-closed：

1. 验证项目内 OpenShell `0.0.83`、隔离 gateway、Landlock 补丁记录和零
   sandbox/容器/端口冲突。
2. 验证五个正式 provider 已存在，且名称与受审 manifest 完全一致。
3. 以只读 TCP probe 检查固定服务端口，并验证 PostgreSQL/Milvus secret-free
   proof；再检查 `18792/18793` broker 的 PID、端口、bridge network 和 alias。
   任一 required service、proof 或 broker 不满足都在 transaction 创建前返回
   `NO_GO`。
4. 验证候选镜像 provenance 和与镜像状态、镜像 ID、smoke 脚本哈希绑定的
   `current-image.smoke.json`。
5. 从当前 profile source 重新编译 runtime config，并要求 compiled hash 与候选
   镜像 label 一致；随后创建隔离 Hermes runtime snapshot。
6. 生成并独立复核恰好 12 个 mount 的 driver-config。
7. 为当前公司 `analysis/` 编译单任务 policy。
8. 启动删除守卫，等待 snapshot 和递归 inotify watch ready。
9. 生成一次性 API key 和 192-bit run nonce，仅写入 `0600` ignored state。
10. 使用固定 driver-config、policy、labels 和正式 providers 创建 sandbox。
11. 交叉验证 gateway ID/labels 与 Docker ID/name/namespace/labels。
12. 启动 `127.0.0.1:28651` forward，验证正确 key 成功且无 key/错 key 均
   返回 401。

任一步失败都会先持久化 `failed_start -> rollback_pending`，再按已验证身份删除 sandbox、停止 forward/guard、删除 key/nonce，并保留 runtime/deletion snapshot、资源回执和无密 manifest。身份不能完成交叉验证时拒绝猜测式清理，保留 active transaction 供恢复或人工复核。

`recover_hermes_gateway.sh` 只恢复唯一、身份绑定的 transaction。它可以继续已开始的 stop/rollback、完成 interrupted finalize，以及回滚尚未提交的 start；正常 guard 仍在运行的 running orphan 只恢复 active pointer，不停止进程或删除 sandbox。若 guard process 已消失、存在 durable trigger/failed outcome，则按 fail-closed stop 清理并恢复快照。缺少 process receipt、仅剩 Docker orphan 或身份冲突时保持 fail-closed。

`repair_hermes_gateway.sh` 是同一恢复实现的明确运维别名，适合故障手册和自动化编排使用；它不增加资源发现、强制删除或 host runtime 切换能力。

## Stop 与回退边界

Stop 在发出任何信号前验证 active transaction、manifest、资源 receipt、nonce digest、gateway sandbox ID/labels、Docker managed labels，以及 guard/forward PID、进程启动时钟和 argv digest。终止信号只通过 pidfd 发送。它只处理 `siq-analysis-<run-id>`，不会枚举删除其他 sandbox，不会访问 `nemoclaw`，也不会停止宿主 Hermes。

清理 sandbox、forward、guard 和一次性凭据后，stop/rollback 会在 host 侧用固定
`market/company` 参数调用 `scripts/openshell/publish_company_index.py`。Publisher
失败不会使分析或事实核查主结果失败，返回 `publisher.status=deferred` 并写入
`publisher.index` 的 `audit_only` 记录；成功返回 `publisher.status=published`。
若 guard 已在 stop/rollback 获锁前写入 trigger，既有 terminal action 不会被改写，
但清理会先恢复该 guard 的 digest-bound deletion snapshot。

`rollback_to_host.sh` 使用启动时固化在 run directory 的宿主 Hermes baseline receipt，在清理前后交叉验证 gateway state、`/proc` 身份、监听 socket、环境身份和 health。它不修改环境文件和宿主进程，只返回 host runs URL 和 receipt digest。

## 当前 live blocker

1. Hash-bound image smoke 已实际通过；policy 通过该证明验证镜像内 runtime
   metadata，不再要求或伪造宿主 `.clean_shutdown` 等 marker。
2. MiniMax、StepFun、Kimi、Tavily provider 已配置；Exa 是切流后配置项，不再阻断
   正式 start。provider 模板和目标规则仍保留，未来启用时必须补真实协议和 fallback
   验收，不能把模板存在解释为能力已验证。
3. `8004/8006/8007/8013` 始终保留白名单。当前 `8004/8006` 未启动，`8007/8013`
   在线；v2 preflight 已对在线端口验证 `GET /v1/models` 最小 JSON contract，并对
   SIQ API/host Hermes 验证 `GET /health` 的 `status=ok`。该只读 discovery 不执行
   推理或 embedding，按项目决策也不由 OpenShell 开发流程启动或停止模型。
4. Host brokers、Milvus sandbox 写保护、正式模型请求、stream/stop 和一次 host rollback
   lifecycle 已真实联调。正式删除、出网、结构化审计、Host/OpenShell A/B 和独立
   fallback 演练仍必须在灰度前形成同一候选 provenance 下的证据。
5. provider-independent live probe 已验证 7 个业务 mount 加 5 个 control mount：固化数据、代码、配置、Prompt、workflow 只读，当前公司完整 `analysis/`、session、memory 和 runtime state 可写，deny-all 网络生效，且退出后 sandbox、容器和 sentinel 均清理。`analysis/` 内的任务叶目录可由智能体按需新建，不要求宿主预建 `.work`。OpenShell JWT/TLS 客户端材料作为固定只读控制挂载可被运行时读取但不可写，其他宿主凭据继续隐藏。该探针不调用 provider，不构成业务质量或 A/B 结论。

宿主基线还观察到 `siq_assistant` 的 Kimi 认证不可用后 fallback，以及一次
SSE transport reset。该问题必须先在宿主凭据/外部反代层定位；不得为了让
OpenShell A/B 通过而改动模型、fallback 顺序或 Prompt。仓库生产 Nginx 模板的
通用 Agent API location 已设置 `1900s` 读写超时，但公网当前使用的 Synology
反代未由本项目自动验证或重载。

API key 不会进入 lifecycle JSON 输出、manifest 或日志；OpenShell `0.0.83`
只提供 `sandbox create --env` 作为此应用 key 的注入面，因此 key 会在 create
子进程 argv 中短暂存在。若威胁模型要求防同 UID `/proc` 读取，需要先增加
OpenShell secret-env/file 注入能力，不能改用可提交配置或长期 key 绕过。

V0.6 的主威胁边界是 sandbox 内智能体、外部恶意内容和误操作。`var/openshell` 不挂载给 sandbox，并以 `0700/0600`、maintenance lock、dirfd 和 pidfd 保护。能够以宿主同一 UID 运行且主动对抗这些锁的恶意进程不属于本版隔离边界；需要该级别防护时应把 lifecycle 放入独立系统用户或特权 supervisor。

## 离线验收

`test_siq_analysis_lifecycle.py` 使用完全注入的 fake backend，不连接 gateway、
Docker 或网络。它覆盖成功顺序、service/broker/proof fail-closed、runtime config
hash drift、stale smoke、policy blocker、create 后失败回滚、PID 身份冲突、严格
stop、host rollback、Publisher deferred audit、guard 终止器和 secret-free 状态。
