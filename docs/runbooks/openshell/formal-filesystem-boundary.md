# OpenShell 正式文件边界证据

`run_formal_filesystem_boundary.py` 只挂接到一个已经处于 `running` 阶段的正式
`siq_analysis` transaction，用来验证文件权限设计。它不创建 sandbox、不调用模型、
provider、broker 或公网，也不改变 Hermes API、输出路径和默认流量。

## 验证范围

Runner 在执行前后都复核同一 transaction、manifest、sandbox/container、policy、
mount plan、runtime config 和 host runtime receipt，并要求五类正式资源均为 `present`。
探针只执行以下操作：

- 拒绝修改固化 Wiki、项目代码、配置、Prompt 和 workflow；
- 允许读取但拒绝修改固定 OpenShell JWT/TLS control mounts，并拒绝读取其他宿主敏感路径；
- 允许写入任务 analysis、runtime state、session、memory 和 `/tmp`；
- 从宿主侧复核四个 bind sentinel 的内容与文件身份；
- 删除全部 sentinel，并确认 transaction、sandbox 和 host identity 未变化。

该证据明确不证明业务推理质量、API/output 契约、provider 可达性、host rollback 或
批量删除阈值。上述能力必须由各自的正式证据和 A/B 评测完成。

## 前置条件

必须先由正式 lifecycle 创建并保持一个健康的 `siq_analysis` run。正式 start 自身会要求
service、provider、broker、PostgreSQL/Milvus 边界全部通过；不得为了生成证据跳过这些
门禁。Exa 已延期、`8004/8006` 当前为 optional；没有 active transaction 时 runner 应返回：

```json
{"decision":"NO_GO","error_code":"formal_active_transaction_required","ok":false}
```

该结果是失败关闭，不会生成 raw、JSON 或 Markdown 证据。

## 执行

在正式 run 仍为 `running` 时执行：

```bash
python3 scripts/openshell/run_formal_filesystem_boundary.py \
  --run-id RUN_ID \
  --artifact-json artifacts/openshell/v0.6/formal-filesystem-boundary.sanitized.json \
  --artifact-markdown artifacts/openshell/v0.6/formal-filesystem-boundary.sanitized.md
```

成功时生成：

```text
var/openshell/proofs/formal-filesystem-boundary/RUN_ID.raw.json
artifacts/openshell/v0.6/formal-filesystem-boundary.sanitized.json
artifacts/openshell/v0.6/formal-filesystem-boundary.sanitized.md
```

三个文件均以 `0600` 排他创建，现有文件不会被覆盖。任一 schema、脱敏、identity 或
清理检查失败时，本次已安装文件会被删除。

## 完成门禁

`build_v06_readiness.py` 只允许这份正式证据声明代码、控制文件和固化数据只读，以及
analysis/session/memory 文件面可写；`readiness_effect=none` 的 provider-independent
probe 不能替代它。readiness 必须绑定证据相对路径和 SHA-256，并与正式 rollback、
delete 和 egress 证据共享同一 image、policy 和 mount 摘要。

`check_v06_completion.py` 还会复核：

- strict schema 与当前 schema 文件摘要；
- probe、lifecycle、transaction 和 runner 当前源码摘要；
- readiness 路径和 SHA-256 绑定；
- `artifacts/openshell` 工作树内容与 Git stage-zero blob 完全一致；
- 正常 memory 持久化证据独立有效。

## Git 发布

Raw receipt 含一次性 runtime identifiers，只保留在被忽略的 `var/openshell`。脱敏
JSON/Markdown 不含凭据、路径、Prompt、请求正文或 runtime identifiers，登记到
`artifacts/openshell/tracked-artifacts.json` 并通过 staged secret scan 后可以提交 Git。
