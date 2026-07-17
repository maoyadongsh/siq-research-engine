# OpenShell memory write boundary

SIQ 的对话和 Agent memory 写入由宿主 FastAPI 执行，不由 OpenShell sandbox
直接连接 PostgreSQL 或 Milvus。一级市场和二级市场共用逻辑 alias
`siq_agent_memory_active`，依靠 `tenant_id`、`agent_group`、`visibility`、
`profile`、`deal_id` 和 `project_id` 做访问隔离。

## Collection 白名单

- 宿主 memory service 只可把运行时写目标配置为 `siq_agent_memory_active`。
- 物理集合通过 Milvus alias 版本化切换；运行时不得直接配置物理集合名。
- 每次写入前必须通过 `siq_agent_memory_milvus_v2` schema preflight。
- `upsert` 实现需要同 ID 的 `delete_by_id`，因此两者同时属于 memory 写入闭环。
- OpenShell sandbox 不能直连 `19530`，也不能修改知识集合。

白名单机器契约位于
`infra/openshell/data-broker/memory-collections.json`。当前宿主写入验证应至少覆盖
`upsert -> get/search -> delete`，并确认临时探针记录已删除。

受控实证入口：

```bash
python3 scripts/openshell/run_memory_write_probe.py --api-pid API_PID
python3 scripts/openshell/build_memory_write_evidence.py --project-root "$PWD"
```

探针只从经过 cwd/cmdline/owner 校验的当前 FastAPI 进程继承 memory 运行配置，使用唯一
合成记录，并在两种 backend 都完成独立零残留复核后才发布 owner-only receipt。receipt
固定保存在 ignored 的 `var/openshell/proofs/`；可提交结果固定为
`artifacts/openshell/v0.6/memory-write-evidence.sanitized.json` 和 `.md`，不含连接信息、
记录 ID、正文、物理 collection 名或凭据。

2026-07-16 的真实探针已覆盖一级/二级市场：PostgreSQL
`insert/readback/rollback/post_rollback_verify` 和 Milvus
`upsert/get/search/delete/post_delete_verify` 全部通过，最终 residual count 均为 0。

## PostgreSQL memory

PostgreSQL `agent_memory` schema 同样只由宿主 FastAPI 写入。OpenShell sandbox
没有 `SIQ_APP_DATABASE_URL`，其市场事实查询只能进入 `18793` 只读 broker。验证宿主
写入时使用合成记录并在同一事务内执行 `insert -> readback -> rollback`，随后从新连接
确认残留行为 0；不得把应用数据库账号改成只读账号。

## 扩展规则

新增 memory collection 时，优先创建新的物理版本并切换
`siq_agent_memory_active` alias，不新增运行时可配置名称。若未来必须让 sandbox
直接保存 memory，应新增独立的 memory broker、独立 token audience 和 ACL 校验；
不得把 Milvus 端口加入 OpenShell 网络白名单。
