# 智能体记忆 Milvus 迁移快照 v1

本合同是离线迁移规划器输入，永远不授权写入 Milvus。

该迁移属于 SIQ 的拟人化全量记忆系统。Milvus 是可重建的语义索引，用于召回和排序；Hermes 运行态记忆、本地临时任务记忆和 PostgreSQL 权威长期记忆是相互独立的记忆层。迁移就绪必须保留 scope、ACL、身份字段、兼容半衰期的 metadata，以及从权威账本执行按需全量召回的能力。

`snapshot_kind` 只能是 `synthetic_contract` 或 `redacted_read_only_inventory`。合成输入始终阻断生产就绪。

`identity.observation_status` 向后兼容：

- 缺失或 `observed`：四个聚合计数和所有 `missing_by_field` 值都必须是非负整数。
- `unavailable`：四个聚合计数和所有 `missing_by_field` 值都必须是 JSON `null`，并且必须提供 `observation_reason`。

当 v1 collection 没有标量 ResearchIdentity 字段，且没有权威只读 inventory 建立字段分布时，`unavailable` 是正确表示。未知计数不得编码为零。规划器会添加 `identity_inventory_unavailable`，保持 `migration_ready=false`，并禁止从 `metadata_json`、正文、标题或 source path 推断身份。

生成产物会区分 `planner_live_milvus_contacted=false` 与 `source_inventory_live_milvus_contacted`；`writes_performed` 始终保持 false。真实迁移只有在单独评审过的只读 inventory 提供 schema、实体数量、向量/索引配置、ID/content-hash manifest、alias 目标和已观测 ResearchIdentity 计数后才能解除阻断。
