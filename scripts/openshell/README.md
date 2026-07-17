# SIQ OpenShell 工具

本目录用于离线 registry/policy 编译、项目本地 toolchain 包装、诊断、灰度和回退命令。

所有脚本必须满足：

1. 默认不安装软件、不启动或销毁 gateway；
2. 默认不读取或输出环境变量值；
3. 运行状态写入 `var/openshell/`，脱敏证据写入 `artifacts/openshell/`；
4. 管理命令显式设置项目专用 `XDG_CONFIG_HOME`、`XDG_STATE_HOME` 和 `XDG_CACHE_HOME`；
5. 检测到目标为现有 `nemoclaw` gateway 时失败关闭；
6. 任何破坏性动作必须提供独立命令和显式确认，不能隐藏在启动流程中。

使用入口：

```bash
# 只查看隔离后的目录，不写入状态
scripts/openshell/env.sh

# 项目内 v0.0.83 未安装时会失败关闭，不会回退到 ~/.local/bin/openshell
scripts/openshell/run_cli.sh doctor check

# 编译并安装经过审查的 Landlock supervisor（会短暂停止并自动恢复独立 gateway）
scripts/openshell/build_patched_supervisor.sh

# 生成独立 mTLS 和 gateway 配置，不启动进程
scripts/openshell/prepare_gateway.sh

# 仅管理 siq-openshell-dev，不操作 nemoclaw
scripts/openshell/start_gateway.sh
scripts/openshell/status_gateway.sh
scripts/openshell/stop_gateway.sh

# 最小 Hermes PoC；仅验证一个 profile，默认不会改变 SIQ host runtime
scripts/openshell/start_hermes_poc.sh
scripts/openshell/smoke_hermes_poc.sh
scripts/openshell/stop_hermes_poc.sh

# NOT_PRODUCTION：真实 siq_analysis + 已配置 MiniMax provider 的隔离 observe PoC。
# 不挂载宿主 Wiki/profile/database，不切换默认流量；必须显式确认。
scripts/openshell/start_siq_analysis_observe_poc.sh --acknowledge-not-production
scripts/openshell/smoke_siq_analysis_observe_poc.sh
scripts/openshell/stop_siq_analysis_observe_poc.sh

# NOT_PRODUCTION：真实公司 company.json + 正式七挂载/leaf policy 的宽松业务 pilot。
# 不进入 formal transaction，不改变 host 默认流量；无论 smoke 成败都必须 stop。
scripts/openshell/start_siq_analysis_wide_pilot.sh --acknowledge-not-production-wide-pilot --market cn --company COMPANY --pilot-id pilot-HEX12
scripts/openshell/smoke_siq_analysis_wide_pilot.sh --market cn --company COMPANY --pilot-id pilot-HEX12
scripts/openshell/stop_siq_analysis_wide_pilot.sh --pilot-id pilot-HEX12

# NOT_PRODUCTION_CANARY：真实 siq_analysis + 当前公司完整 analysis 写权限。
# 不要求 Exa/8004/8006/Milvus formal proof；不放宽正式 lifecycle 门禁。
scripts/openshell/start_siq_analysis_canary.sh --acknowledge-not-production-canary --market cn --company COMPANY --run-id canary-HEX12
scripts/openshell/status_siq_analysis_canary.sh --run-id canary-HEX12
scripts/openshell/probe_siq_analysis_canary.sh --run-id canary-HEX12
scripts/openshell/stop_siq_analysis_canary.sh --run-id canary-HEX12
scripts/openshell/rollback_siq_analysis_canary.sh --run-id canary-HEX12

# NOT_PRODUCTION_CANARY pool slot：端口 reservation/token 由 owner-only manager 内部处理。
# status/stop 从 registry 读取精确 slot endpoint，不会影响 28651 legacy canary。
scripts/openshell/run_siq_analysis_pool_lifecycle.sh start --acknowledge-not-production-canary --market cn --company COMPANY --run-id canary-HEX12 --local-port 28652
scripts/openshell/run_siq_analysis_pool_lifecycle.sh status --market cn --company COMPANY --run-id canary-HEX12
scripts/openshell/run_siq_analysis_pool_lifecycle.sh stop --market cn --company COMPANY --run-id canary-HEX12

# PostgreSQL reader：默认只输出 plan；apply/rollback 需要精确确认
python3 scripts/openshell/provision_postgres_reader.py plan
python3 scripts/openshell/provision_postgres_reader.py apply --confirm-role siq_openshell_reader
python3 scripts/openshell/provision_postgres_reader.py verify --confirm-role siq_openshell_reader

# 固定 host brokers；只绑定经验证的 siq-openshell-dev bridge gateway。
# start_all.sh 默认 auto 管理并强制 strict request identity；egress 不继承数据库或模型凭据。
scripts/openshell/start_brokers.sh --require-request-identity
scripts/openshell/status_brokers.sh --require-request-identity
scripts/openshell/stop_brokers.sh
python3 scripts/openshell/export_broker_status.py
python3 scripts/openshell/run_egress_boundary_proof.py --project-root "$PWD"
python3 scripts/openshell/run_memory_write_probe.py --api-pid API_PID
python3 scripts/openshell/build_memory_write_evidence.py --project-root "$PWD"

# 正式 siq_analysis 候选镜像和单任务 lifecycle；不会自动切流
scripts/openshell/build_siq_analysis_image.sh
scripts/openshell/smoke_siq_analysis_image.sh
scripts/openshell/run_milvus_boundary_proof.sh --market cn --company COMPANY --probe-id probe-HEX12 --acknowledge-not-production
python3 scripts/openshell/check_siq_services.py --host-alias 127.0.0.1 --proof-file var/openshell/proofs/service-security.json --milvus-proof-file var/openshell/proofs/milvus-write-protection.json --output artifacts/openshell/v0.6/service-preflight.sanitized.json --markdown-output artifacts/openshell/v0.6/service-preflight.sanitized.md --replace --json
python3 scripts/openshell/export_provider_inventory.py
python3 scripts/openshell/check_siq_analysis_ab_prerequisites.py --help  # --output 仅写固定 evaluation 路径；覆盖需 --replace
python3 scripts/openshell/run_formal_filesystem_boundary.py \
  --run-id RUN_ID \
  --artifact-json artifacts/openshell/v0.6/formal-filesystem-boundary.sanitized.json \
  --artifact-markdown artifacts/openshell/v0.6/formal-filesystem-boundary.sanitized.md
python3 scripts/openshell/build_v06_readiness.py \
  --generated-at YYYY-MM-DDThh:mm:ssZ
python3 scripts/openshell/check_v06_completion.py --project-root "$PWD" --json
python3 scripts/openshell/export_sanitized_logs.py \
  --audit var/openshell/audit/YYYY-MM-DD.jsonl \
  --operational gateway=var/openshell/gateway/siq-openshell-dev/gateway.log \
  --output-root artifacts/openshell/v0.6/logs-YYYYMMDD
python3 scripts/openshell/build_tracked_artifact_manifest.py --project-root "$PWD" --refresh
python3 scripts/openshell/check_tracked_state.py --repo-root "$PWD" --require-allowlist --json
scripts/openshell/run_hermes_gateway.sh --profile siq_analysis --market cn --company COMPANY --run-id RUN_ID
scripts/openshell/status_hermes_gateway.sh --profile siq_analysis --run-id RUN_ID
scripts/openshell/stop_hermes_gateway.sh --profile siq_analysis --run-id RUN_ID
scripts/openshell/rollback_to_host.sh --profile siq_analysis --run-id RUN_ID
scripts/openshell/recover_hermes_gateway.sh --profile siq_analysis --run-id RUN_ID
scripts/openshell/repair_hermes_gateway.sh --profile siq_analysis --run-id RUN_ID

# Provider 默认 dry-run；apply 不会打印 credential values
python3 scripts/openshell/provision_siq_providers.py
```

