# OpenShell NO_GO 到 GO 发布差距矩阵

状态快照：2026-07-18

本文件把 `scripts/openshell/check_v06_completion.py` 的正式完成门禁拆成可执行的发布工作单。它不是新的门禁，也不允许人工跳过门禁；最终发布判定仍只来自：

```bash
python3 scripts/openshell/check_v06_completion.py --json --require-go ...
```

## 目标口径

第一阶段只申请 `siq_analysis` 的 Limited GO，不申请 OpenShell 全域生产切流。

允许范围：

- profile 固定为 `siq_analysis`；
- 只覆盖有效单公司上下文触发的公司级 OpenShell sandbox；
- 同一前端对话内同公司复用 sandbox generation；
- 同一前端对话切换公司时生成隔离 generation；
- 请求结束后租约归零，空闲 TTL 后销毁；
- Host 仍是默认运行面，OpenShell GO 表示可以进入受控生产灰度，不表示已经全量切流；
- 完成门禁仍按 formal lifecycle 与正式 A/B 契约采证，Host `/v1/runs` 为 `18651`，OpenShell formal endpoint 为 `28651`；
- `cutover_performed` 必须仍为 `false`，真正切流另走发布操作。

不允许范围：

- 不覆盖任意 profile、任意工具或任意应用中心任务；
- 不覆盖多公司比较 sandbox；
- 不宣称采用 NemoClaw / NemoHermes 官方路径；
- 不把当前公司 pool 的动态转发端口直接替代 formal A/B 的 `28651` 固定端点；
- 不把 canary、wide pilot、observe PoC 或前端 smoke 当作正式 production evidence。

## 当前快照

当前直接运行：

```bash
python3 scripts/openshell/check_v06_completion.py --json
```

得到：

```text
decision=NO_GO
passed_count=0/13
default_runtime=host
cutover_performed=false
```

当前 blocker 为：

```text
api_output_contract_evidence_missing
docs_or_audit_evidence_missing
formal_ab_missing_or_failed
formal_delete_guard_evidence_missing
formal_host_rollback_missing
human_review_missing
immutable_write_evidence_missing
normal_write_evidence_missing
quality_ab_missing_or_failed
reproducible_evidence_missing
service_or_provider_preflight_no_go
tracked_state_scan_missing_or_failed
upload_guard_missing
```

需要特别注意三点：

- `artifacts/openshell/v0.6/readiness.json` 生成于 `2026-07-16T14:43:51Z`，按完成门禁的 24 小时新鲜度要求已经不能作为最新发布证据。
- `artifacts/openshell/v0.6/service-preflight.sanitized.json` 自身是 `GO`，但 completion 仍报 `service_or_provider_preflight_no_go`，原因是 readiness 仍为 `NO_GO`，且正式证据、Git index 绑定和运行态字段未整体进入 GO。
- `var/openshell/eval/siq-analysis-20260717-formal11/summary.json` 已有正式 A/B 尝试，但 `quality_gate.passed=false`，只能作为诊断输入，不能作为 GO 证据。

`formal11` 的主要失败项：

```text
host_baseline_contract_failure
openshell_contract_failure
host_primary_route_telemetry_incomplete
openshell_primary_route_telemetry_incomplete
host_primary_route_not_effective
openshell_primary_route_not_effective
openshell_task_success_rate_below_absolute_floor
openshell_answer_citation_rate_below_absolute_floor
openshell_hallucination_block_rate_below_absolute_floor
openshell_evidence_coverage_below_absolute_floor
openshell_report_completeness_below_absolute_floor
openshell_timeout_rate_above_absolute_ceiling
hallucination_block_rate_regression
total_p95_regression
```

对应关键指标：

