# SIQ OpenShell Provider 资产

本目录只包含固定到 OpenShell `0.0.83` 的无密钥 Provider 定义。这里不包含 Provider 值、生成的 gateway 状态或实体化 Hermes `auth.json`。

## 内容

- `manifest.json` 将稳定 Provider 实例名绑定到 profile 文件和凭据环境变量 key。
- `profiles/*.yaml` 是 OpenShell `0.0.83` 自定义 Provider profile。每个 endpoint 都使用强制 REST method/path 规则。
- `hermes/minimax-cn-auth-pool.template.json` 保留两项 Hermes MiniMax 中国池，使用 OpenShell placeholder，priority 为 `0` 和 `10`。

MiniMax profile 保持当前 pool base URL 不变，并允许规范 `/v1/messages` 路由和当前 Hermes/Anthropic client 兼容路由 `/v1/v1/messages`。这避免在 Host baseline 保持稳定时引入 OpenShell deny；规范化 base URL 是独立 Hermes 配置变更，不在这里执行。

Tavily profile 启用 `request_body_credential_rewrite`。OpenShell `0.0.83` 对该功能最多缓冲 `262144` 字节（256 KiB）。边界内受支持的 UTF-8 JSON、form 和 text 请求会被改写；更大或 unresolved-placeholder 请求会被拒绝。Exa 和模型 API 的凭据保留在请求 header 中，不启用 body rewrite。

已评审的 SIQ Web 配置继续使用现有 Tavily 和 Exa `/search` 调用，不改变请求或响应合同。profiles 还会暴露 Provider 检索能力：Tavily search/extract/crawl/map/research 与 Exa search/contents/answer/context/agent research。只有异步结果树使用已评审 `/**` 前缀规则。文件上传形态、账号管理、imports、monitors、websets、任意 method 和 host-wide path access 均不授权。

Tavily 和 Exa 是已批准的检索处理器，因此它们的 Provider 路由不使用通用未知域 128 KiB 阈值。这不意味着它们是通用发布目标，也不提供本地文件或原始字节输入面。能读取私有数据的 sandbox 仍可把文本编码进合法查询；防止这种语义通道需要 V0.6 任务书中记录的能力隔离检索模式。

## 校验与 dry-run

默认 provision 模式不修改状态，也不读取密钥文件或凭据环境变量：

```bash
python3 scripts/openshell/provision_siq_providers.py
```

成功输出只包含 Provider 实例名和确定性摘要 SHA-256。服务端 schema lint 也不修改状态，但要求隔离 SIQ gateway 可达。按文件 lint 未注册 profile：

```bash
scripts/openshell/run_cli.sh provider profile lint \
  --file infra/openshell/providers/profiles/siq-tavily-search.yaml
```

当目录中也包含已经注册到 gateway 的 profiles 时，OpenShell `0.0.83` 会拒绝目录 lint。provision 命令通过只 lint 将要导入的 profiles 来支持增量运行；已有 profiles 会完整导出并比较，包括 `resource_version`，通过后才允许更新。

## 显式 provision

provision 有意由 `--apply` 和精确 gateway 确认门控。脚本要求 OpenShell `0.0.83`，并验证 active registration 是本地 mTLS endpoint `https://127.0.0.1:17671`。它还验证 gateway 已经具备 `providers_v2_enabled=true`；脚本永远不修改 gateway settings，也不把 Provider attach 到 sandbox。

apply 会持有 sandbox lifecycle 命令共用的项目维护锁，并要求 gateway 中没有 sandbox。这防止 sandbox 在 quiescence check 和非事务 profile/provider update 之间启动。provision 前请停止并删除开发 sandbox；失败运行可以在修复报告条件后重复执行。

密钥可以来自当前进程环境、一个或多个严格 dotenv 文件，以及包含已评审两项 MiniMax 中国池的 Hermes `auth.json`。密钥文件必须是当前用户拥有的普通非 symlink 文件，且没有 group/other permission bits（通常 mode `0600`）。值只通过子进程环境传给 `openshell provider create/update`。CLI 参数只包含凭据 key 名称，不包含值；子进程输出不会转发到日志。

OpenShell `0.0.83` 会把静态 Provider credential 值持久化到本地 gateway 数据库。SIQ 对应文件是 `var/openshell/gateway/siq-openshell-dev/openshell.db`：它必须保持为 Git 忽略运行树下单链接、owner-only、`0600` 文件，且永远不 mount 到 sandbox。Gateway 数据库备份具有同等密级。本版本没有提供应用层静态加密证据，因此宿主文件权限和磁盘加密属于凭据边界；脱敏 artifact 绝不能包含数据库或其内容。

操作示例（不要把这些文件存入 Git）：

```bash
chmod 600 /restricted/siq-provider.env /restricted/hermes-auth.json
python3 scripts/openshell/provision_siq_providers.py \
  --apply \
  --confirm-gateway siq-openshell-dev \
  --secret-file /restricted/siq-provider.env \
  --minimax-auth-json /restricted/hermes-auth.json
```

全部 Provider apply 会在任何必需凭据缺失时失败关闭。可用重复 `--provider NAME` 参数执行已评审的部分操作。启用 provider v2、sandbox attach 和生成运行态 Hermes `auth.json` 仍是独立生命周期步骤，因此该命令不能静默改变 sandbox 或当前 Host 运行面。
