# OpenShell broker 请求身份

本机制只保护正式 OpenShell sandbox 到两个宿主 broker 的请求边界，不改变宿主
Hermes 默认运行方式。默认 `start_brokers.sh` 继续启动兼容模式；正式
`siq_analysis` lifecycle 和 A/B 门禁只接受请求身份强制模式。

## 安全契约

- HMAC-SHA256 密钥只保存在
  `var/openshell/secrets/broker-request-identity.key`，目录 `0700`、文件 `0600`；
  `var/**` 已被 Git 忽略，密钥不进入镜像、policy、manifest、审计或脱敏证据。
- 每次正式 transaction 生成两个 6 小时 token，分别限定 audience：
  `siq-egress-guard` 和 `siq-read-only-data-broker`。
- 两个 token 都绑定固定 gateway、`profile`、`run_id`、逻辑 sandbox 名称、
  `session_id`、task policy SHA-256 和 run nonce SHA-256。
- sandbox 只获得 token，不获得 HMAC 密钥。token 以 OpenShell `--env` 注入到
  `SIQ_OPENSHELL_EGRESS_IDENTITY_TOKEN` 和
  `SIQ_OPENSHELL_DATA_IDENTITY_TOKEN`；它们不会写入镜像。
- `siq_fetch.py` 只向 egress broker 发送 egress token，`pg_query.py` 只向 data
  broker 发送 data token。错误 audience、签名、profile、过期 token、重复 header
  或缺少 header 均在访问上游前失败关闭。
- broker 仅对业务 POST 路由要求身份；无敏感内容的 health 路由保持可探测。
- 每请求审计从已验证 claims 覆盖进程级默认上下文，不记录 token、SQL、向量、
  URL、Prompt 或请求正文。

OpenShell 0.0.83 目前没有本项目可用的 secret-file/env 注入接口，因此 token 会在
创建 sandbox 的 CLI 子进程 argv 中短暂出现。lifecycle 将其列入 redaction 集合，
但同 UID 主动读取 `/proc` 的风险仍属于 V0.6 已声明的宿主边界。不得把 token 改写
到可提交配置来规避这一限制。

## 查看当前模式

以下命令只读，不启动或停止服务：

```bash
scripts/openshell/status_brokers.sh
scripts/openshell/status_brokers.sh --require-request-identity
```

第二条只有在两个 broker 都使用同一个当前私钥、且 PID/cmdline/listener/health
全部交叉验证成功时返回 `ok: true`。正式 lifecycle 在创建 transaction 之前执行
同一严格检查；兼容模式 broker 会返回 `NO_GO`，不会创建 sandbox。

## 切换到严格模式

在维护窗口内确认没有正式 active run，再执行：

```bash
test ! -e var/openshell/siq-analysis/active-run.json
scripts/openshell/stop_brokers.sh
scripts/openshell/start_brokers.sh --require-request-identity
scripts/openshell/status_brokers.sh --require-request-identity
```

`start_all.sh` 不会自动把兼容模式升级为严格模式，也不会自动重启 broker。严格
broker 已存在时，默认启动流程可以验证并复用它们。未携带正式 token 的 broker
业务请求会被拒绝，这是预期行为；宿主 Hermes 未配置 broker URL 时不受影响。

## 回退兼容模式

```bash
test ! -e var/openshell/siq-analysis/active-run.json
scripts/openshell/stop_brokers.sh
scripts/openshell/start_brokers.sh
scripts/openshell/status_brokers.sh
```

这不会停止 gateway、宿主 Hermes、模型或数据库，也不会修改 SIQ 默认 runtime。

## 密钥轮换

轮换会立即撤销所有旧 token，因此仅允许两个 broker 已停止且不存在正式 active
run 时执行：

```bash
scripts/openshell/stop_brokers.sh
scripts/openshell/rotate_broker_identity_key.sh
scripts/openshell/start_brokers.sh --require-request-identity
scripts/openshell/status_brokers.sh --require-request-identity
```

轮换使用私有父目录、`O_NOFOLLOW` 和原子 rename；输出只包含新密钥 SHA-256，
不包含密钥。若 broker 仍在运行或 active run 存在，操作失败关闭。

## Transaction 清理

正式 start 将两个 token 和一次性 Hermes API key/run nonce 放在单次 `0600` run
目录，并将四个文件共同绑定到 transaction 的 `secrets` receipt。正常 stop、失败
回滚和 recover 都会验证 receipt 后删除：

```text
api.key
run.nonce
egress.identity.token
data.identity.token
```

token 过期不妨碍身份绑定的资源清理，但会让后续 broker 请求失败。超过 6 小时的
任务必须结束当前 transaction 后重新启动，不能延长或复用旧 token。

## Pilot 复用

NOT_PRODUCTION pilot 必须调用
`scripts.openshell.broker_request_identity.issue_broker_identities()`，不能自行拼 claims
或共用一个无 audience token。调用方传入该 pilot 的真实
`profile/run_id/sandbox_id/session_id/policy_digest/run_nonce_digest` 和受控 TTL；返回的
bundle 提供固定 `as_environment()` 映射及 `secret_values()` redaction 集合。pilot
仍负责把 token 写入自己的 ignored `0600` transaction state、在失败和正常停止时
删除，并把 `readiness_effect` 保持为 `none`。正式 lifecycle 已使用同一 helper。

## 离线验证

```bash
PYTHONPATH=. pytest -q \
  scripts/openshell/tests/test_broker_request_identity.py \
  scripts/openshell/tests/test_broker_lifecycle.py \
  scripts/openshell/tests/test_egress_guard.py \
  scripts/openshell/tests/test_read_only_data_broker.py \
  scripts/openshell/tests/test_siq_fetch.py \
  scripts/openshell/tests/test_siq_analysis_lifecycle.py \
  apps/api/tests/test_hermes_pg_query.py
```
