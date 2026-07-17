# SIQ OpenShell 服务协议预检

`scripts/openshell/check_siq_services.py` 是正式 `siq_analysis` lifecycle 和 A/B 前置门禁
共用的只读服务检查。它不会启动、停止、重载或修改模型、API、数据库、gateway、broker
或 sandbox。

正式 lifecycle 从宿主 loopback 运行这项 readiness 检查。它证明服务本身已在宿主提供
预期协议，不单独证明 sandbox 内的 `host.openshell.internal` 路由；后者仍由正式 sandbox
网络 smoke 验收。

## 检查范围

报告 schema 固定为 `siq.openshell.service_preflight.v2`，并把两个层次分开记录：

| 服务 | Transport | 只读协议契约 |
|---|---|---|
| Qwen `8004` | TCP | `GET /v1/models`，OpenAI model list 最小 JSON shape |
| Gemma `8006` | TCP | `GET /v1/models`，OpenAI model list 最小 JSON shape |
| Nemotron `8007` | TCP | `GET /v1/models`，OpenAI model list 最小 JSON shape |
| Embedding `8013` | TCP | `GET /v1/models`，OpenAI model list 最小 JSON shape |
| PostgreSQL `15432` | TCP | 独立 read-only identity proof |
| Milvus `19530` | TCP | 独立 mutation-deny proof |
| SIQ API `18081` | TCP | `GET /health`，`status=ok` |
| host Hermes `18651` | TCP | `GET /health`，`status=ok` |

HTTP 检查只发送固定 `GET`，没有 query、请求体、Authorization 或 cookie；不使用环境
代理，不跟随重定向。响应最多读取 128 KiB，只在内存中校验 JSON，不把正文、模型清单、
URL 或服务返回的任意错误文本写入报告。

## 运行与判读

```bash
python3 scripts/openshell/check_siq_services.py \
  --host-alias 127.0.0.1 \
  --proof-file var/openshell/proofs/service-security.json \
  --milvus-proof-file var/openshell/proofs/milvus-write-protection.json \
  --output artifacts/openshell/v0.6/service-preflight.sanitized.json \
  --markdown-output artifacts/openshell/v0.6/service-preflight.sanitized.md \
  --replace \
  --json
```

退出码：`0` 为 `GO`，`1` 为结构正确的 `NO_GO`，`2` 为预检配置错误。required 服务的
TCP 或协议契约任一失败都会阻断；可选 Nemotron 失败只产生 warning。端口未启动显示为
`service_connectivity`，TCP 已连通但 JSON/API 不符合契约显示为 `service_protocol`。
不加 `--replace` 时导出器拒绝覆盖现有 evidence；JSON/Markdown 均以 `0600` 原子写入。
导出后必须运行 `check_sanitized_artifacts.py`，不能把 stdout 重定向冒充正式 evidence。

当前策略不会自动启动离线的 `8004/8006`，也不会把它们从白名单移除。两个 fallback
当前明确禁用，preflight 仍记录其连通性和只读协议结果，但离线只产生 optional warning，
不阻断 OpenShell 切流。未来启用任一服务时，必须先恢复对应协议检查并完成真实 fallback
验收，不能把“保留白名单”解释为“能力已验证”。

## 不代表的结论

`/v1/models` 只证明 OpenAI-compatible discovery 契约可用，不执行 completion 或
embedding 请求。因此本预检不能替代：

- Qwen/Gemma fallback 真实调用；
- embedding 向量生成与维度验证；
- PostgreSQL 查询和 DML/DDL 负向测试；
- Milvus proof 必须来自独立 OpenShell boundary sandbox：直连 `19530` 被拒绝，
  broker Search/Query/Get/Describe 成功且 mutation 路由不存在；
- proof schema、3600 秒有效期、bridge、policy compiler、broker 源码及
  sandbox/container binding；
- Hermes create/stream/collect/stop；
- Host/OpenShell 真实 A/B 质量评测。

这些证据不完整时，V0.6 completion 必须继续返回 `NO_GO`。

## v1 兼容边界

`siq.openshell.service_preflight.v1` 只证明 TCP connect，不能证明监听端口提供的是预期
HTTP 服务。lifecycle、A/B prerequisite 和 completion 因此只接受 v2；旧 v1 evidence
会被判定为 stale/invalid，而不会静默降级为通过。升级不改变端口、服务 required/optional
分类或运行时流量，只需用上面的正式导出命令重新生成 evidence。
