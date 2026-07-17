# SIQ 显式外联门禁

本目录定义 SIQ OpenShell V0.6 的宽松外联基线。实现入口为：

- `allowlist.json`：可审查、无密钥的目标规则；
- `mihomo-runtime.json`：socket 存在时才启用的非敏感 fake-IP 运行配置；
- `scripts/openshell/egress_decision.py`：纯判定层；
- `scripts/openshell/egress_guard.py`：显式 HTTP 请求 broker；
- `scripts/openshell/siq_fetch.py`：sandbox 内的受限兼容客户端。

listener 由 `start_brokers.sh` 管理，并已接入 `start_all.sh` 的 `auto` 模式。它只
绑定项目 `siq-openshell-dev` bridge gateway 的固定 `18792`，不会监听任意私网地址
或 `0.0.0.0`。Hermes 默认仍使用 host runtime；正式 sandbox 接线和 A/B 通过前
不会自动获得流量。

## 边界

`egress_guard.py` 不是透明代理，不实现 HTTP `CONNECT`，也不接收 SOCKS、任意 TCP、文件路径或原始字节流。只有显式调用 `/v1/request` 的流量会经过该门禁。

```text
Hermes / SIQ tool
  -> siq_fetch.py
  -> egress_guard.py:18792
  -> DNS 解析 + policy 判定 + IP 锁定
  -> 公网 HTTP(S)

模型与搜索调用
  -> OpenShell provider / inference route
  -> 模型或搜索服务
```

模型与搜索 allowlist 目标在 broker 内返回 `provider_direct_required`，防止 SDK 流量被错误改造成通用 HTTP 请求。它们应继续使用 OpenShell provider/inference route，因此本实现不改变模型输出路径。

## 请求契约

broker 只接受 `POST /v1/request`，请求体必须为结构化 JSON：

```json
{
  "method": "POST",
  "url": "https://public.example/events",
  "json_body": {
    "event": "completed"
  },
  "headers": {
    "Content-Type": "application/json"
  }
}
```

字段约束：

- `method` 和 `url` 必填；
- `GET`、`HEAD` 不得携带 `json_body`；
- `POST` 必须携带 `json_body`，由 broker 规范化编码；
- `headers` 可省略，仅接受有界字符串映射；
- 不存在 `file`、`path`、`raw_body`、`multipart` 或输出文件字段；
- URL 仅允许 `http`、`https`，禁止凭据、fragment、反斜线和控制字符。

响应体以有界 Base64 返回给 `siq_fetch.py`；客户端只写标准输出，不提供本地输出路径。

## 宽松 V0.6 行为

| 行为 | 结果 |
| --- | --- |
| 未知公网 `GET` / `HEAD` | 放行并审计 |
| 未知公网、最多 128 KiB 的 JSON `POST` | `audit_only` 后转发 |
| allowlist 中 GitHub/Lark 的小型 JSON `GET` / `HEAD` / `POST` | 按规则放行 |
| 模型、Tavily、Exa | broker 拒绝，要求走 OpenShell provider |
| `PUT`、其他修改方法 | broker 拒绝 |
| `multipart/form-data`、`application/octet-stream` | broker 拒绝，包括 allowlist 目标 |
| 超过 128 KiB 的结构化请求体 | 解析阶段拒绝 |
| metadata、回环、私网、链路本地、保留或非全局 IP | 拒绝 |

这里的 128 KiB 上限来自 `allowlist.json`，broker 不允许以运行参数扩大到 policy 阈值以上。

## SSRF 与重定向

每一个请求和重定向 hop 都执行以下流程：

1. 禁止已知 metadata 和内部 hostname；
2. 重新执行 DNS 解析，不使用前一个 hop 的结果；
3. 检查全部 A/AAAA 结果，只要任一地址非全局公网就拒绝；
4. 运行 `egress_decision.py`；
5. 先写最小审计记录；
6. 用本次审核过的 IP 集合创建一次性 resolver；
7. 禁止客户端自动跟随重定向；
8. 建连后核对实际 peer IP 必须属于审核集合。

