# SIQ 财务专家启动检查清单 (STARTUP_CHECKLIST.md)

**Agent ID**: `siq_ic_finance_auditor`
**版本**: v1.0 | **生效日期**: 2026-05-01
**规则状态**: 🔒 已固化 — 任何项目任务执行前**必须**完成本清单

---

## 一、强制启动协议（任务触发后自动执行）

收到任何项目任务后，在输出财务观点前，**必须**按以下顺序完成 Deal OS startup-retrieval receipt 检索。

### ☐ 步骤一：环境确认

| 检查项 | 命令/方法 | 通过标准 |
|--------|----------|---------|
| Deal OS startup-retrieval API | `POST /api/deals/{deal_id}/agents/siq_ic_finance_auditor/startup-retrieval` | 返回 receipt |
| 共享底稿命中 | receipt `shared_hits` / `evidence_hits` | 有相关证据或明确 gap |
| 私有知识命中 | receipt `private_hits` | 有方法论证据或明确 gap |
| 私有 Milvus / 可选 rerank | 后端配置与响应字段 | 私有 Milvus 必须成功且非空；rerank 状态必须有 reason |

### ☐ 步骤二：获取项目标签

1. 读取 `data/wiki/deals/{deal_id}/project_brief.md`
2. 提取 `项目标签` 字段（如 `dajin`）
3. 如项目标签缺失或不匹配，由 Deal OS retrieval response 的 `gaps` / `dynamic_queries` 暴露，不在 profile 侧采样底层 collection。

### ☐ 步骤三：共享项目底稿检索（siq_deal_shared）

**来源**: startup receipt 中的共享底稿和 evidence hits
**方法**: Deal OS startup-retrieval API
**检索关键词模板**（财务专家专用）：

```
{company_name} 收入 利润 现金流 估值 财务
{company_name} 可比公司 估值倍数 PE PB
{company_name} 客户 订单 产能 毛利率
```

**优先关注**: 收入模式、成本结构、融资事实、营运资本线索
**目标**: Top-20 去重证据
**阅读要求**: 必须浏览返回证据的摘要内容

### ☐ 步骤四：私有知识库检索（siq_ic_finance_auditor）

**来源**: startup receipt 中 profile-scoped private hits
**方法**: 同上
**检索关键词模板**（财务专家专用）：

```
Pre-IPO 估值 DCF 可比公司 装备制造 港股上市 锚定投资
财务尽调 收入确认 现金流 毛利率 单位经济
国资条款 退出框架 对赌 回购 清算优先
```

**优先关注**: 阶段估值方法、单位经济模型、国资条款、退出框架
**目标**: Top-20 去重证据
**阅读要求**: 必须浏览返回证据的摘要内容

### ☐ 步骤五：本地底稿 Fallback

当 API 不可用或 receipt 明确缺口时，只允许预演或显式 fallback 读取本地底稿并标注 `retrieval_degraded`；正式任务必须阻断：

```
data/wiki/deals/{deal_id}/
  ├── project_brief.md          (必读)
  ├── R0_信息校验报告.md         (必读)
  ├── r1_finance_auditor_report*.md  (如存在，必读)
  ├── r2_finance_auditor_report*.md  (如存在，必读)
  └── *.json 数据文件            (按需)
```

### ☐ 步骤六：深度学习与交叉验证

- [ ] 先看共享底稿中的 **verified** 财务数字
- [ ] 再看私有知识库中的估值方法论
- [ ] 所有数字必须标注 `verified` 或 `assumed`
- [ ] 没完成双库检索时 **不得** 直接输出估值建议

---

## 二、检索失败降级方案（按优先级）

| 优先级 | 失败场景 | 降级动作 | 报告标注 |
|--------|---------|---------|---------|
| 1 | startup-retrieval API 不可用 | 读取本地 `data/wiki/deals/{deal_id}/` 底稿文件 | "检索方式降级：startup receipt 不可用，置信度调整为 Low" |
| 2 | receipt 返回 vector/rerank 错误 | 使用 receipt 中本地 evidence hits 和 gaps | "检索方式降级：后端增强检索不可用，置信度调整为 Medium/Low" |
| 3 | 项目标签不匹配 | 依据 `dynamic_queries` / `gaps` 补充本地底稿核查 | "检索方式降级：project_tag 不匹配，改用本地底稿复核" |

**强制要求**: 任何降级必须在最终报告中注明 `"检索方式降级，置信度调整"`

---

## 三、检索后观点发表规则

### 必须先完成的检查

- [ ] 双库检索已完成（或已执行降级并标注）
- [ ] 本地底稿已阅读
- [ ] Verified vs Assumed 已区分
- [ ] 红旗项已识别（如有，评分上限 60）
- [ ] 未验证假设已计数（>3项则评分≤70）

### 输出格式强制要求

每份专家报告**必须包含**以下结构：

1. **综合评分**: 0-100 分整数
2. **子维度评分**: 根据自身分析框架拆解
3. **置信度**: High / Medium / Low（受检索方式影响）
4. **红旗项**: 存在即扣分的硬伤（如有则评分上限 60）
5. **已验证事实** vs **未验证假设**: 明确区分
6. **开放问题**: 需要进一步尽调的待定事项

### 评分纪律

- 置信度 Low 时，分数浮动范围 ±15
- 存在红旗项，评分不得超过 60（除非有充分对冲方案）
- 未验证假设超过 3 项，评分不得超过 70
- 数据来源必须标注（verified / assumed / estimated）

---

## 四、快速参考：常用项目目录

| 项目 | 目录 | 项目标签 | Milvus Tag |
|------|------|---------|-----------|
| 大金重工港股锚定 | `SIQ-大金重工-20260430` | `dajin` | `ingest_0430_2033` |
| SJSEMI | `SIQ-SJSEMI-2026-0422` | — | — |
| 宇树科技 | `SIQ-YUSHU-2026-002` | — | — |

---

## 五、自动化入口（SIQ/Hermes）

在 Hermes 中不再直接执行 OpenClaw 本地检索脚本。财务专家使用 Deal OS 后端：

```text
POST /api/deals/{deal_id}/agents/siq_ic_finance_auditor/startup-retrieval
```

请求体：

```json
{
  "round_name": "R1",
  "query": "{company_name} 收入 利润 现金流 估值 财务",
  "limit": 20
}
```

若 API 不可用，只能为预演或显式 fallback 读取 `data/wiki/deals/{deal_id}` 项目包并标注 `retrieval_degraded`；不得生成正式报告。

---

**固化确认**: 本清单于 2026-05-01 由 siq_ic_finance_auditor 固化，经 Milvus 双库检索验证后生效。
**下次审阅**: 2026-06-01 或新技能发布后

---

*📊 SIQ 投委会财务专家 | Precision, skepticism, stage-aware valuation*
