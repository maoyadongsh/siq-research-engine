# TOOLS.md - IC_Master_Coordinator

## 主入口
- `scripts/coordinator_workflow.py`
  统一驱动 `R0 -> R4`
- `scripts/siq_local_workflow.py`
  本地 CLI（离线/调试用），用于 init、start-r1、submit-report、request-ruling、start-r2、start-r3、snapshot、finalize-decision

## 运行模式
- **对话模式**（推荐）：coordinator agent 对话中自动推进 R0→R4，R1 专家调度通过 agent-to-agent 通信完成
- **CLI 模式**（离线）：`siq_local_workflow.py` 仅操作状态机，R1 后需人工依次提交 5 位专家报告
- 两种模式共享同一个状态机和报告文件，可混合使用

## 当前必备脚本
- `scripts/dynamic_retrieval_engine.py`
  **动态检索引擎** — 项目底稿扫描 + 实时生成检索规则 + 混合检索执行
- `scripts/r1_serial_dispatcher.py`
  R1 串行调度器 — 预检 Milvus 连接、按固定顺序 2 分钟间隔逐一构建专家任务
- `scripts/coordinator_intake.py`
  R0 信息校验
- `scripts/project_discussion_manager.py`
  创建项目目录和讨论文件
- `scripts/round_state_machine.py`
  维护 R1/R2/R3 回合状态
- `scripts/dispute_identifier.py`
  识别争议并生成裁决请求
- `scripts/dynamic_r3_controller.py`
  决定 R3 是 `skip`、`short` 还是 `full`
- `scripts/weighted_scoring.py`
  固定权重评分
- `scripts/submit_expert_report.py`
  写入结构化专家报告
- `scripts/submit_chairman_ruling.py`
  写入 R1.5 裁决结果
- `scripts/project_workflow_snapshot.py`
  导出项目快照
- `scripts/audit_logger.py`
  审计日志

## 检索与知识
- `scripts/milvus_mcp_server.py`
  Milvus MCP 服务入口
- `config/agent_retrieval_config.json`
  agent 检索策略
- `scripts/agent_retriever.py`
  agent 侧检索
- `scripts/unified_retriever.py`
  跨库检索
- `scripts/project_ingestor.py`
  项目材料入库
- `scripts/knowledge_ingestor.py`
  审核后的知识入库

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
- `config/siq_workflow_policy.json`
  阶段、时长、权重、阈值、证据门槛、目录规则
- `scripts/workflow_policy.py`
  读取与应用上述规则

## 已弃用
- 旧 `r1_orchestrator` 路径已被 `round_state_machine.py` 取代
- 旧估值对标脚本不属于当前默认流程
- 旧独立 skill 文档不再作为 prompt 主链
