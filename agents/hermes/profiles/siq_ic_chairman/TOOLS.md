# TOOLS.md - SIQ 投委会主席

## 可用工具

### 信息查阅
- `web_search` / `tavily_search` — 宏观验证、政策确认
- `read` — 读取专家报告和项目文件

### 决策支持
- Deal OS API — 通过 R1.5 / R4 workflow endpoints 提交裁决和决策合同
- `write` / `edit` — 仅用于草稿或人工审阅材料；正式 artifact 由 Deal OS API 写入

### R1.5 主席裁决 API
- 读取任务：`GET /api/deals/{deal_id}/workflow/disputes/chairman-task`
- 批量提交：`POST /api/deals/{deal_id}/workflow/disputes/chairman-rulings`
- 单条提交：`POST /api/deals/{deal_id}/workflow/disputes/{dispute_id}/ruling`
- deterministic 草案：`POST /api/deals/{deal_id}/workflow/generate-dispute-rulings`

主席只负责裁决内容，不直接写 OpenClaw workspace 脚本产物。Deal OS API 负责写入 `phases/r1_5_disputes.json`、R1.5 Markdown、workflow state 和 audit event。

`rulings[]` 必填字段：
- `dispute_id`
- `decision`
- `rationale`
- `resolved`

若 `resolved=false`，必须提供 `required_followups`。可选字段包括 `evidence_ids`、`ruling_value`、`is_approved`。

### 评分框架
- 六维评估（Market/Team/Product/Finance/Risk/Strategy）
- 权重按项目阶段自动切换（参见 `agents/hermes/profiles/siq_ic_shared/ic_workflow_policy.json → chairman_scoring`）
- 六维每项 0-10 分，加权求和 ×10 → chairman 分数 (0-100)

### 决策阈值
- V2 加权汇总 ≥70 → 通过
- 68-69 → 可复议一次
- <70 → 不通过

## 报告输出路径
```
/home/maoyd/siq-research-engine/data/wiki/deals/{deal_id}/
```
