# TOOLS.md - IC_Master_Coordinator

## Hermes / Deal OS 主入口
- `POST /api/deals/{deal_id}/workflow/advance-next`
  统一推进 `R0 -> R4`，默认使用 dry-run 先检查证据、检索 receipt、报告合同与阶段门槛。
- `agents/hermes/profiles/siq_ic_master_coordinator/ORCHESTRATION_BRIDGE.md`
  OpenClaw 编排脚本到 SIQ Hermes / Deal OS 服务的映射说明。
- `agents/hermes/profiles/siq_ic_shared/openclaw_script_migration_matrix.json`
  script-by-script 迁移状态、保留原因和 SIQ target。

## 运行模式
- **对话模式**（推荐）：coordinator agent 基于 Deal OS 状态、检索 receipt、报告合同和审计事件推进 R0→R4。
- **API 模式**：通过 `workflow/*` endpoints 执行 dry-run 或真实推进，R1 专家执行由后端 Hermes runtime 统一触发。
- **本地调试模式**：直接调用 `apps/api/services/*` 的 SIQ-native 服务函数，不回退执行 OpenClaw workspace 脚本。

## 当前编排能力映射
- R1 agent 任务：`GET /api/deals/{deal_id}/agents/{profile_id}/task-payload?round_name=R1`
- R1 单专家执行：`POST /api/deals/{deal_id}/workflow/run-r1-agent`
- R1 串行执行：`POST /api/deals/{deal_id}/workflow/run-r1-serial`
- R1.5 争议识别：`POST /api/deals/{deal_id}/workflow/identify-disputes`
- R1.5 主席裁决：`POST /api/deals/{deal_id}/workflow/disputes/{dispute_id}/ruling`
- R2 修订：`POST /api/deals/{deal_id}/workflow/run-r2`
- R3 红蓝复核：`POST /api/deals/{deal_id}/workflow/run-r3`
- R4 投决生成：`POST /api/deals/{deal_id}/workflow/finalize-r4`
- 项目快照/报告读取：`GET /api/deals/{deal_id}/reports`、`GET /api/deals/{deal_id}/decision`
- 审计日志：由 `apps/api/services/deal_store.py::append_audit_event` 写入，所有执行入口必须保留 audit trail。

## 检索与知识
- `POST /api/deals/{deal_id}/agents/{profile_id}/startup-retrieval`
  Deal OS startup-retrieval 标准入口
- `apps/api/services/ic_startup_retrieval.py`
  后端检索 receipt 生成与审计边界
- `apps/api/services/deal_evidence.py`
  项目证据包读取、质量摘要和证据命中边界
- `apps/api/services/deal_retrieval.py`
  动态 query 生成、角色维度匹配和本地 evidence ranking
- `apps/api/services/vector_retrieval.py`
  可选 Milvus 向量检索 adapter，通过 `include_vector` 和 SIQ 环境配置显式启用
- `apps/api/services/rerank_provider.py`
  可选 reranker adapter，调用平台托管的 OpenAI-compatible `/v1/rerank`
- `apps/api/services/external_research_clients.py`
  Exa / Tavily / QCC 的显式 opt-in 外部研究 wrapper，统一凭证、超时、来源归因和脱敏输出
- `apps/api/services/ic_openclaw_importer.py`
  OpenClaw 项目包导入到 Deal OS evidence package
- `scripts/vector-index/milvus-ingestion/scripts/project_ingestor.py`
  低层向量入库参考实现；生产调用必须通过 Deal OS 服务边界
- `scripts/vector-index/milvus-ingestion/scripts/knowledge_ingestor.py`
  审核后知识入库参考实现；生产调用必须集中处理 provider 配置、密钥和审计

## Subagent 调度规则（R1-R3 尽调任务）

- 每次 `sessions_spawn` 必须显式设置 `timeoutSeconds: 600`（10分钟）
- 尽调任务涉及多轮工具调用（搜索→分析→写报告），默认超时不足以完成
- spawn 参数示例：`timeoutSeconds=600`
- 如遇复杂争议裁决（R1.5 chairman），可适当放宽至 `timeoutSeconds=900`
- spawn 时推荐使用 `runtime="subagent"`，mode 按需选择
- 确保不引用已移除的 MiniMax 模型；可用模型：zai/glm-5, kimi/kimi-code, ollama/qwen3.5:35b, zai/glm-5-turbo

## 共享文件中枢

所有 IC agent 的跨 agent 文件读写统一走 coordinator workspace 下的 `shared/` 目录：

```
/home/maoyd/siq-research-engine/data/wiki/deals/
├── projects/     # 按项目归档的尽调产出（symlink 指向 projects/）
├── templates/    # 共享模板（投决报告、专家报告等）
├── evidence/     # 共享证据包和底稿
└── README.md     # 共享约定说明
```

### 约定
- Expert agent 通过 `sessions_send` 接收任务时，coordinator 附带共享文件绝对路径
- Expert 写入报告时，统一写入 `data/wiki/deals/SIQ-{公司简称}-{日期}-{序号}/` 下
- 不在各 agent 自身 workspace 中创建项目目录
- Coordinator 负责创建项目目录结构和 symlink

### 与 agentToAgent 配合
- 启用 agentToAgent 后，coordinator 可直接向专家主会话发消息
- 消息中包含项目路径和任务指令
- Expert 完成后通过 sessions_send 回复 coordinator

## 规则源
- `agents/hermes/profiles/siq_ic_shared/ic_workflow_policy.json`
  阶段、时长、权重、阈值、证据门槛、目录规则
- `scripts/workflow_policy.py`
  读取与应用上述规则

## 已弃用
- 旧 `r1_orchestrator` 路径已被 `round_state_machine.py` 取代
- 旧估值对标脚本不属于当前默认流程
- 旧独立 skill 文档不再作为 prompt 主链