| 指标 | Host | OpenShell | GO 口径 |
| --- | ---: | ---: | --- |
| task_success_rate | 0.833333 | 0.880952 | OpenShell 绝对下限 0.95，且不低于 Host |
| answer_citation_rate | 0.833333 | 0.933333 | OpenShell 绝对下限 0.95，且不低于 Host |
| numeric_accuracy | 1.0 | 1.0 | OpenShell 绝对下限 0.95，且不低于 Host |
| hallucination_block_rate | 1.0 | 0.916667 | OpenShell 绝对下限 0.95，且不低于 Host |
| evidence_coverage | 0.833333 | 0.933333 | OpenShell 绝对下限 0.95，且不低于 Host |
| report_completeness | 0.880952 | 0.904762 | OpenShell 绝对下限 0.95，且不低于 Host |
| timeout_rate | 0.047619 | 0.047619 | OpenShell 必须为 0 |
| policy_false_positive_rate | 0.0 | 0.0 | OpenShell 必须为 0 |
| total_p95 | 106485.665 ms | 121149.352 ms | OpenShell / Host 不能超过 1.10，当前 1.137706 |

## GO 验收定义

`NO_GO` 进入 `GO` 的最小机器验收是：

```bash
python3 scripts/openshell/check_v06_completion.py \
  --json \
  --require-go \
  --ab-summary artifacts/openshell/v0.6/formal-ab-summary.sanitized.json \
  --ab-prerequisites var/openshell/eval/EVALUATION_ID/prerequisites.json \
  --review-record artifacts/openshell/v0.6/architecture-security-review.sanitized.json
```

通过时必须同时满足：

- `decision=GO`；
- `passed_count=13/13`；
- `default_runtime=host`；
- `cutover_performed=false`；
- readiness、service preflight、formal evidence、A/B summary、fallback drill、review record 的 SHA-256 互相绑定；
- 可发布 evidence 已进入 `artifacts/openshell/tracked-artifacts.json` 并与 Git index stage-zero blob 完全一致；
- 私有 prerequisites 和 raw receipts 留在 `var/openshell/`，只通过 digest 被公开证据引用。

## 差距矩阵

