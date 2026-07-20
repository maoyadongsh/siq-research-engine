# SIQ 本地运行态目录

## 目录定位

`var/` 是 SIQ 新增本地运行态的推荐根目录。与 `data/` 的历史兼容角色不同，`var/` 代表的是面向未来的、更清晰的运行态分层设计。

OpenShell 是 `var/` 当前最重要的新增运行态之一：Gateway、provider、broker、pool registry、proof、audit 和 toolchain 私有状态集中在 `var/openshell/`，用于支撑 NVIDIA OpenShell + Hermes demo/canary control plane，但原件默认不提交。

## 主要内容

推荐子目录包括：

- `var/api`
- `var/pdf-parser`
- `var/document-parser`
- `var/market-report-finder`
- `var/hermes`
- `var/wiki`
- `var/db`
- `var/logs`
- `var/cache`
- `var/runtimes`

## 当前最新状态

`var/` 是新的运行态收口方向，但项目仍保留 `data/` 兼容路径。新增服务或新增运行状态时，应优先考虑：

```text
SIQ_RUNTIME_ROOT=/home/maoyd/siq-research-engine/var
```

然后再由各服务派生专属目录。这样能把本机状态、长期事实层、评测样本和一次性产物分开，降低部署和清理成本。

## 与其他数据目录的边界

- `var/`：新增本地运行态推荐目录。
- `data/`：历史兼容运行态目录。
- `artifacts/`：构建、测试、评测和批处理生成产物。
- `datasets/`：可版本化稳定样本。

如果某份内容属于“本机运行时状态”，新设计优先考虑放进 `var/`，而不是继续加深 `data/` 依赖。

## 可提交与不可提交内容

可提交：

- 本 README
- 必要的 `.gitkeep`
- `.gitignore` 明确放行且通过 manifest 绑定的 OpenShell 脱敏 README/manifest

不可提交：

- `*.db`
- 上传文件、下载文件、解析产物、未经脱敏的日志、缓存、模型权重
- 用户会话、附件、聊天响应和任何本地敏感数据
- OpenShell TLS 私钥、provider 凭据、gateway 数据库、未经脱敏的日志、trace、缓存和回退备份

OpenShell 运行树采用“默认忽略、脱敏导出、manifest 精确绑定”，而不是提交整个 `var/openshell/`。非敏感运行证据和日志导出到 `artifacts/openshell/` 后可以提交；凭据值、绝对用户目录、请求正文和用户内容不得进入可发布副本。

## 运行或使用建议

- 新增服务默认路径时，优先引入 `SIQ_RUNTIME_ROOT` 或领域专属 `SIQ_*_DATA_DIR` 指向 `var/`。
- 从 `data/` 迁移运行态时，逐步迁移并保留兼容路径说明，避免突然破坏现有本地环境。
- 按领域分目录，避免不同服务在同一层乱写文件。

## 维护原则

- 把 `var/` 当作新的运行态主路径，而不是另一个杂物目录。
- README 和脚本中提到的运行路径应明确是“推荐新值”还是“兼容旧值”。
- 不把长期需要共享的数据误存进 `var/`；长期资产要么进入事实层，要么进入可版本化样本层。

## 敏感运行面隔离

`var/openshell` 保存 gateway、mTLS、registry、lease、sandbox 和 broker 私有状态；`var/meetings` 保存会议 worker/model target 等运行状态。它们不得被 Agent 当普通知识目录挂载，也不得复制进公开 artifact。真正需要审计的内容应通过专用导出器生成最小、脱敏、带 manifest 的 `artifacts/` 副本。

运行态删除与业务删除也不是一回事：清理 PID/cache/sandbox 不能删除 Wiki 事实，删除会议/声纹数据则必须遵循授权、tombstone、留存和恢复边界。
