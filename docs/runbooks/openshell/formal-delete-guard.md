# 正式 OpenShell Delete Guard 证据

本流程使用四个彼此不同的正式 `siq_analysis` transaction：`shell_rm`、`python_shutil`、`node_fs`
各一个高危删除 transaction，另一个 transaction 验证正常任务文件生命周期。一次高危触发必然终止
对应 sandbox，因此禁止把三类路径拼装成一个 transaction，也禁止从 canary 或离线测试推断正式结果。

## 合成 fixture 边界

`prepare` 取得与 lifecycle start/recover 相同的 maintenance lock，并且仅在没有 active transaction 时，
在目标公司现有 `analysis/` 下创建一个唯一、owner-only、
digest-bound 的 `.siq-openshell-delete-proof-SUITE_ID` 目录。它不创建公司根，不复用、不覆盖、不删除
真实业务文件。

fixture 包含：

- 每个高危机制 501 个删除目标和 500 个保留目标；
- 正常清理 3 个预存在目标；
- 所有内容均为固定 ASCII 合成 marker，不含凭据或业务正文。

prepare 先记录 fixture 外原始 analysis 树摘要。每次 capture 前后以及最终删除 fixture 后都要求该摘要
完全不变。fixture 内出现额外文件、符号链接、硬链接、特殊文件或内容漂移都会失败关闭。

## 正常权限证明

`normal_cleanup` 在 sandbox 内验证缺失叶目录由任务创建，并完整执行 `mkdir`、create、write、overwrite、
rename、少量 delete 和递归清理。guard 必须不触发且 sandbox 必须保持健康。随后同一个 transaction
必须通过正式 host rollback runner 进入终态。

高危三类路径各删除 501 个启动前已进入 deletion snapshot 的文件。结果必须是
`deletion_count_gt_500`、sandbox 被身份校验后终止、501 个文件全部恢复、零缺失。shell、Python 和
Node 最终都由同一 recursive filesystem event guard 判定，不依赖命令文本匹配。

## 串行执行

```bash
cd /home/maoyd/siq-research-engine
SUITE_ID=delete-v06-20260716
COMPANY='600104-上汽集团'
SHELL_RUN=formal-delete-shell-20260716
PYTHON_RUN=formal-delete-python-20260716
NODE_RUN=formal-delete-node-20260716
NORMAL_RUN=formal-delete-normal-20260716

python3 scripts/openshell/run_formal_delete_guard.py prepare \
  --suite-id "$SUITE_ID" \
  --market cn \
  --company "$COMPANY"
```

对三个高危机制分别执行一次正式 start 和 capture。每个 `RUN_ID` 必须唯一；capture 会等待 guard
及 transaction recovery 完成，不要并行运行：

```bash
scripts/openshell/run_hermes_gateway.sh \
  --profile siq_analysis --market cn --company "$COMPANY" --run-id "$SHELL_RUN"
python3 scripts/openshell/run_formal_delete_guard.py capture \
  --suite-id "$SUITE_ID" --mechanism shell_rm --run-id "$SHELL_RUN"

scripts/openshell/run_hermes_gateway.sh \
  --profile siq_analysis --market cn --company "$COMPANY" --run-id "$PYTHON_RUN"
python3 scripts/openshell/run_formal_delete_guard.py capture \
  --suite-id "$SUITE_ID" --mechanism python_shutil --run-id "$PYTHON_RUN"

scripts/openshell/run_hermes_gateway.sh \
  --profile siq_analysis --market cn --company "$COMPANY" --run-id "$NODE_RUN"
python3 scripts/openshell/run_formal_delete_guard.py capture \
  --suite-id "$SUITE_ID" --mechanism node_fs --run-id "$NODE_RUN"
```

正常权限 transaction 可同时承载正式 filesystem/egress/audit 采集。完成这些 attach-only probe 后，
先采集 normal case，再使用 host rollback runner 收敛同一个 transaction：

```bash
scripts/openshell/run_hermes_gateway.sh \
  --profile siq_analysis --market cn --company "$COMPANY" --run-id "$NORMAL_RUN"
python3 scripts/openshell/run_formal_delete_guard.py capture \
  --suite-id "$SUITE_ID" --mechanism normal_cleanup --run-id "$NORMAL_RUN"

python3 scripts/openshell/run_formal_host_rollback.py capture --run-id "$NORMAL_RUN"
python3 scripts/openshell/run_formal_host_rollback.py publish \
  --raw-receipt "var/openshell/proofs/formal-host-rollback/$NORMAL_RUN.raw.json" \
  --artifact-json artifacts/openshell/v0.6/formal-host-rollback.sanitized.json \
  --artifact-markdown artifacts/openshell/v0.6/formal-host-rollback.sanitized.md
```

四个 transaction 全部终态后发布 delete evidence：

```bash
python3 scripts/openshell/run_formal_delete_guard.py publish \
  --suite-id "$SUITE_ID" \
  --artifact-json artifacts/openshell/v0.6/formal-delete-guard.sanitized.json \
  --artifact-markdown artifacts/openshell/v0.6/formal-delete-guard.sanitized.md
```

publish 会取得同一个 maintenance lock，重验四份 journal、guard outcome、snapshot、fixture、宿主
identity 和共同的 image/policy/normalized mount provenance。清理前先 fsync owner-only cleanup intent，
随后逐个验证并删除四个 deletion snapshot 和唯一合成 fixture，每完成一个资源就原子持久化进度；
全部清理且 fixture 外树摘要不变后才写 terminal cleanup receipt。进程或磁盘故障后可直接重跑 publish，
runner 会从 durable state 幂等恢复。任一失败都不会生成 sanitized evidence；不要用手写 JSON、旧
canary receipt 或人工删除 fixture 的方式绕过。
