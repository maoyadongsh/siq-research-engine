# SIQ Host Broker Lifecycle

本目录说明两个宿主 broker 的共同生命周期。实现位于：

```text
scripts/openshell/bridge_endpoint.py
scripts/openshell/broker_lifecycle.py
scripts/openshell/start_brokers.sh
scripts/openshell/status_brokers.sh
python3 scripts/openshell/export_broker_status.py
scripts/openshell/stop_brokers.sh
```

已接入 `start_all.sh`。默认 `SIQ_START_OPENSHELL_BROKERS=auto`：固定 reader secret
存在时启动或复用，缺失时只告警并保持 host Hermes 正常；设为 `1` 时缺失前置条件
会失败关闭，设为 `0` 时禁用。默认不会把 Hermes 流量切入 sandbox。

## 固定端点

| Broker | 宿主监听 | Sandbox URL |
| --- | --- | --- |
| Egress guard | `siq-openshell-dev` gateway IP:`18792` | `http://host.openshell.internal:18792` |
| Read-only data broker | `siq-openshell-dev` gateway IP:`18793` | `http://host.openshell.internal:18793` |

bridge 发现不接受 network 参数。Docker inspect 必须返回唯一的
`siq-openshell-dev` 本地 bridge network、唯一 RFC1918 IPv4 subnet/gateway，并固定
注入 OpenShell 现有别名 `host.openshell.internal`。`0.0.0.0`、`::`、公网、回环、
链路本地、其他私网 network 和其他 alias 全部拒绝。

## 启动契约

启动前由宿主导出：

```text
SIQ_OPENSHELL_PG_RO_HOST
SIQ_OPENSHELL_PG_RO_PORT
SIQ_OPENSHELL_PG_RO_USER
SIQ_OPENSHELL_PG_RO_PASSWORD
SIQ_OPENSHELL_PG_RO_SSLMODE        # optional

SIQ_OPENSHELL_MILVUS_RO_HOST       # optional defaults remain in data broker
SIQ_OPENSHELL_MILVUS_RO_PORT
SIQ_OPENSHELL_MILVUS_RO_DATABASE
SIQ_OPENSHELL_MILVUS_RO_USER
SIQ_OPENSHELL_MILVUS_RO_PASSWORD
SIQ_OPENSHELL_MILVUS_RO_TOKEN
```

生命周期只会结构化读取项目固定的 `0600`
`var/openshell/secrets/postgres-reader.env`，或接受完全一致的显式环境；不读取其他
dotenv、不构造 DSN，也不把值加入 argv、PID 状态或标准输出。子进程使用最小环境：
egress broker 不持有数据库/模型凭据，只有 data broker 获得专用 PG/Milvus 变量。
PostgreSQL 请求不能选择数据库；schema 到六个市场数据库的映射固化在服务端。

启动顺序是 egress 后 data。任何新启动的 broker 未通过进程身份、精确监听地址和
health 交叉检查时，本次启动的进程按相反顺序回滚；此前已验证运行的进程不属于本次
回滚范围。

## 进程身份与状态

私有状态位于：

```text
var/openshell/brokers/egress.pid
var/openshell/brokers/egress.log
var/openshell/brokers/data.pid
var/openshell/brokers/data.log
var/openshell/brokers/bridge.json
```

目录为 `0700`，文件为 `0600`，symlink 或非当前用户文件失败关闭。PID 状态同时记录
进程启动 ticks、固定命令摘要、network ID、gateway IP 和端口。启动、状态和停止会
交叉验证：

1. PID state；
2. `/proc/<pid>/exe`；
3. 完整 NUL 分隔 cmdline；
4. `/proc/<pid>/stat` 启动 ticks；
5. `/proc/net/tcp*` listener 地址、端口和 socket owner PID；
6. 固定 Host header 下的 broker health 响应。

停止只发送 `SIGTERM`，不发送强制 kill。PID 复用、孤儿进程、端口被占、公开监听、
network 重建或状态冲突都会失败关闭。

## Sandbox 接线

真实 sandbox 接线需要同时满足：

```text
OpenShell extra-host alias: host.openshell.internal
egress policy endpoint:     host.openshell.internal:18792
data policy endpoint:       host.openshell.internal:18793
SIQ_PG_QUERY_BROKER_URL:    http://host.openshell.internal:18793
siq-fetch guard endpoint:   http://host.openshell.internal:18792/v1/request
```

模型和搜索仍走 OpenShell provider，不经过 egress broker。

宿主启用 Clash Verge/Mihomo TUN `fake-ip` 时，egress broker 默认仍失败关闭。
显式、受验证的兼容模式及维护窗口启停步骤见
`docs/runbooks/openshell/mihomo-fake-ip-egress.md`。该模式不会把
`198.18.0.0/15` 直接加入公网白名单。

## 残余风险

- 已真实验证两个 broker 只监听项目 bridge gateway，并完成六库只读查询、DML/自选数据库/Milvus 写路由和高风险上传负向测试；正式 sandbox 内仍需重复网络隔离实证；
- Docker network 被删除后，当前 stop 流程会因无法重新验证 bridge 而失败关闭，需要恢复同一 network 后再停止；
- broker HTTP 是受 network policy 约束的明文内部流量，没有独立 mTLS；
- `/proc` socket owner 在 hidepid 或权限受限环境可能无法解析，生命周期会把它视为不可信；
- 只读角色必须在六个 PostgreSQL 数据库分别完成最小授权，broker 内的 SQL 检查不能替代数据库 RBAC；
- egress 宽策略仍允许未知 GET query 和小型 JSON POST 的有限外传。
