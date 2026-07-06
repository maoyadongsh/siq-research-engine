# SIQ 投委会 Milvus 知识库架构梳理

**生成时间**: 2026-04-30 15:52 CST  
**梳理人**: ic_master_coordinator  
**用途**: R1 投研任务启动前检查清单

> 2026-07-06 说明：本文仅保留为 OpenClaw/Milvus 历史审计快照。Hermes IC 智能体不得按本文直连本地 collection；运行时统一通过 Deal OS startup retrieval、vector adapter、rerank adapter 和审计合同接入。

---

## 一、Collection 总览

```
┌─────────────────────────────────────────────────────────────┐
│                    Milvus 知识库架构 (9 Collections)          │
├─────────────────────────────────────────────────────────────┤
│  【共享项目底稿库】                                          │
│  ic_collaboration_shared        1,476 条  project_tag=dajin │
│  ic_archive_sop                       0 条  (归档库，空)     │
├─────────────────────────────────────────────────────────────┤
│  【Agent 私有知识库】                                        │
│  ic_strategist                    985 条  SOP_20260407      │
│  ic_sector_expert               3,750 条  SOP_20260408      │
│  ic_finance_auditor             1,335 条  SOP_20260407      │
│  ic_legal_scanner             198,780 条  ingest_0429_2354  │
│  ic_risk_controller             1,205 条  ingest_0418_0012   │
│  ic_chairman                    1,599 条  chairma           │
│  ic_master_coordinator              0 条  (空)               │
└─────────────────────────────────────────────────────────────┘
```

---

## 二、共享项目底稿库 — `ic_collaboration_shared`

| 属性 | 值 |
|------|-----|
| **Collection 名称** | `ic_collaboration_shared` |
| **总记录数** | 1,476 条 |
| **当前项目** | `dajin` (大金) |
| **向量维度** | 1,024 (Qwen3-VL-Embedding-2B) |
| **索引类型** | HNSW (M=32, efConstruction=256) |
| **度量方式** | L2 |
| **Schema** | id, vector(1024), project_tag, metadata |
| **project_tag 索引** | INVERTED (已建) |

### 底稿内容来源
- **文件**: `Teaser - Dajin - CH.md` 等
- **路径**: `/home/maoyd/文档/大金md文件/`
- **分块策略**: 1,200 字符/块，180 字符重叠
- **总块数**: 约 1,476 个文本块

---

## 三、Agent 私有知识库映射

| Agent ID | Collection 名称 | 记录数 | project_tag | 知识库内容 |
|----------|----------------|--------|-------------|-----------|
| `ic_strategist` | `ic_strategist` | 985 | `SOP_20260407` | 十五五规划、国家级政策文件汇编 |
| `ic_sector_expert` | `ic_sector_expert` | 3,750 | `SOP_20260408` | 人形机器人行业白皮书、LED显示赛道分析 |
| `ic_finance_auditor` | `ic_finance_auditor` | 1,335 | `SOP_20260407` | A股市场展望、券商研报 |
| `ic_legal_scanner` | `ic_legal_scanner` | 198,780 | `ingest_0429_2354` | 法律法规、军事设施保护条例等 |
| `ic_risk_controller` | `ic_risk_controller` | 1,205 | `ingest_0418_0012` | 风险预警报告 |
| `ic_chairman` | `ic_chairman` | 1,599 | `chairma` | 私募股权投资手册、尽调经验 |
| `ic_master_coordinator` | `ic_master_coordinator` | 0 | — | (空，协调者无需私有库) |

### 各 Agent 私有库内容特征

#### `ic_strategist` (985 条)
- **内容**: 国家级政策文件、五年规划编制文件
- **用途**: 宏观战略分析、政策导向判断
- **规模**: 较小，需补充更多资本流向和地缘政治资料

#### `ic_sector_expert` (3,750 条)
- **内容**: 人形机器人行业白皮书、LED显示赛道分析
- **用途**: 行业深度分析、TAM/SAM/SOM 计算
- **规模**: 中等，覆盖机器人和显示行业

#### `ic_finance_auditor` (1,335 条)
- **内容**: A股春季展望、券商研报、市场分析
- **用途**: 财务分析、估值模型、现金流评估
- **规模**: 较小，需补充更多财务尽调方法论

#### `ic_legal_scanner` (198,780 条)
- **内容**: 法律法规、军事设施保护条例、地方法规
- **用途**: 法律合规、股权结构、监管合规、知识产权
- **规模**: **最大**，近 20 万条，法律检索能力强

#### `ic_risk_controller` (1,205 条)
- **内容**: 风险预警报告、风险分析
- **用途**: ESG、舆情、供应链、行业周期风险评估
- **规模**: 较小，需补充更多行业风险案例

#### `ic_chairman` (1,599 条)
- **内容**: 私募股权投资手册、尽调经验、投资方法论
- **用途**: 综合裁决、条款设计、退出路径
- **规模**: 中等，覆盖投资全流程经验

