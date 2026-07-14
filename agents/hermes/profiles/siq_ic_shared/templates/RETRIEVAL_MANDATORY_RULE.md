# SIQ 双库检索硬性规则（投委会专家适用）

> 不可跳过规则：所有正式 R0-R4 Hermes 任务在执行前，必须取得该阶段、该角色自己的 Deal OS startup-retrieval receipt。未完成共享项目 Evidence 与角色专属 Milvus 背景库检索的输出不得进入正式 phase artifact。`siq_ic_master_coordinator` 在 R0 也必须使用自己的 receipt；任何角色都不得复用其他角色的私有命中。

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
  "include_vector": true,
  "include_rerank": false,
  "vector_collections": ["siq_deal_shared", "{agent_id}"]
}
```

其中：

- `deal_id`: `data/wiki/deals/{deal_id}` 下的项目包 ID。
- `agent_id`: canonical Hermes profile ID，例如 `siq_ic_finance_auditor`。
- `round_name`: 后端 startup-retrieval 支持 `R0` / `R1` / `R1.5` / `R2` / `R3` / `R4`，正式任务必须读取与当前阶段精确匹配的 receipt。
- `include_external`: 默认 `false`；显式启用后可走 Exa / Tavily / QCC wrapper，并保留来源归因与脱敏输出。
- `include_vector`: 正式 IC prepare 默认 `true`；由 Deal OS adapter 同时访问共享项目库和当前角色的专属私有库。
- `include_rerank`: 默认 `false`；显式启用后由平台 reranker adapter 处理排序。

---

## 二、检索目标

| 目标 | Collection / 来源 | 最少命中 |
|------|-------------------|---------|
| 共享项目底稿 | `siq_deal_shared` / deal evidence package | 5 条 |
| 私有知识库 | `{agent_id}` | 至少 1 条；0 条时正式任务阻断 |
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

## 四、失败与降级规则

- Startup retrieval、Milvus 或角色私有库不可用时，正式模型任务 fail closed，不写正式报告、不推进 workflow。
- 本地项目包读取只能用于预演、诊断或显式 `deterministic_fallback`，产物必须标记 `private_kb_unavailable` / `retrieval_degraded`，不得冒充正式角色报告。
- 背景知识只能形成 `KBREF-*` 并用于方法、对标和反证；项目事实只能由当前 Deal 的 `EVID-*` 验证。