| Gate check | 当前 blocker | GO 要求 | 推进动作 |
| --- | --- | --- | --- |
| `real_host_openshell_ab` | `formal_ab_missing_or_failed` | 同一 evaluation 的 sanitized A/B summary、private prerequisites、raw results 和 provenance 互相绑定，summary schema 为当前版本，质量门无 failure reasons，且 fallback drill 与同一候选 runtime provenance 兼容。 | 先修复 `formal11` 暴露的合同失败、主路由遥测、超时和质量指标；重新准备 dataset 和 prerequisites；运行正式 Host/OpenShell A/B；把通过的 summary 发布为 `artifacts/openshell/v0.6/formal-ab-summary.sanitized.json`，保留 `var/openshell/eval/EVALUATION_ID/prerequisites.json`。 |
| `api_and_output_paths_unchanged` | `api_output_contract_evidence_missing` | readiness 中 `contracts.api_and_output_paths_unchanged=true`，且由正式 host rollback publisher 与通过的 A/B 共同支撑。 | 用正式 lifecycle transaction 生成 host rollback evidence，要求 `publisher_index_published=true`；A/B 通过后重新 `build_v06_readiness.py`。 |
| `immutable_write_denials` | `immutable_write_evidence_missing` | 正式 filesystem boundary 证明项目代码、配置、Prompt、workflow 和固化来源只读；证据与 rollback、delete、egress 共享 image、policy、mount 摘要。 | 启动正式 `siq_analysis` transaction 后运行 `run_formal_filesystem_boundary.py`；不要用 provider-independent probe 替代。 |
| `normal_analysis_and_memory_writes` | `normal_write_evidence_missing` | 正式 filesystem boundary 证明 `analysis/`、runtime session、runtime memory 可写；宿主 memory write evidence 有效且绑定 readiness。 | 重新跑 memory write probe 和 evidence builder；在同一发布窗口内生成 formal filesystem evidence；用新 `generated_at` 刷新 readiness。 |
| `services_models_search_fallback` | `service_or_provider_preflight_no_go` | service preflight v2 为 `GO`；provider inventory、broker status、required providers、`8007/8013` 可达、PostgreSQL/Milvus 边界和 readiness runtime 字段一致。 | 重新导出 provider inventory、broker status 和 service preflight；确认 `8004/8006` 仍按 optional warning 处理；完成其余 formal evidence 后刷新 readiness。 |
| `unknown_file_upload_denied` | `upload_guard_missing` | host egress component、formal sandbox egress evidence 和 formal structured audit 同时有效；直接上传、直连网络、metadata 等负向路径被拒绝。 | 先刷新 host egress boundary；在正式 transaction 内运行 `run_formal_egress_audit.py`，生成 `formal-egress-sandbox` 与 `formal-structured-audit` 两类 evidence。 |
| `quality_gate` | `quality_ab_missing_or_failed` | A/B summary 的 `quality_gate.passed=true` 且 `failure_reasons=[]`。 | 针对 `formal11` 修复质量和遥测问题；至少 10 个 case、3 次 repetition、每臂至少 30 次执行；OpenShell 质量绝对线达到 0.95，timeout 和 policy false positive 为 0，P95 不超过 Host 1.10 倍。 |
| `formal_host_rollback` | `formal_host_rollback_missing` | 正式 rollback capture/publish 证明回滚前后 host receipt 完全一致，sandbox、forward、guard、临时身份全部清理。 | 在承载 filesystem/egress/audit/normal delete 的正式 transaction 末尾执行 `run_formal_host_rollback.py capture` 和 `publish`。 |
| `docs_and_audit_complete` | `docs_or_audit_evidence_missing` | required runbooks 在 Git index 中有稳定 digest；readiness 绑定该 digest；service/report/audit/evidence 一致；formal structured audit 有效。 | 更新并暂存 runbook；生成 formal structured audit；刷新 tracked artifact manifest；重新构建 readiness。 |
| `human_architecture_security_review` | `human_review_missing` | 人工评审 JSON schema 为 `siq.openshell.architecture-security-review.v1`，`decision=approved`，reviewer 非占位，八项 checklist 全为 true，证据 digest 与当前 gate 读取值逐项一致。 | 在所有机器证据和 readiness 完成后，按 `review-record-template.md` 生成脱敏 JSON；评审只能绑定最终 digest，后续重建 evidence 后必须重签。 |
| `reproducible_sanitized_evidence` | `reproducible_evidence_missing` | baseline、readiness、service、memory、formal evidence、A/B summary、fallback drill 都可重复读取、脱敏通过、Git index 绑定通过。 | 将可发布 evidence 放入 `artifacts/openshell/`；私有 receipt 留在 `var/openshell/`；运行 manifest refresh、sanitizer、tracked-state 和 staged secret scan。 |
| `tracked_state_secret_scan` | `tracked_state_scan_missing_or_failed` | `check_tracked_state.py --require-allowlist` 无 findings，manifest 与 index 一致，不含凭据或业务正文。 | 清理或登记新增 evidence；修正 manifest 摘要；避免提交 raw audit、session DB、key、nonce、prompt 或业务正文。 |
| `formal_delete_guard_evidence` | `formal_delete_guard_evidence_missing` | 三类高危删除 transaction 和一个 normal cleanup transaction 全部通过，publish 生成正式 delete guard evidence；证据与同一候选 image/policy/mount provenance 兼容。 | 按 `formal-delete-guard.md` 串行运行 prepare、三类 high-risk capture、normal cleanup、host rollback、publish；禁止并行和手写 JSON。 |

## 推荐执行顺序

