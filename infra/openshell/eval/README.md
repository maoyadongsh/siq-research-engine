# `siq_analysis` Host/OpenShell A/B 评测

本目录只保存可提交的评测契约与说明。真实数据集、API key、原始响应和运行结果不得放在本目录；运行结果只能写入 `var/openshell/eval/<evaluation-id>/`。

评测器不会修改 `start_all.sh`、gateway、policy 或运行时路由，也不会自动切流。质量门禁失败时只返回非零退出码并生成脱敏摘要。

## 数据集契约

数据集必须符合 `infra/openshell/schemas/siq-analysis-ab-dataset.schema.json`，并使用固定版本：

```json
{
  "schema_version": "siq.openshell.siq-analysis-ab-dataset.v1",
  "profile": "siq_analysis",
  "model": "pinned-model-alias",
  "temperature": 0.1,
  "instructions": "same instructions for both arms",
  "repetitions": 3,
  "run_timeout_seconds": 600,
  "cases": [
    {
      "case_id": "case-001",
      "input": "private evaluation input",
      "history": [],
      "expectations": {
        "numeric": [
          {"expectation_id": "revenue", "value": 42.0, "absolute_tolerance": 0.01}
        ],
        "citations": ["[SOURCE-001]"],
        "evidence_ids": ["EVIDENCE-001"],
        "required_sections": ["Executive Summary"],
        "abstention_required": false,
        "abstention_markers": [],
        "required_tools": ["pg_query"],
        "fallback_expected": null,
        "policy_denial_expected": false
      }
    }
  ]
}
```

上例只展示一个 case 的字段结构；正式数据集至少包含 10 个 case，并至少运行 3 次 repetition。每臂至少产生 30 次执行，citation、numeric、hallucination、evidence、tool 和 report completeness 各至少有 10 个有效分母，正常路径 policy 样本至少 20 个；任一分母不足均按统计证据不足返回 `NO_GO`。

Prompt、输入、history 和预期文本都视为私有评测数据。评测器只持久化 `case_id`、case/payload/output digest、计数、状态、耗时和分数，不持久化正文。

`fallback_expected` 的含义：

- `true`：该 case 专门验证 fallback，终态必须显示 fallback 已启用。
- `false`：该 case 不应 fallback，发生 fallback 会使 OpenShell 质量门禁失败。
- `null`：该 case 不计入 fallback 成功率，也不判断是否意外 fallback。

正式正常路径 A/B 的所有 case 都必须使用 `fallback_expected: null`。fallback 不再通过正常
A/B 内的故障注入采样，而由独立 formal fallback drill 验证；正常数据集仍必须包含
`policy_denial_expected: false` 的业务 case，保证 policy false-positive 指标有有效分母。

## 运行前约束

