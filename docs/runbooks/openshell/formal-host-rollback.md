# 正式 OpenShell Host Rollback 证据

本流程只接受一个正在运行的正式 `siq_analysis` transaction。它不会从 canary、wide pilot、
手写 JSON、测试结果或已经停止的历史 transaction 推断 rollback 成功。

## 契约

`run_formal_host_rollback.py capture` 在执行回滚前固定并验证以下身份：

- transaction v2 为 `running`，五类 resource 均有精确 intent/receipt；
- sandbox、Docker container、manifest、候选镜像、policy 和原始 mount plan 相互绑定；
- live container 恰好具有 7 个业务 bind 和 5 个只读 control bind；
- normalized mount contract 来自严格验证后的原始 plan，只移除 run-id-specific runtime snapshot source；
- 宿主 Hermes baseline 与两次稳定读取的当前 receipt 完全相同。

随后 runner 调用正式 `rollback_to_host.sh`，不直接实现第二套清理逻辑。capture 只有在 transaction
进入 `stopped + rollback_to_host`、active pointer 消失、sandbox/container/forward listener/临时身份
全部清理、宿主 receipt 再次完全相同，且 host Publisher 已实际返回 `published` 后，才写入
owner-only raw receipt。Publisher `deferred` 不影响业务主产物，但不能作为正式 rollback GO 证据。

`publish` 再从当前 terminal journal 和宿主状态重验 raw receipt，并绑定 lifecycle、transaction、
mount projector、runner、wrapper 和 schema 的当前 SHA-256；公开 cleanup 契约同时保留经重验的
Publisher lifecycle receipt SHA-256 投影。失败时不生成 sanitized JSON/Markdown。

## 串行执行

先按 `siq-analysis-lifecycle.md` 启动正式 transaction，并在 rollback 前完成需要共享同一 runtime
provenance 的 filesystem、egress、audit 或 normal-delete 采集。然后执行：

```bash
cd /home/maoyd/siq-research-engine
RUN_ID=formal-rollback-20260716

python3 scripts/openshell/run_formal_host_rollback.py capture \
  --run-id "$RUN_ID" \
  --timeout 180

python3 scripts/openshell/run_formal_host_rollback.py publish \
  --raw-receipt "var/openshell/proofs/formal-host-rollback/$RUN_ID.raw.json" \
  --artifact-json artifacts/openshell/v0.6/formal-host-rollback.sanitized.json \
  --artifact-markdown artifacts/openshell/v0.6/formal-host-rollback.sanitized.md
```

两个命令必须在下一次正式 start 之前串行完成。capture 成功后 runtime 已回到 host；不要再调用
普通 stop 来补写证据。若 capture 返回失败，先按 lifecycle runbook 恢复 transaction，不能手写 raw
或 sanitized 文件。

## 不代表的结论

该证据证明身份不变和清理闭环，不代表默认生产流量已经切到 OpenShell，也不替代 A/B、质量门、
egress、structured audit 或人工评审。它也不会单独声明 API 与输出路径未变化；该结论只能由这里的
真实 Publisher `published` receipt 与后续有效的 route/A-B API、SSE、terminal 和质量证据组合派生。
