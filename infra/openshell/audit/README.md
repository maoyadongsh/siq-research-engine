# OpenShell 审计证据流水线

本目录说明 T8 审计聚合和竞赛证据导出 workflow。这里不包含运行态审计记录或已导出证据。

## 组件

```text
scripts/openshell/security_audit.py
  写入固定 siq.openshell.audit.v1 运行态记录

scripts/openshell/aggregate_security_audit.py
  只读取显式命名的 JSONL 文件，并生成安全聚合 JSON

scripts/openshell/export_sanitized_evidence.py
  只读取显式命名的 JSON/Markdown 文件，并在显式 output root 下写入成对
  *.sanitized.json 和 *.sanitized.md 文件

scripts/openshell/check_sanitized_artifacts.py
  在导出成功前校验 exporter 产生的每个文件
```

没有脚本会隐式扫描 `var/openshell`、用户 home 或日志目录。审计输入不接受 shell glob 或目录。调用方必须为每个普通输入文件单独提供 `--input` 参数。symlink 输入、输出 root 或父目录都会失败关闭。

## 聚合指标

聚合 schema 是 `siq.openshell.audit-summary.v1`。它会按 `siq.openshell.audit.v1` 的精确字段集合和值约束校验每条 source record，然后输出：

- policy deny 计数；
- audit-only 计数；
- sandbox start 失败；
- tool operation 计数、失败计数和失败率；
- external upload 阻断；
- immutable path write 阻断；
- `runtime.route` gateway 样本的 P50/P95 时长；
- 按 decision、operation class、profile、policy digest 和 deny error/rule ID 的计数。

输入文件名、输入路径、sandbox ID、run ID 和 session ID 不复制到 summary。source 文件只以 SHA-256 digest 和文件数量表示。P50/P95 对排序后的毫秒样本做线性插值。Tool operation 是目标 scope 投影为 `tool`/`tool.*` 或错误码以 `tool_` 开头的记录。Upload 阻断是固定上传或传输 rule/error ID 拒绝的 `network.request` 记录。Sandbox start 失败是使用固定 start scope/error 约定的 `sandbox.lifecycle` 拒绝记录。

显式路径示例：

```bash
python scripts/openshell/aggregate_security_audit.py \
  --input /explicit/review-copy/audit-part-1.jsonl \
  --input /explicit/review-copy/audit-part-2.jsonl \
  --output /explicit/review-work/audit-summary.json
```

示例路径只是占位符。自动化测试或 CI 不应指向真实运行态审计状态。

## 脱敏证据导出

exporter 会移除 API 凭据、token、Authorization/cookie 数据、DSN、用户 home 数据、Prompt/用户输入正文、请求正文和附件正文等敏感 JSON key 和 Markdown section。它还会删除单次运行身份字段（sandbox/container/probe ID、nonce digest、sentinel name、run-specific policy 或 mount-plan path）；这些字段对本地清理有用，但对提交聚合证据没有评审价值。文本中的 credential URL、private-key marker、bearer 值、home 引用和 POSIX/Windows 绝对机器路径会被脱敏。它保留 profile、rule/error ID、decision、latency、success/failure metrics、quality score、version 和 policy/mount digest 等评审安全值。

每个显式输入都会产生一对文件：

```text
<name>.sanitized.json
<name>.sanitized.md
```

已有输出永不覆盖。命名冲突会在写入前失败。所有文件创建后，exporter 会用精确输出文件列表调用 `check_sanitized_artifacts.scan_paths`。任何发现都会移除本次调用创建的所有文件并返回非零状态。

```bash
python scripts/openshell/export_sanitized_evidence.py \
  --input /explicit/review-work/audit-summary.json \
  --input /explicit/review-work/quality-summary.md \
  --output-root /explicit/review-work/sanitized
```

exporter 不修改 Git ignore 规则或 tracked artifact manifest。提交前，reviewer 必须检查脱敏文件对并运行 tracked-state 检查。脱敏审计和 operational log bundle 可以作为可发布竞赛证据。原始 JSONL、未脱敏 gateway logs、traces、prompts、attachments 和 session databases 绝不能提交。

## 单元测试

所有测试使用生成的临时 fixture，绝不打开真实运行日志：

```bash
PYTHONPATH=. pytest -q scripts/openshell/tests/test_audit_evidence_pipeline.py
PYTHONPATH=. pytest -q scripts/openshell/tests/test_security_audit.py
PYTHONPATH=. pytest -q scripts/openshell/tests/test_check_sanitized_artifacts.py
```
