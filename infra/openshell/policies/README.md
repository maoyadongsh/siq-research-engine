# SIQ OpenShell 策略 V0.6

`base.yaml` 和 `profiles/siq-analysis.yaml` 使用 JSON 语法保存；JSON 是合法 YAML，因此可以由 OpenShell 直接读取，同时让 SIQ 编译器只依赖 Python 标准库。

## 文件权限模型

OpenShell Landlock 采用授权叠加：只读父目录可以有可写子目录，但可写父目录不能再用更具体的只读条目锁回去。因此本策略固定：

1. `include_workdir` 必须为 `false`；
2. 项目根只读；
3. 当前任务公司的完整 `analysis/` 根、`siq_analysis` 自身运行态子目录、SQLite/WAL 文件和四个 gateway 运行元数据文件可写；`analysis/` 下的任务叶目录不要求预先存在，智能体可正常创建目录、下载解析、覆盖、改名和清理当前任务文件；
4. 任何可写路径只要覆盖 immutable registry 条目、项目代码、Prompt 或 workflow，编译立即失败；
5. 总路径数超过 OpenShell 的 256 条限制时编译失败。
6. `siq_analysis` profile 必须声明全部 Hermes runtime 文件。默认编译仍检查宿主文件；正式 lifecycle 只接受与候选镜像、smoke 脚本和完整 metadata 检查集绑定的 `candidate-image` attestation，不在宿主伪造 marker。
7. 不允许把整个 `/etc` 授予智能体。策略只列出 Hermes/TLS/DNS/字体/动态链接所需的公共配置；OpenShell 自身注入的 sandbox JWT、client certificate 和 client key 仅按固定控制挂载提供运行时读取，并由只读 mount 和写入负向测试保护。其他宿主凭据、用户配置和 OpenShell 私有状态不挂载。

编译器会拒绝任何未在代码中固定的项目写路径，并把项目外写权限锁定为 `/dev/null`、`/sandbox` 和 `/tmp`。`artifacts`、API/会议/解析服务运行目录、`var/meetings/hermes`、`var/openshell/xdg`、TLS、gateway DB 和备份均不对 `siq_analysis` 开放写权限。项目根挂载还必须从源头排除 `.env`、真实 `infra/env/*.env`、`env/*.env` 和用户凭据目录；Landlock 不能靠省略路径隐藏已经位于只读项目挂载中的文件。

## 网络模型

Sandbox 的普通二进制只直接访问 SIQ egress guard、read-only data broker 和明确列出的内部服务。模型与搜索使用 OpenShell provider；公开网页读取和未知小 JSON POST 使用 egress guard。内部服务白名单固定保留 `8004/8006/8007/8013`，服务离线不改变 policy。OpenShell 原生 REST policy 不检查通用 Content-Type 或任意 body size，所以下列规则由宿主 guard 实现：

- 未知域 JSON POST 不超过 128 KiB 时审计放行；
- 超过阈值或 multipart/octet-stream 上传到非白名单域时阻断；
- 模型和搜索必须走 provider；批准的 GitHub/Lark 小 JSON 操作按独立规则放行。

正式镜像、mount safety、runtime attestation、内部寻址、egress/data brokers 和批量删除守卫均已实现。MiniMax、StepFun、Kimi、Tavily 已配置；Exa 延后到切流后配置，`8004/8006` 当前禁用且为 optional。默认流量仍不能切换，因为正式删除、出网、审计、回滚和 Host/OpenShell A/B 证据尚未全部完成，completion 会继续失败关闭。

候选 BYOC mount 必须在启动前执行 `python3 scripts/openshell/check_mount_safety.py --mount-root <staged-root>`。禁止直接以当前仓库根作为 `<staged-root>`；当前仓库含真实 `.env`、Hermes auth 和宿主运行状态，检查失败是预期结果。
