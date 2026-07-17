# GitHub 上的 OpenShell + Hermes 开源实现

> 核验日期：2026-07-15
>
> 结论：OpenShell 可以直接承载 Hermes，不要求引入 NemoClaw。官方和社区已有
> 多种实现，但没有一个项目可原样满足 SIQ 的同路径运行、固化数据只读、任务级
> 写路径、数据库只读和未知上传门禁要求。

## 参考优先级

| 优先级 | 项目 | 核验版本 | 定位 | SIQ 用法 |
| --- | --- | --- | --- | --- |
| 1 | [NVIDIA/NemoClaw](https://github.com/NVIDIA/NemoClaw/tree/45b1cb5a01f3408743c8479772af4ad9d4a91fc8) | `v0.0.83`, `45b1cb5...` | 官方完整 Hermes 集成和 live E2E | 学习镜像、manifest、provider、状态和 E2E，不引入运行时 |
| 2 | [NVIDIA/nemoclaw-community](https://github.com/NVIDIA/nemoclaw-community/tree/24ef85f1653d84589c8a523ff1030a16ff21f35c) | `24ef85f...` | NVIDIA 社区完整业务样例 | 学习宿主只读 broker、受控上传 relay 和两阶段启用 |
| 3 | [ppritcha/hermesshell](https://github.com/ppritcha/hermesshell/tree/9e012880d76131462dbdc99f8c581520a4edd556) | `v1.0.1`, `9e01288...` | 社区 lifecycle CLI、policy tier 和 snapshot | 学习 registry、preset merge 和 rebuild UX |
| 4 | [windoliver/agentenv](https://github.com/windoliver/agentenv/tree/d2eecc220838cc36b13a1da16f87451235258eee) | `d2eecc2...` | Alpha 阶段的通用 agent/sandbox 编排 | 学习 runtime、agent、context、inference 分层和 lockfile |
| 5 | [raja-patnaik/hermes-openshell](https://github.com/raja-patnaik/hermes-openshell/tree/f0d68164ffb1a5be89c7d4576669f278633b80ea) | `f0d6816...` | 最小直接集成 | 只参考 create/policy/provider/forward 命令骨架 |
| 6 | [shanemcd/openshell-kubevirt](https://github.com/shanemcd/openshell-kubevirt/tree/81e62fb56c6f6fe22b929664a94cfa692f73e97f) | `81e62fb...` | 实验性 KubeVirt/VM 路线 | 只参考持久卷、Secret 和 supervisor 生命周期 |

## 官方实现的版本边界

NemoClaw `v0.0.83` 是目前最完整的 Hermes 参考，但它不是 OpenShell
`v0.0.83` 的兼容证明：

- `nemoclaw-blueprint/blueprint.yaml` 固定 `min_openshell_version` 和
  `max_openshell_version` 都为 `0.0.72`；
- `scripts/install-openshell.sh` 同样固定 OpenShell `0.0.72`；
- `agents/hermes/manifest.yaml` 期望 Hermes `0.18.0`；
- SIQ 当前冻结 Hermes `0.13.0`，并已经独立验证 OpenShell `0.0.83` 的最小
  Hermes PoC，因此不能复制 NemoClaw 的版本组合或安装器。

重点参考文件：

- [Hermes manifest](https://github.com/NVIDIA/NemoClaw/blob/45b1cb5a01f3408743c8479772af4ad9d4a91fc8/agents/hermes/manifest.yaml)
- [Hermes Dockerfile](https://github.com/NVIDIA/NemoClaw/blob/45b1cb5a01f3408743c8479772af4ad9d4a91fc8/agents/hermes/Dockerfile)
- [Hermes startup](https://github.com/NVIDIA/NemoClaw/blob/45b1cb5a01f3408743c8479772af4ad9d4a91fc8/agents/hermes/start.sh)
- [Hermes policy additions](https://github.com/NVIDIA/NemoClaw/blob/45b1cb5a01f3408743c8479772af4ad9d4a91fc8/agents/hermes/policy-additions.yaml)
- [OpenShell installer pin](https://github.com/NVIDIA/NemoClaw/blob/45b1cb5a01f3408743c8479772af4ad9d4a91fc8/scripts/install-openshell.sh)
- [Hermes live E2E](https://github.com/NVIDIA/NemoClaw/blob/45b1cb5a01f3408743c8479772af4ad9d4a91fc8/test/e2e/live/hermes-e2e.test.ts)

## 可直接吸收到 SIQ 的设计

1. 用 agent manifest 明确 Hermes 版本、启动命令、health、内部/转发端口、配置
   文件、状态目录和 SQLite backup 策略。
2. 镜像、Hermes commit、OpenShell 三个二进制、安装归档和依赖全部固定 digest，
   构建时校验，运行时再次 attestation。
3. 配置由宿主生成并校验哈希；sandbox 仅持有 provider placeholder，不落真实
   API key、数据库口令或 OAuth token。
4. 把不可变配置与可写 memory/session/checkpoint 分开，并在启动前物化 Hermes
   必需的 SQLite、PID、lock 和 metadata 文件。
5. Hermes 只监听 sandbox loopback，再用受控 forward 暴露 API；forward、Hermes
   health 和 bearer auth 分别验收。
6. lifecycle 使用显式 phase：preflight、snapshot、create、health、verify、publish、
   stop、restore；每一步都保存可回滚状态。
7. 数据库查询与上传使用不同的宿主 broker。只读数据 broker 不提供 mutation
   API；上传 relay 单独绑定目标、方法和凭据。
8. provider 按任务精确绑定，路由设置后运行 smoke；不得把 gateway 中所有 provider
   自动挂给每个 sandbox。

## 不应照搬的配置

- `latest` OpenShell base、Hermes `main`、未固定 digest 的镜像或依赖；
- `landlock.compatibility: best_effort`；SIQ 保持 `hard_requirement`；
- `include_workdir: true` 加整个 `/sandbox` 或整个项目根可写；
- 把 `.env`、`auth.json` 或真实 provider secret 持久化到 Hermes home；
- 开放全部 package registry、GitHub 写入或 inference 任意 method/path；
- 自动绑定全部 provider；
- 使用 `rm -rf` 清空备份目录后再下载状态；
- 安装 NemoClaw Hermes plugin。它包含 `pre_llm_call`、skill reload 和多处 runtime
  patch，会改变提示上下文、工具选择和输出行为，不属于纯安全层。

## 对 SIQ 当前实现的影响

这次核验不要求改变当前架构。SIQ 已采用更适合业务边界的方式：

```text
固定 Hermes 0.13.0 + OpenShell 0.0.83
  -> 项目内独立 gateway
  -> 正式 BYOC image
  -> task-scoped 12-mount contract
  -> 代码/Prompt/workflow/固化 Wiki 只读
  -> analysis + Hermes memory/runtime 精确可写
  -> provider 精确绑定
  -> PostgreSQL/Milvus 只读 broker
  -> 未知上传 egress broker
```

因此后续只吸收 manifest、配置完整性、provider placeholder、typed lifecycle 和
live E2E 思路，不引入 NemoClaw，也不改变 Hermes 的现有输出路径和提示链。