1. 使用两个固定且不同的 loopback `/v1/runs` URL：host Hermes 必须为 `127.0.0.1:18651`，OpenShell Hermes 必须为 `127.0.0.1:28651`。
2. 两端必须使用两个不同的 API key 文件，且 key 内容也必须不同；文件权限必须为 `0600`，key 不得放在命令行或数据集中。
3. 固定两端同一 Hermes commit、profile、模型路由、工具版本和数据快照；Host 当前 editable `api_server.py`、`run_agent.py` 的摘要必须与候选镜像签名 build context 中对应文件完全相同。
4. 当前 Hermes `0.13.0` 接受请求中的 `temperature` 字段，但实际采样温度仍可能由 profile/provider 配置决定。正式 A/B 前必须独立核对两端 profile/provider 的有效温度相同；请求字段相同本身不是充分证明。
5. 用符合 `infra/openshell/schemas/siq-analysis-ab-provenance.schema.json` 的 `0600` provenance 文件分别记录两臂的 Hermes commit、profile、模型路由、工具和数据快照摘要；这些摘要必须一致。Host 臂还绑定 key receipt、PID/start ticks、executable、argv、systemd unit、launcher、editable 源文件和认证 capability 摘要；OpenShell 臂固定 image、policy、mount plan 和 runtime config 摘要。
6. 数据集和 key 文件不得放入 Git，也不得置于符号链接路径。原始 provenance 留在受控运行目录；不含路径、凭据或业务正文的 provenance 脱敏投影可以进入 tracked artifact manifest。
7. provider inventory、service preflight 和 broker status 必须在正式检查前重新导出。前置检查会把三个源文件的规范绝对路径、SHA-256、文件身份、生成时间和有效期写入 `siq.openshell.siq-analysis-ab-prerequisites.v3`；provider/service/broker 的最长有效期分别为 15 分钟、5 分钟和 60 秒。
8. prerequisite 生成时会认证读取 Host `/v1/capabilities`，但不保存响应正文，并要求 `run_runtime_metadata_v1=true`。正式评测器在任何模型请求前重新打开全部绑定源，再次复核 Host 进程身份、关键部署文件、Host URL/key 指纹和 capability。旧 `v1/v2` 报告、进程更换、进程启动后文件更新、代码不一致、过期或任一漂移均按配置错误终止，产生零次模型调用。
9. 多用户或多批次并发时，每个发起者必须使用唯一 `evaluation-id`；对应目录固定为 owner-only `0700`，文件为 `0600`。评测器在构造 Runs client 前预检该目录，并以排他创建写入结果；禁止多个作业复用同一 evaluation 目录。

示例：

```bash
OPEN_SHELL_RUN_ID=<formal-run-id>
OPEN_SHELL_KEY_FILE="var/openshell/siq-analysis/runs/${OPEN_SHELL_RUN_ID}/api.key"
test -f "$OPEN_SHELL_KEY_FILE"

install -d -m 700 var/openshell/eval/siq-analysis-20260715-01
python scripts/openshell/prepare_siq_analysis_ab_eval.py dataset \
  --evaluation-id siq-analysis-20260715-01 \
  --company-dir data/wiki/companies/600104-company \
  --case-plan /secure/siq-analysis-ab-case-plan.json

# 从 18651 的同一 listener 生成 host.key、host-key-receipt.json 和
# host-runtime-receipt.json；只执行 health/capability GET，不执行模型请求。
python scripts/openshell/prepare_siq_analysis_ab_eval.py host-key \
  --evaluation-id siq-analysis-20260715-01

# 必须在 host-key 之后执行；provenance 会立即复验收据并比较 Host/candidate 源码。
python scripts/openshell/prepare_siq_analysis_ab_eval.py provenance \
  --evaluation-id siq-analysis-20260715-01 \
  --run-manifest var/openshell/siq-analysis/runs/<run-id>/run.json

# 只读导出 broker 状态（不启停 broker，输出不含 PID）。
python scripts/openshell/export_broker_status.py
# 原件：var/openshell/proofs/broker-status.json（0600）；脱敏投影可进入 tracked artifact manifest

# 校验前置条件；除认证 capability GET 外不发出 Host/OpenShell 业务请求。
python scripts/openshell/check_siq_analysis_ab_prerequisites.py \
  --dataset var/openshell/eval/siq-analysis-20260715-01/dataset.json \
  --host-runs-url http://127.0.0.1:18651/v1/runs \
  --openshell-runs-url http://127.0.0.1:28651/v1/runs \
  --host-api-key-file var/openshell/eval/siq-analysis-20260715-01/host.key \
  --openshell-api-key-file "$OPEN_SHELL_KEY_FILE" \
  --evaluation-id siq-analysis-20260715-01 \
  --provenance var/openshell/eval/siq-analysis-20260715-01/provenance.json \
  --provider-inventory var/openshell/proofs/provider-inventory.json \
  --service-report var/openshell/proofs/ab-service-preflight.json \
  --broker-report var/openshell/proofs/broker-status.json \
  --output var/openshell/eval/siq-analysis-20260715-01/prerequisites.json \
  --json --require-go

python scripts/openshell/run_siq_analysis_ab_eval.py \
  --dataset var/openshell/eval/siq-analysis-20260715-01/dataset.json \
  --host-runs-url http://127.0.0.1:18651/v1/runs \
  --openshell-runs-url http://127.0.0.1:28651/v1/runs \
  --host-api-key-file var/openshell/eval/siq-analysis-20260715-01/host.key \
  --openshell-api-key-file "$OPEN_SHELL_KEY_FILE" \
  --evaluation-id siq-analysis-20260715-01 \
  --prerequisites var/openshell/eval/siq-analysis-20260715-01/prerequisites.json \
  --confirm-live-evaluation
```

