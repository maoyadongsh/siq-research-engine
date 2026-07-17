# Milvus Sandbox Write-Protection Proof

状态：实现完成，真实 proof 必须由独立 NOT_PRODUCTION OpenShell sandbox 生成。没有
新鲜 proof 时，正式 `siq_analysis` lifecycle 和 service preflight 均失败关闭。

## 安全边界

当前宿主 Milvus 2.6.14 的 `authorizationEnabled=false`。因此不能声称“匿名 Milvus
身份只读”。SIQ 使用以下组合边界：

1. 编译器禁止 sandbox policy 出现 `5432`、`15432` 或 `19530`；
2. proof sandbox 的网络只开放 `host.openshell.internal:18793`；
3. data broker 仅暴露 Search、Query、Get、Describe；
4. Insert、Upsert、Delete、Drop、Create、Alter 和索引变更路径不存在；
5. sandbox 内实测直连 `19530` 失败，同时宿主只读探测确认 Milvus 在线；
6. proof 绑定 active policy、OpenShell sandbox/container、bridge、broker 源码和 Milvus
   schema 摘要，有效期固定为 3600 秒。

这个 proof 不会向 Milvus 发送任何写请求。所谓 mutation 负向测试只请求 broker 中
不存在的 HTTP 路径，必须得到 `404 route_not_found`，不会到达 Milvus 后端。业务
collection、索引和数据均不修改。

该结论只约束 sandbox 到知识 collection 的通道，不代表宿主 Milvus 全局只读。
`apps/api/services/agent_memory_service.py` 和 `agent_memory_milvus.py` 仍在宿主
FastAPI 中使用独立配置，对 `SIQ_AGENT_MEMORY_MILVUS_COLLECTION` 指定的 memory
collection 执行 upsert/search。OpenShell 不得向 sandbox 暴露这组写凭据，也不得
阻断宿主 memory 写入；两条链路必须分别验收。

## 前置条件

- project gateway `siq-openshell-dev` 已连接且没有其他 sandbox；
- 候选 `siq_analysis` 镜像和 7 个业务 mount + 5 个控制 mount contract 已通过既有 smoke；
- data broker 健康响应 schema 为 `siq.openshell.read-only-data-broker.v2`；
- broker request identity key 存在，data broker 要求签名身份；
- Milvus `127.0.0.1:19530` 在线；
- 选择一个已存在的公司目录，只用于复用固定 mount/identity lifecycle。

proof sandbox 不启动 Hermes、不配置 provider、不调用模型、不开放 egress。它获得一个
15 分钟、audience 固定为 `siq-read-only-data-broker` 的 token。正常或失败路径都必须
验证 sandbox/container 删除并清理临时 snapshot/state，随后才允许发布 GO proof。

## 执行

```bash
cd /home/maoyd/siq-research-engine

scripts/openshell/run_milvus_boundary_proof.sh \
  --market cn \
  --company '600519-贵州茅台' \
  --probe-id "probe-$(openssl rand -hex 6)" \
  --acknowledge-not-production
```

不要绕过 shell wrapper 直接运行 Python。wrapper 持有项目 maintenance lock，并以
项目固定的 OpenShell/Docker 环境执行。

成功后私有原始证据位于：

```text
var/openshell/proofs/milvus-sandbox-receipt.json
var/openshell/proofs/milvus-write-protection.json
```

可提交的脱敏证据位于：

```text
artifacts/openshell/v0.6/milvus-write-protection.sanitized.json
artifacts/openshell/v0.6/milvus-write-protection.sanitized.md
```

proof schema：

```text
infra/openshell/schemas/milvus-write-protection-proof.schema.json
```

## 消费

```bash
python3 scripts/openshell/check_siq_services.py \
  --host-alias 127.0.0.1 \
  --proof-file var/openshell/proofs/service-security.json \
  --milvus-proof-file var/openshell/proofs/milvus-write-protection.json \
  --json
```

以下任一变化都会使 proof 失效：超过 3600 秒、bridge 重建、broker 源码变化、policy
编译输入变化、schema/key 缺失、active policy 暴露数据库端口、sandbox/container
receipt 不一致或清理未完成。旧的 `--milvus-write-protection-proof` 布尔参数已拒绝。
