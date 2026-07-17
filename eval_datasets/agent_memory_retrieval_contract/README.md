# 智能体记忆检索合同样本

本目录是 SIQ 智能体记忆 Milvus 检索探针使用的合同样本，适合在 PR 和自托管回归中安全运行。

SIQ 记忆系统由四层组成：Hermes 原生会话记忆、本地临时任务记忆、PostgreSQL 权威长期记忆和 Milvus 语义索引。本样本只验证 Milvus 检索面，不声明 Milvus 是事实源；权威记忆项、ACL、scope、半衰期衰减和按需全量召回仍由 API memory service 与 PostgreSQL 账本治理。

本目录只包含可以通过仓库内 Hermes profile seed 满足的 profile 级预期：

- `siq_assistant`
- `siq_ic_legal_scanner`
- `siq_ic_chairman`

发布包装器默认使用这些 case 运行 nightly `agent_memory_milvus_retrieval_latency` 探针。启用 `SIQ_AGENT_MEMORY_VECTOR_SEED=1` 时，包装器也会默认先为这三个 profile 注入同一套 seed，再执行性能基线。

不要向本目录提交运行态 Milvus dump、embedding 缓存、密钥或真实检索命中结果。