`export_sanitized_logs.py` 对 structured audit JSONL 复用严格 schema 校验并聚合；对
gateway/broker/forward operational log 只发布 byte/line/severity counts 与 SHA-256，
绝不复制原始消息、文件名或路径。输出仍需进入 `tracked-artifacts.json`；
`check_tracked_state.py` 从 Git index 校验 manifest、mode、size、digest、凭据和业务正文，
而不是信任 `.sanitized` 文件名。完整发布规则见
`docs/runbooks/openshell/git-publication-policy.md`。

`run_formal_filesystem_boundary.py` 不创建 sandbox，只挂接到正在运行的正式
`siq_analysis` transaction。它验证固化数据、代码、配置、Prompt 和 workflow 拒写，
同时验证 analysis、runtime state、session、memory 和临时目录可写；不调用模型、
provider 或公网。没有 active formal transaction 时固定返回
`formal_active_transaction_required` 且不生成产物。详细流程见
`docs/runbooks/openshell/formal-filesystem-boundary.md`。

`run_formal_host_rollback.py` 把正式 rollback 分成 capture 和 publish：capture 先固定 active
transaction、live 7+5 mounts 和 host receipt，再调用既有 rollback wrapper；publish 从 terminal
journal 和当前 host 状态重验 owner-only raw receipt。`run_formal_delete_guard.py` 使用唯一合成
fixture 和四个不同正式 transaction，验证 shell/Python/Node 高危删除以及 mkdir/create/write/
overwrite/rename/少量删除/递归清理的正常权限。两者均拒绝 canary、手写 JSON 和跨 transaction
拼接；完整串行命令见对应 formal runbook。