`host.key`、两份 Host receipt、provenance 和 prerequisites 都是 `var/openshell/eval/<evaluation-id>/` 下的 `0600` 私有运行态，不提交。receipt 不含 key、Authorization、capability 正文或 argv 正文；只含本机身份、规范 URL 和摘要。`--output` 只接受固定 prerequisites 路径并原子写入；既有有效文件只有显式 `--replace` 才会替换，`--require-go` 遇到 `NO_GO` 不创建或替换文件。评测顺序按 case 和 repetition 交错，并在相邻 case 反转首发 arm，降低时间漂移造成的偏差。传给两端的 model、temperature、instructions、input、conversation history 和 session ID 完全一致。

## 输出与门禁

输出文件：

```text
var/openshell/eval/<evaluation-id>/raw-results.json
var/openshell/eval/<evaluation-id>/summary.json
```

目录权限为 `0700`，文件权限为 `0600`。两个文件都会经过 `check_sanitized_artifacts.py`；任意脱敏检查失败时会删除本次已生成文件。

private raw 使用 `siq.openshell.siq-analysis-ab-raw.v2`，summary 使用 `siq.openshell.siq-analysis-ab-summary.v3`；旧 raw v1 / summary v2 只能作为历史诊断证据，不能进入 publisher、completion、fallback 或正式业务回执。两者都保存 `prerequisites_path`（仓库相对路径）及 `prerequisites_sha256`，供 completion gate 精确绑定本次前置凭证。

摘要包含任务成功率、引用率、数字准确率、拒答/幻觉阻断率、证据覆盖率、工具最终成功率、报告完整率、TTFT/总时长 P50/P95、timeout rate、policy false positive rate、各指标实际分母和契约失败数。工具另行审计 attempt/success/failure/retry、曾失败后恢复和最终未恢复计数及对应 rate；这些重试/错误指标只报告及比较差值，不因次数高于 Host 直接产生 `NO_GO`。required tool 仍按最后一次 `tool.completed` 计分并保留相对不回退门禁，且不参与业务 `task_success`。

`0.95` 绝对线只约束 OpenShell 候选的 task、引用、数值、幻觉阻断、证据覆盖和报告完整性，不约束 Host 或工具指标；总时长 P95 比值不得超过 `1.10`，OpenShell 正常路径 policy false positive 与 timeout 必须为 `0`。正常 A/B 的 fallback 分母和 rate 固定为空，独立 formal fallback drill 提供该门禁证据。

退出码：

- `0`：质量门禁通过，仍只允许人工审核，不自动切流。
- `1`：评测完成但门禁失败，不切流。
- `2`：配置、契约或产物安全校验失败，不切流。

生成参赛用脱敏证据前，显式调用 T8 导出器：

```bash
mkdir -m 700 /secure/siq-ab-evidence
python scripts/openshell/export_sanitized_evidence.py \
  --input var/openshell/eval/siq-analysis-20260715-01/summary.json \
  --output-root /secure/siq-ab-evidence
```

导出物仍需通过精确 Git allowlist 审核后才能提交。

最终完成审计必须同时提供 `summary.json` 和同一 evaluation 的
`prerequisites.json`，且 readiness 要绑定两者的相对路径和 SHA-256；缺少
provenance 前置报告时，单独的质量摘要不能进入 `GO`。

## 离线测试

测试使用两个本地 `ThreadingHTTPServer` 模拟完整 create/SSE/stop 协议，不会访问真实模型、Hermes 或 OpenShell：

```bash
pytest -q scripts/openshell/tests/test_siq_analysis_ab_eval.py
```
