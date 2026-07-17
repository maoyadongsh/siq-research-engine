# OpenShell 项目运行态

`var/openshell/` 是 SIQ 管理的 OpenShell 本地状态根目录。目录本身位于项目内，但它同时包含凭据与非敏感运行态，因此除本 README 和 `manifests/*.sanitized.json|md` 外默认被 Git 忽略。这里的默认忽略不是禁止发布 OpenShell 资产；非敏感内容经脱敏导出到 `artifacts/openshell/**` 后即可提交。

该目录服务于 SIQ 自研 NVIDIA OpenShell + Hermes 演示/灰度控制面。网关 TLS/数据库、Provider 清单、Broker 状态、资源池注册表、沙箱生命周期、toolchain 摘要、proof、audit 和临时运行记录都可能出现在这里；它们用于支撑公司范围自动创建、对话沙箱代际、租约/隔离/恢复、Host 回退和空闲 TTL 清理，但原件默认不进入 Git。

## 可提交

- 本 README；
- `manifests/` 中不含凭据或机器身份的版本、资源摘要和兼容性结论；
- 上述文件必须同时进入 `artifacts/openshell/tracked-artifacts.json`，并通过内容、摘要和大文件门禁。

## 日志和运行证据先脱敏再提交

- `audit/*.jsonl`：严格校验 schema 后只导出聚合计数、错误码、延迟和 digest；
- gateway、broker、forward 等 operational log：由专用导出器移除凭据和私有正文，再导出可审查的日志元数据；
- provider、service、policy、mount plan、registry 和 runtime 状态：只导出稳定契约、计数、版本和摘要。

脱敏副本统一写入 `artifacts/openshell/**`，不在 `var/openshell/` 中原地改名后强制提交。

## 不提交原件

- `secrets/`、`xdg/` 认证状态、TLS 私钥、API key、密码、token、cookie、nonce 和数据库 DSN；
- `gateway/*.db`、session/response DB、sandbox filesystem、PID、socket、lock 和临时端口状态；
- 未经脱敏的 gateway/broker/Hermes 日志、完整 trace、Prompt、对话、请求/响应正文和附件正文；
- Hermes bundle、工作树 patch、数据库备份、toolchain 二进制、build tree、镜像层和依赖副本。

PID、socket、lock、构建缓存和二进制运行态也保持忽略，因为它们不可移植且不适合 Git；对应版本、摘要和状态可脱敏后提交。本机运行态目录使用 `0700`，文件默认使用 `0600`。诊断工具只能输出凭据是否配置，不能输出凭据值。
