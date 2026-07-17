# SIQ OpenShell 运维入口

OpenShell V0.6 的首要原则是保持 Hermes 输出路径和业务契约不变，只在执行面增加最后一道安全保险。

当前状态（2026-07-17）：Hermes 升级已冻结；上汽集团 `canary-8e695febb483:28652` 和贵州茅台 `canary-ee8d4d9c4e97:28653` 均运行候选镜像 `aadca453805ef6d783956e93`，lifecycle status、认证 probe、Docker health 和 API pool recovery 均正常。Host 仍是默认 runtime，未注册公司和其他 profile 自动留在 Host。Exa 延期配置，`8004/8006` 为 optional 且当前禁用；三者不阻断当前 canary。正式全量门禁仍为 `NO_GO 1/13`，两家公司 live 不等于正式 GO。任何操作前先查看：

- `docs/runbooks/hermes-upgrade-freeze.md`；
- `artifacts/openshell/v0.6/baseline.md`；
- `var/openshell/manifests/toolchain.sanitized.json`。
- `docs/runbooks/openshell/review-record-template.md`（人工架构与安全评审，模板未填写前不算批准）。

OpenShell 源码、配置、测试、脱敏证据和脱敏日志默认都可以进入 Git；凭据值和未经脱敏的业务正文不得进入。
具体发布边界、manifest 摘要绑定及检查命令见 `docs/runbooks/openshell/git-publication-policy.md`。

不得直接运行官方安装器、销毁现有 gateway 或把项目 CLI 指向 `nemoclaw`。

正式 `siq_analysis` 单任务 lifecycle 的离线实现、调用边界和 live blocker
见 `docs/runbooks/openshell/siq-analysis-lifecycle.md`。当前只允许公司范围匹配的运行优先
canary；不得据此宣称正式全量 GO。

两家公司 slot、同公司跨进程 FIFO、跨公司并行、显式认证 principal、上下文隔离边界及
受约束 `migrate-legacy`/Host fallback 见
`docs/runbooks/openshell/siq-analysis-pool.md`。当前长驻 sandbox 共享公司 `analysis/` 和
runtime state，只允许可信内部公司级 canary，不具备强多用户/多租户物理隔离。

正式 sandbox 到 egress/data broker 的短期、分 audience 请求身份、严格模式切换和
密钥轮换见 `docs/runbooks/openshell/broker-request-identity.md`。

Clash Verge/Mihomo TUN `fake-ip` 的显式兼容、真实 peer 绑定和回滚见
`docs/runbooks/openshell/mihomo-fake-ip-egress.md`；该兼容默认关闭。
宿主 egress broker 的真实读取/上传/metadata 边界证明及其非正式 sandbox 限制见
`docs/runbooks/openshell/egress-boundary-proof.md`。
一级/二级市场 Agent memory 的宿主写入 alias 白名单、schema preflight 和 sandbox
边界见 `docs/runbooks/openshell/memory-write-boundary.md`。
service preflight v2 的 TCP/只读 HTTP 契约、正式证据导出和 v1 兼容边界见
`docs/runbooks/openshell/service-protocol-preflight.md`。
候选镜像的 provider/gateway-independent 两轮运行态 smoke 与当前正式
mount/Landlock blocker 见 `docs/runbooks/openshell/siq-analysis-runtime-lifecycle-smoke.md`。
真实公司 `company.json` 读取、唯一 `.work/pilot-*` 写入、当前 provider 子集和
Tavily 的 NOT_PRODUCTION 宽松业务验证见
`docs/runbooks/openshell/siq-analysis-wide-pilot.md`；其结果始终不影响 readiness。
当前四 provider、七加五挂载及当前公司完整 `analysis/` 业务写权限的独立宽松 canary
见 `docs/runbooks/openshell/siq-analysis-canary.md`。它不要求 Exa、`8004/8006` 或
Milvus formal proof，也不改变正式 lifecycle 的严格门禁；其运行选择仅影响与 active
company 精确匹配的 `siq_analysis` 请求。
正式 transaction 内的固化路径拒写、analysis/session/memory 文件面可写及源码/证据
摘要绑定见 `docs/runbooks/openshell/formal-filesystem-boundary.md`。当前没有 active formal
transaction，因此尚未生成正式 filesystem GO 证据。
正式 transaction 的 public read/小 JSON 正向控制、危险上传/直连负向控制和同一
transaction 结构化审计导出见 `docs/runbooks/openshell/formal-egress-audit.md`。
正式 rollback 的执行前后精确 host receipt、terminal transaction 和清理证据见
`docs/runbooks/openshell/formal-host-rollback.md`。正式批量删除三路径、正常叶目录文件生命周期、
四 transaction 绑定及合成 fixture 清理见 `docs/runbooks/openshell/formal-delete-guard.md`。

发布前用 `python3 scripts/openshell/check_v06_completion.py --json` 逐条核对任务书
第 16 节；当前真实结果为 `NO_GO 1/13`。只有人工评审、真实业务 A/B 和所有正式证据齐全后，
才允许使用 `--require-go` 作为发布门禁。

正式 A/B 完成后，完成门禁必须同时传入同一 evaluation 的
`--ab-summary` 与 `--ab-prerequisites`；后者包含 hash-bound provenance 前置结果，
不能用单独的 summary 替代。正常 A/B 不注入故障；独立
`run_siq_analysis_fallback_drill.py` 会在无 active formal transaction 时启动一次性 sandbox，
仅在已验证 Docker bridge 地址的宿主 `8004` 临时监听 503 stub，验证 primary 失败后的既有
fallback 和 telemetry，随后删除 sandbox、forward 与 stub listener。该端口用途不启动、调用
或验收当前禁用的 `8004/8006` 可选模型服务。

已验证的最小流程（只使用 `siq-openshell-dev`）：

```bash
scripts/openshell/build_patched_supervisor.sh
scripts/openshell/start_hermes_poc.sh
scripts/openshell/smoke_hermes_poc.sh
scripts/openshell/stop_hermes_poc.sh
```

构建脚本使用维护锁、fresh source/target、离线 Cargo 编译和容器内候选
ELF 校验；不要手工替换 `var/openshell/toolchains/v0.0.83/bin/openshell-sandbox`。
`restore_upstream_supervisor.sh` 可在无 sandbox 的维护窗口恢复官方备份。