`run_siq_analysis_fallback_drill.py` 必须在正常 A/B 已 GO 且没有 active formal transaction 时
运行。它使用独立 `fallback-*` transaction，并在 host `8004` 上临时建立仅 Docker bridge 可达的
503 stub；这只验证当前 primary provider 到既有 fallback 的真实切换和 telemetry，不启用或
验收 optional `8004/8006` 模型服务。completion 跨 transaction 比较 image、policy、runtime
config 和 normalized mount contract；每个 transaction 的原始 mount plan 仍由自身 terminal
receipt 严格校验，不能错误要求包含不同 run-id 的原始 mount plan SHA 相同。

正常 A/B 的 required tool 按同一工具最后一次 `tool.completed` 状态计分：失败后成功视为
已恢复，成功后最终失败仍不通过；`failed_tools` 只保留“本次运行曾失败”审计信息。工具遵循
不参与业务 `task_success`，但 `tool_success_rate` 继续要求 OpenShell 不低于 Host。`0.95`
绝对线只约束 OpenShell 的 task、引用、数值、幻觉阻断、证据覆盖和报告完整性，不约束 Host
baseline，也不用于工具指标；timeout 和正常 case 的 policy false positive 零容忍同样只约束
OpenShell 候选。两臂的业务指标、工具指标、延迟、协议和 runtime route 相对不回退门禁保持不变。
raw/summary 另行记录每个工具的 attempt/success/failure、retry、失败后恢复和最终未恢复计数与
rate；这些字段只用于审计和两臂差值，不因重复调用次数或错误次数高于 Host 直接阻断。
该口径从 private raw `v2` / summary `v3` 起生效；旧 raw `v1` / summary `v2` 只能保留为历史
诊断证据，publisher、completion、fallback 和正式业务回执不得把它们当作新口径结果复用。
并发评测必须为每个用户/批次使用唯一 `evaluation-id` 和对应 owner-only 目录，不能共享同一
可写 evaluation 路径或 key 文件。

`build_v06_readiness.py` 只从已有证据生成机器 readiness，不读取人工评审文件，也不会
启动或修改服务。写文件必须同时指定 `--output`、`--generated-at`，覆盖还需
`--replace`。正式可发布证据必须位于 `artifacts/openshell`，其工作树内容必须与 Git
stage-zero blob 相同；人工评审在 readiness 生成后由 `check_v06_completion.py` 单向校验，
避免 review/readiness 摘要循环。

`export_provider_inventory.py` 只读调用项目 wrapper 的 OpenShell `0.0.83`
`provider list -o json`，不会执行 provider 创建、更新或凭据读取。输出固定为
`var/openshell/proofs/provider-inventory.json`，权限为 `0600`，内容只保留 provider
名称和 `configured` 状态；provider ID、credential key/value、resource version 和 CLI
原始输出均不会写入 proof 或打印到终端。

`siq_analysis` observe PoC 只证明正式 profile 的 Hermes gateway、真实 OpenShell
provider、`8007` Nemotron 可用 fallback、`/v1/runs` create/SSE/stop 和 terminal tool 链路可行。Hermes runtime home
固定为 sandbox 内一次性目录，宿主业务目录没有 mount；smoke 还会比较静态 profile
内容摘要和 immutable registry 所有固化目录的前后元数据摘要。该结果固定标记
`readiness_effect=none`，不替代正式服务 preflight、数据访问、安全负向测试或 A/B。

