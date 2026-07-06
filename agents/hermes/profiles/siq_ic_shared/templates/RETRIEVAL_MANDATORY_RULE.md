# SIQ 双库检索硬性规则（投委会专家适用）

> 不可跳过规则：R1/R2/R4 投委会专家在发表投资观点前，必须完成 Deal OS startup-retrieval。未执行检索即发表观点 = 无效报告。`siq_ic_master_coordinator` 不为自己执行 startup-retrieval，只负责校验和编排专家 receipt。

---

## 一、标准入口

所有角色使用 SIQ Deal OS 后端入口，而不是 OpenClaw 本地脚本：

```text
POST /api/deals/{deal_id}/agents/{agent_id}/startup-retrieval
```

请求体示例：

```json
{
  "round_name": "R1",
  "query": "{company_name} {industry} {stage}",
  "limit": 20,
  "include_external": false,
  "external_providers": ["exa", "tavily", "qcc"],
  "include_vector": false,
  "include_rerank": false,
  "vector_collections": ["siq_deal_shared", "{agent_id}"]
}
```

其中：

- `deal_id`: `data/wiki/deals/{deal_id}` 下的项目包 ID。
- `agent_id`: canonical Hermes profile ID，例如 `siq_ic_finance_auditor`。
- `round_name`: 后端 startup-retrieval 支持 `R1` / `R2` / `R4`。`R0` 使用 intake，`R1.5` 使用 chairman-task，`R3` 使用 red-blue review，不调用 startup-retrieval。
- `include_external`: 默认 `false`；显式启用后可走 Exa / Tavily / QCC wrapper，并保留来源归因与脱敏输出。
- `include_vector`: 默认 `false`；显式启用后由 Deal OS adapter 访问 Milvus collection。
- `include_rerank`: 默认 `false`；显式启用后由平台 reranker adapter 处理排序。

---

## 二、检索目标

| 目标 | Collection / 来源 | 最少命中 |
|------|-------------------|---------|
| 共享项目底稿 | `siq_deal_shared` / deal evidence package | 5 条 |
| 私有知识库 | `{agent_id}` | 3 条（允许 0 条但必须标注） |
| workspace 规则 | 当前 profile 的 `SOUL.md`、`AGENTS.md`、`BOOTSTRAP.md` 等 | 必读 |

---

## 三、报告强制章节

每位专家的 R1 报告必须包含：

```markdown
## 检索结果摘要

### 共享底稿证据（Top-10）
- [evidence_id] 来源 / 时间 / 关键事实 / 置信度

### 私有知识库证据（Top-10）
- [evidence_id] 方法论 / 框架 / 历史案例 / 适用边界

### 证据缺口
- 缺口：
- 对结论影响：
- 需要补充材料：
```

---

## 四、降级规则

- Startup retrieval API 失败时，必须读取 `data/wiki/deals/{deal_id}` 项目包中的本地证据文件。
- Milvus 或私有知识库不可用时，必须在报告中写明 `private_kb_unavailable` 或 `retrieval_degraded`。
- 降级后的报告不得给出 High 置信度结论，除非项目包内已有足够可审计证据。