1. 冻结范围和基线：确认本次只申请 `siq_analysis` Limited GO，选择固定公司集合、commit、OpenShell `0.0.83`、Hermes commit、provider 集合和运行环境。
2. 修复 A/B 已知问题：优先处理 `formal11` 中的 contract failure、primary route telemetry、timeout、P95 和 0.95 质量线问题。
3. 刷新轻量前置证据：重新导出 provider inventory、broker status、service preflight、memory write、host egress component，并确认 `8007/8013` 在线。
4. 创建正式 transaction：使用 `scripts/openshell/run_hermes_gateway.sh --profile siq_analysis ...`，在同一候选 runtime 下采集 filesystem、formal egress、formal audit 和 normal cleanup。
5. 执行正式 rollback：用 `run_formal_host_rollback.py capture/publish` 生成 host rollback evidence。
6. 完成 delete guard：串行跑三类高危删除和 normal cleanup，发布 delete guard evidence。
7. 重新跑正式 A/B：生成通过质量门的 raw results、summary 和 prerequisites。
8. 执行 fallback drill：在正常 A/B 已 GO 且没有活跃正式 transaction 时运行独立 fallback drill，并发布 sanitized evidence。
9. 预暂存正式证据：把可提交 evidence 放进 `artifacts/openshell/`，刷新 `tracked-artifacts.json`，暂存除新 readiness 外的正式证据。
10. 构建新 readiness：用显式 `--generated-at`、`--output artifacts/openshell/v0.6/readiness.json --replace` 和最终 A/B 路径运行 `build_v06_readiness.py`。
11. 发布证据索引：再次刷新 `tracked-artifacts.json`，暂存新 readiness 和 manifest，并运行 tracked-state、staged secret 和大文件检查。
12. 人工评审：评审人基于最终 digest 生成 `architecture-security-review.sanitized.json`，八项 checklist 全部为 true。
13. 运行最终门禁：带 `--ab-summary`、`--ab-prerequisites` 和 `--review-record` 运行 `check_v06_completion.py --require-go`。

## 发布前命令骨架

以下是顺序骨架，具体 `RUN_ID`、`COMPANY`、`EVALUATION_ID` 必须在发布窗口内唯一。

```bash
python3 scripts/openshell/export_provider_inventory.py
python3 scripts/openshell/export_broker_status.py
python3 scripts/openshell/check_siq_services.py \
  --host-alias 127.0.0.1 \
  --proof-file var/openshell/proofs/service-security.json \
  --milvus-proof-file var/openshell/proofs/milvus-write-protection.json \
  --output artifacts/openshell/v0.6/service-preflight.sanitized.json \
  --markdown-output artifacts/openshell/v0.6/service-preflight.sanitized.md \
  --replace \
  --json

python3 scripts/openshell/run_memory_write_probe.py --api-pid API_PID
python3 scripts/openshell/build_memory_write_evidence.py --project-root "$PWD"
python3 scripts/openshell/run_egress_boundary_proof.py --project-root "$PWD"

scripts/openshell/run_hermes_gateway.sh \
  --profile siq_analysis \
  --market cn \
  --company "$COMPANY" \
  --run-id "$RUN_ID"

python3 scripts/openshell/run_formal_filesystem_boundary.py \
  --run-id "$RUN_ID" \
  --artifact-json artifacts/openshell/v0.6/formal-filesystem-boundary.sanitized.json \
  --artifact-markdown artifacts/openshell/v0.6/formal-filesystem-boundary.sanitized.md

python3 scripts/openshell/run_formal_egress_audit.py \
  --project-root "$PWD" \
  --run-id "$RUN_ID"

python3 scripts/openshell/run_formal_host_rollback.py capture --run-id "$RUN_ID"
python3 scripts/openshell/run_formal_host_rollback.py publish \
  --raw-receipt "var/openshell/proofs/formal-host-rollback/$RUN_ID.raw.json" \
  --artifact-json artifacts/openshell/v0.6/formal-host-rollback.sanitized.json \
  --artifact-markdown artifacts/openshell/v0.6/formal-host-rollback.sanitized.md
```

正式 A/B 和 fallback drill 需要单独发布窗口：