`export_broker_status.py` 只调用现有 broker `status` 检查，不执行 start/stop/repair，也不创建
缺失的 broker 状态目录。它验证固定 `siq-openshell-dev` bridge、`18792/18793` 两个 broker
均为 `running`，然后原子写入 owner-only `0600`
`var/openshell/proofs/broker-status.json`。输出不包含 PID、cmdline、网络 ID、日志或凭据，
可直接作为 A/B 前置检查的 `--broker-report` 输入。

`run_egress_boundary_proof.py` 使用短期、分 audience 的内存身份真实验证公网 GET/HEAD、
未知小 JSON audit-only、multipart/octet-stream/PUT 和 metadata 拒绝，并把运行中 broker
启动时加载的 allowlist contract 与源码包摘要绑定到脱敏证据。该结果固定为
`scope=host_egress_broker`、`readiness_effect=none`，不替代正式 sandbox 的直连旁路、
文件传输客户端、provider 路由或语义 DLP 证明。

`check_siq_services.py` 输出 `siq.openshell.service_preflight.v2`。它先检查全部固定端口的
TCP transport，再对 `8004/8006/8007/8013` 固定执行无请求体的 `GET /v1/models`，对
SIQ API `18081` 和 host Hermes `18651` 固定执行 `GET /health`。HTTP 探测不带凭据、
不使用环境代理、不跟随重定向，也不记录响应正文或模型 ID；只保留最小 JSON contract、
状态码、稳定错误码和延迟。PostgreSQL 使用专用只读身份 proof；Milvus 必须使用独立
NOT_PRODUCTION OpenShell sandbox 生成的短期组合 proof，证明 `19530` 不可直连且仅
Search/Query/Get/Describe broker 可达。旧布尔自证会被拒绝。协议 discovery 通过不代表模型推理、embedding 生成、fallback 或
Hermes stream/stop 已通过，也不单独证明 sandbox alias 路由；这些仍由正式 sandbox
network smoke 和 A/B 验收。

正式 `start` 会再次执行只读 service/broker preflight，并在 transaction 创建前失败关闭；手工
preflight 只用于诊断，不会启动模型或修改数据库。runtime snapshot 会从当前
`data/hermes/home/profiles/siq_analysis/config.yaml` 重新编译，要求 alias、`28651`
和 provider/broker contract 的 digest 与候选镜像一致。`8004/8006/8007/8013` 始终保留
在白名单中，端口离线不会被自动启动或从策略删除。

`_index.json` 不由 sandbox 内脚本直接写入：host Hermes 使用固定
`publish_company_index.py`，OpenShell stop/rollback 在清理 sandbox 后用固定
market/company 参数调用同一 Publisher。发布失败仅返回 deferred 并审计，不影响
analysis/factcheck 主产物和原有输出路径。Publisher 以 `dirfd`/inode 锚定项目、锁目录和
公司目录，构建前后核对输入树身份，并通过固定 `_index.json` 名称执行 `openat`/`renameat`
原子发布；锁、输入或输出出现 symlink、hardlink 或运行中替换时均失败关闭。

`run_cli.sh` 会强制覆盖父进程的 XDG 路径，把 config、state、data、cache 和 TLS 都定向到 `var/openshell/xdg/`。gateway 名称固定为 `siq-openshell-dev`；父进程指定其他名称、空值或 `nemoclaw` 时均失败关闭。

`bridge_endpoint.py` 只检查固定 Docker network `siq-openshell-dev`，并只输出其
唯一 RFC1918 IPv4 gateway 与固定 alias `host.openshell.internal`。broker lifecycle
固定使用 `18792/18793`，PID、cmdline、进程启动时间、监听地址和 health 必须同时
一致；任何一项冲突都不会发送信号或接管端口。status/stop 从 owner-only v2 PID
record 的 command digest 与 `/proc` observed executable 重建 exact argv，因此检查器可
使用不同 Python 版本，但 PIDFD 打开前后的身份强度不变。

`start_all.sh` 仍把 `SIQ_HERMES_RUNTIME=host` 作为环境回退基线，并默认管理项目
gateway 和可用 brokers。`switch_siq_analysis_runtime.sh` 通过 owner-only 状态文件热切换
`siq_analysis`；只有请求公司与健康 active canary 精确匹配时才进入 OpenShell，其他公司和
其他 profile 继续使用 Host。启动输出同时显示环境回退和实际热切换状态。