---

## 四、R1 投研任务启动检查清单

### ✅ 基础设施检查

| 检查项 | 状态 | 说明 |
|--------|------|------|
| Milvus 服务 | ✅ 正常 | 历史本地端点（已废弃为 profile 运行入口） |
| Embedding 服务 | ✅ 正常 | localhost:8000 (Qwen3-VL-Embedding-2B) |
| Rerank 服务 | ⚠️ 待确认 | localhost:8001 |
| 企查查 API | ✅ 可用 | 四端点已验证 |
| Tavily API | ✅ 可用 | 实时搜索 |
| Exa API | ✅ 可用 | 深度搜索 |

### ✅ Collection 连接状态

| Collection | 加载状态 | 可用性 |
|-----------|---------|--------|
| `ic_collaboration_shared` | ✅ Loaded | 项目底稿可检索 |
| `ic_strategist` | ✅ Loaded | 宏观政策库可检索 |
| `ic_sector_expert` | ✅ Loaded | 行业知识库可检索 |
| `ic_finance_auditor` | ✅ Loaded | 财务知识库可检索 |
| `ic_legal_scanner` | ✅ Loaded | 法律知识库可检索 |
| `ic_risk_controller` | ✅ Loaded | 风险知识库可检索 |
| `ic_chairman` | ✅ Loaded | 投资经验库可检索 |

### ⚠️ 需要注意的问题

1. **ic_legal_scanner 编码问题**: 企查查 API 返回 GBK 编码中文，需修复解码逻辑
2. **project_tag 命名不一致**: 
   - 大部分用 `SOP_YYYYMMDD` 格式
   - legal_scanner 用 `ingest_YYYYMMDD_HHMM`
   - chairman 用 `chairma` (截断)
3. **ic_master_coordinator 私有库为空**: 协调者主要依赖 workspace 文件
4. **ic_archive_sop 为空**: 归档库尚未启用

---

## 五、Agent 启动检索规则 (R1 前置条件)

每位专家在发表 R1 观点前**必须**完成三步学习：

```
┌─────────────────────────────────────────────────────────┐
│  Agent 启动检索流程 (legacy startup retrieval)           │
├─────────────────────────────────────────────────────────┤
│  1. 共享项目底稿 → ic_collaboration_shared               │
│     按 project_tag=dajin 过滤                            │
│     目标: Top-8 项目事实片段                              │
├─────────────────────────────────────────────────────────┤
│  2. 私有知识库 → {agent_id} Collection                   │
│     按角色专业领域检索                                   │
│     目标: Top-12 专业背景知识                             │
├─────────────────────────────────────────────────────────┤
│  3. Workspace 文件 → SOUL.md + AGENTS.md + 方法论        │
│     目标: 角色职责、工作流程、输出标准                      │
└─────────────────────────────────────────────────────────┘
```

### 各 Agent 检索配置摘要

| Agent | 共享库目标 | 私有库目标 | Workspace 目标 | 关注领域 |
|-------|-----------|-----------|---------------|---------|
| `ic_strategist` | 8 (赛道/融资/产业链) | 12 (政策/资本/周期) | 0 | 政策、资本、地缘 |
| `ic_sector_expert` | 12 (产品/市场/竞争) | 8 (技术/国产替代) | 0 | TAM、技术路线、壁垒 |
| `ic_finance_auditor` | 8 (财务/估值/现金流) | 12 (估值方法/审计) | 0 | 估值、现金流、盈利 |
| `ic_legal_scanner` | 8 (股权/合规/IP) | 12 (法规/案例) | 0 | 合规、股权、诉讼 |
| `ic_risk_controller` | 8 (风险/ESG/舆情) | 12 (风险案例/预警) | 0 | ESG、供应链、周期 |
| `ic_chairman` | 8 (亮点/红线) | 8 (经验/方法论) | 4 (决策框架) | 综合裁决 |

---

## 六、下一步行动

### 立即执行 (R0 → R1 启动)

1. **确认项目信息**
   - 公司名称: 大金 (dajin)
   - 行业: (待确认)
   - 轮次: (待确认)
   - 项目标签: `dajin`

2. **执行 legacy startup retrieval**
   - 为每位专家生成 Top-20 证据包
   - 包含: 共享底稿 + 私有知识 + Workspace 指引

3. **R1 串行调度**
   - 按固定顺序: strategist → sector → finance → legal → risk → chairman
   - 间隔: 2 分钟/位
   - 超时: 10 分钟/位

### 需要用户确认

1. **当前项目是否为「大金」？** 确认 project_tag = `dajin`
2. **是否需要先统一检索脚本？** (hybrid_retriever v4 统一化)
3. **R1 启动时间？** 确认后立即开始调度

---

**状态**: 所有 Collection 已加载，基础设施就绪，等待项目确认后启动 R1。
