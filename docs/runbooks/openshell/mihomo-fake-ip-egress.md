# Mihomo fake-IP 出网兼容

本兼容层仅解决 Clash Verge/Mihomo TUN `fake-ip` 模式下，宿主系统 DNS 返回
`198.18.0.0/15` 保留地址而导致 OpenShell egress guard 失败关闭的问题。默认关闭，
不会把 `198.18.0.0/15` 或其中任何地址直接视为公网。

## 安全边界

只有同时满足以下条件才会查询 Mihomo 控制接口：

1. 目标是域名，不是 IP literal；
2. 系统 DNS 的全部结果都落入显式配置的 fake-IP 子网；
3. 子网位于 `198.18.0.0/15` 内，且必须为 `/16` 或更窄；
4. 控制端点是经过 owner、目录权限、symlink 和 socket 类型检查的 Unix socket；
5. `/version` 返回可验证的 Mihomo Meta 身份；
6. `/dns/query` 的 Question、CNAME 归属和 A/AAAA 记录结构通过检查。

控制接口返回的真实地址仍需经过公网地址判定、egress allowlist、逐请求审计、
一次性 DNS pinned connector 和实际 TCP peer 匹配。mixed fake/public DNS、私网或
metadata 地址、unsafe socket、非 Meta 响应、超时、无 peer、重定向漂移和任何解析
异常都失败关闭。每个重定向 hop 都重新执行同一流程。

## 项目启动与显式覆盖

项目跟踪 `infra/openshell/egress/mihomo-runtime.json`，模式固定为
`auto_if_socket_present`。`start_all.sh` 启动 broker 时，lifecycle 只在该配置通过
owner、mode、symlink、schema 检查且受信 Unix socket 实际存在时注入下面三个值；
其他机器没有该 socket 时继续使用普通 DNS。任意一个同名环境变量已显式设置时，
项目配置不会覆盖 operator 选择。

2026-07-16 已在清空三个显式变量后完成 broker stop/start，并再次运行
`run_egress_boundary_proof.py` 通过 7 个真实用例。该记录证明当前项目环境的重启配置，
不代表其他机器必然安装或启用 Mihomo。

手工启用或诊断时可显式提供：

这三个值不是秘密，不得写入 reader secret 文件：

```bash
export SIQ_OPENSHELL_MIHOMO_FAKE_IP_COMPAT=1
export SIQ_OPENSHELL_MIHOMO_CONTROL_SOCKET=/tmp/verge/verge-mihomo.sock
export SIQ_OPENSHELL_MIHOMO_FAKE_IP_RANGE=198.18.0.0/16
```

broker 不会原地重载环境。只能在确认没有正式 active run 的维护窗口执行：

```bash
scripts/openshell/stop_brokers.sh
scripts/openshell/start_brokers.sh --require-request-identity
scripts/openshell/status_brokers.sh --require-request-identity
```

egress health 必须显示：

```text
dns_resolver_mode=mihomo_fake_ip_verified
request_identity_required=true
```

随后使用短期、audience 为 `siq-egress-guard` 的正式 token 验证一个公开
`HEAD` 请求，并重复 metadata、私网、mixed DNS 和跨域重定向负向测试。不得用关闭
peer 检查或允许整个 benchmark range 的方式绕过失败。

## 回滚

在维护窗口停止 broker，清除三个环境变量并按严格身份模式重新启动。回滚 fake-IP
兼容不应关闭 broker request identity：

```bash
scripts/openshell/stop_brokers.sh
unset SIQ_OPENSHELL_MIHOMO_FAKE_IP_COMPAT
unset SIQ_OPENSHELL_MIHOMO_CONTROL_SOCKET
unset SIQ_OPENSHELL_MIHOMO_FAKE_IP_RANGE
scripts/openshell/start_brokers.sh --require-request-identity
```

## 残余风险

- peer 捕获依赖锁定版本 `aiohttp==3.13.3` 的 connector 生命周期，升级 aiohttp 后必须
  重跑真实 GET/HEAD 和无 peer 失败关闭测试；
- 该模式信任宿主 operator 管理的 Mihomo 进程和控制 socket；Mihomo 被攻陷属于宿主
  网络控制面失陷，但下游公网、SSRF、审计和 peer 检查仍保留；
- 默认关闭且运行中的 broker 不会自动继承新环境，因此源码就绪不等于线上已启用。

回归测试：

```bash
python3 -m pytest -q scripts/openshell/tests/test_egress_guard.py
```