```bash
HOST_RUNS_URL=http://localhost:18651/v1/runs
OPENSHELL_RUNS_URL=http://localhost:28651/v1/runs
HOST_KEY_FILE=var/openshell/eval/"$EVALUATION_ID"/host.key
OPENSHELL_KEY_FILE=/path/to/formal-openshell-api.key
DATASET=var/openshell/eval/"$EVALUATION_ID"/dataset.json
PROVENANCE=var/openshell/eval/"$EVALUATION_ID"/provenance.json
PREREQUISITES=var/openshell/eval/"$EVALUATION_ID"/prerequisites.json
SUMMARY=var/openshell/eval/"$EVALUATION_ID"/summary.json

python3 scripts/openshell/check_siq_analysis_ab_prerequisites.py \
  --host-runs-url "$HOST_RUNS_URL" \
  --openshell-runs-url "$OPENSHELL_RUNS_URL" \
  --host-api-key-file "$HOST_KEY_FILE" \
  --openshell-api-key-file "$OPENSHELL_KEY_FILE" \
  --dataset "$DATASET" \
  --evaluation-id "$EVALUATION_ID" \
  --provenance "$PROVENANCE" \
  --provider-inventory var/openshell/proofs/provider-inventory.json \
  --service-report artifacts/openshell/v0.6/service-preflight.sanitized.json \
  --broker-report var/openshell/proofs/broker-status.json \
  --json \
  --require-go \
  --output "$PREREQUISITES"

python3 scripts/openshell/run_siq_analysis_ab_eval.py \
  --dataset "$DATASET" \
  --host-runs-url "$HOST_RUNS_URL" \
  --openshell-runs-url "$OPENSHELL_RUNS_URL" \
  --host-api-key-file "$HOST_KEY_FILE" \
  --openshell-api-key-file "$OPENSHELL_KEY_FILE" \
  --evaluation-id "$EVALUATION_ID" \
  --prerequisites "$PREREQUISITES" \
  --confirm-live-evaluation

python3 scripts/openshell/publish_siq_analysis_ab_summary.py \
  --evaluation-id "$EVALUATION_ID" \
  --replace

python3 scripts/openshell/run_siq_analysis_fallback_drill.py \
  --evaluation-id "$EVALUATION_ID" \
  --company "$COMPANY" \
  --dataset "$DATASET" \
  --normal-summary "$SUMMARY" \
  --prerequisites "$PREREQUISITES" \
  --provenance "$PROVENANCE" \
  --confirm-live-drill
```

完成证据发布和 readiness 构建：

```bash
# 先让 readiness builder 看到已发布、已暂存的正式 evidence。
python3 scripts/openshell/build_tracked_artifact_manifest.py \
  --project-root "$PWD" \
  --refresh

git add artifacts/openshell/tracked-artifacts.json artifacts/openshell/v0.6

python3 scripts/openshell/build_v06_readiness.py \
  --generated-at YYYY-MM-DDThh:mm:ssZ \
  --ab-summary artifacts/openshell/v0.6/formal-ab-summary.sanitized.json \
  --ab-prerequisites var/openshell/eval/"$EVALUATION_ID"/prerequisites.json \
  --output artifacts/openshell/v0.6/readiness.json \
  --replace

# readiness 写出后再次绑定 manifest 和 Git index。
python3 scripts/openshell/build_tracked_artifact_manifest.py \
  --project-root "$PWD" \
  --refresh

git add artifacts/openshell/tracked-artifacts.json artifacts/openshell/v0.6

python3 scripts/openshell/check_tracked_state.py \
  --repo-root "$PWD" \
  --require-allowlist \
  --json

python3 scripts/openshell/check_v06_completion.py \
  --json \
  --require-go \
  --ab-summary artifacts/openshell/v0.6/formal-ab-summary.sanitized.json \
  --ab-prerequisites var/openshell/eval/"$EVALUATION_ID"/prerequisites.json \
  --review-record artifacts/openshell/v0.6/architecture-security-review.sanitized.json
```

## 不能走的捷径

- 不能把前端 smoke、pool probe、canary 或 wide pilot 标记为 formal evidence。
- 不能手写 sanitized JSON 代替 raw receipt 的 publish 流程。
- 不能用旧 readiness 或旧 A/B summary 绑定新代码、新镜像或新 generation lifecycle。
- 不能为了 A/B 通过而放宽文件、网络、上传或删除策略。
- 不能把 OpenShell `GO` 表述为 NemoClaw 官方支持路径。
- 不能在 review 完成后重建 evidence，却继续复用旧 review digest。

## 决策点

进入正式执行前需要人工确认三件事：

- 本次 Limited GO 的公司集合和灰度比例；
- A/B dataset 是否足以代表二级市场投研主路径；
- 人工评审责任人和接受的残余风险，尤其是非 NemoClaw 路径下的自研控制面责任。