301/302/303 按 HTTP 语义把适用的 `POST` 转为无 body 的 `GET`；307/308 保留结构化 JSON。重定向目标无效、形成循环、超过 8 hop 或转入非公网地址时停止请求。

## Header 与资源限制

所有目标都会移除 `Host`、`Content-Length` 和 hop-by-hop header，由 HTTP client 重新生成必要字段。未知目标额外移除 `Authorization` 和 `Cookie`；allowlist 目标只有在同 origin 时才可保留它们，跨 origin 重定向必定移除。

默认资源边界：

- 并发：16；
- 排队：2 秒；
- 整个重定向链：30 秒；
- 连接：10 秒；
- 响应 header：32 KiB、最多 128 项；
- 响应 body：8 MiB；
- 重定向：最多 8 hop。

HTTP proxy 环境变量、cookie jar、自动解压和自动重定向均关闭。关闭自动解压可以避免压缩响应在客户端隐式膨胀后绕过 body 上限。

## Listener 绑定

默认只绑定：

```text
127.0.0.1:18792
```

禁止绑定 `0.0.0.0`、`::`、公网或链路本地地址。容器桥接由
`bridge_endpoint.py` 固定检查：

- Docker network 必须精确为 `siq-openshell-dev`，driver 为本机 `bridge`；
- bind IP 必须是该 network IPAM 中唯一的 RFC1918 IPv4 gateway；
- sandbox 只使用 OpenShell 已有别名 `host.openshell.internal`。

不能通过参数传入其他私网 IP、其他 Docker network 或其他 `.internal` 别名。
listener 不直接绑定 hostname，避免该 hostname 因错误 DNS 配置解析到意外接口。
未来接入 OpenShell 时，sandbox 网络 policy 还必须只允许访问受控 bridge alias 和
已批准 provider；仅启动本进程不能拦截其他程序的直连。

## 审计与隐私

每个 policy hop 调用 `security_audit.py`，审计文件位于 `var/openshell/audit/YYYY-MM-DD.jsonl`。持久化内容只有运行上下文、规则、决策、耗时和目标的不可逆投影，不写入：

- 完整 URL、path 或 query；
- request/response body；
- header、token、cookie；
- prompt、用户输入或 session 原文。

正式 sandbox 的业务 POST 还必须携带 audience 为 `siq-egress-guard` 的短期签名
身份；broker 验证后才用 claims 写入逐请求 profile/run/sandbox/session/policy
上下文。密钥、token、切换与轮换流程见
`docs/runbooks/openshell/broker-request-identity.md`。

审计写入失败时，请求失败关闭，不会先访问上游。listener 关闭 access log，应用错误只返回稳定错误码。

## 验证

测试使用假 DNS、假 transport 和临时审计目录，不访问互联网，也不启动 listener：

```bash
python3 -m pytest -q \
  scripts/openshell/tests/test_egress_decision.py \
  scripts/openshell/tests/test_egress_guard.py \
  scripts/openshell/tests/test_siq_fetch.py

# 需要健康的 strict brokers；生成 readiness_effect=none 的脱敏组件证据
python3 scripts/openshell/run_egress_boundary_proof.py --project-root "$PWD"
```

## 已知残余风险

这是“只管理高风险行为”的宽门禁，不是数据防泄漏系统：

- 未知公网 GET 的 query 仍可携带少量数据；
- 未知公网 128 KiB 内 JSON POST 明确是 `audit_only`，仍可传出数据；
- agent 可先读取文件再自行构造小型 JSON，`siq_fetch.py` 不接收文件路径并不能识别数据来源；
- 只有 OpenShell 网络 policy 禁止任意直连后，broker 才能成为强制执行点；
- 宿主 bridge、Mihomo fake-IP、TLS/SNI、peer pinning 与公网 GET/HEAD 已真实验证；正式 sandbox 直连旁路、IPv6 和各 provider SDK 仍待正式联调；
- 外部返回内容按当前需求不做内容安全或入库控制。

这些风险是保持现有研究、检索和模型输出路径不变的直接代价。收紧时应基于审计数据单独调整未知 GET query、JSON POST 阈值或目标 allowlist，而不是改动 Hermes 工作流。
