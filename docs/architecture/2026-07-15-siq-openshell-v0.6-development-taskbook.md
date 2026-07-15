# SIQ OpenShell 宽松安全门禁 V0.6 开发任务书

> 状态：待实施
> 日期：2026-07-15
> 适用仓库：`/home/maoyd/siq-research-engine`
> 目标执行者：Codex 或具备仓库读写权限的开发人员
> 本期定位：在不改变智能体正常输出路径、工具行为和回答质量的前提下，将 OpenShell 作为 Hermes 执行面的最后一道安全保险。

## 1. 项目背景

SIQ Research Engine 当前由 FastAPI、React、文档解析服务、市场报告服务、PostgreSQL、Milvus 和多组 Hermes profiles 组成。Hermes 通过独立 `/v1/runs` gateway 与 API 通信，承担通用问答、智能分析、事实核查、持续跟踪、法务和一级市场 IC 多角色任务。

当前 Hermes profiles 普遍具备 `terminal`、`file`、`code_execution` 和 `web` 能力。部分 profile 会直接执行仓库中的 Python 脚本，并向以下路径写入派生产物：

- `data/wiki/companies/*/analysis/**`
- `data/wiki/companies/*/factcheck/**`
- `data/wiki/companies/*/tracking/**`
- `data/wiki/companies/*/legal/**`
- `artifacts/**`
- `var/**`
- `data/hermes/home/profiles/*` 下的运行时状态目录和数据库

与此同时，仓库代码、Hermes Prompt、skills、workflow 配置、入库后的 Wiki 事实包、原始文档和正式数据库事实不应被智能体直接修改。

本任务书要求引入 OpenShell，但不采用 NemoClaw，不重构 Hermes 的业务角色，不替换现有 `/v1/runs` 契约，不改变现有智能体可见的绝对路径。

## 2. 目标与非目标

### 2.1 必须实现的目标

1. Hermes gateway 运行在 OpenShell sandbox 中，SIQ API 继续通过现有 `/v1/runs` 接口调用。
2. 智能体读取项目代码、Prompt、skills、workflow 和固化数据的行为保持不变。
3. 智能体不能修改项目源代码、Agent 控制面文件及已固化入库数据。
4. 智能体仍可在现有路径生成分析、核查、跟踪、法务、任务和运行时产物。
5. 聊天、会议、任务、审计、Agent memory 和 Milvus memory 写入继续由宿主 FastAPI 正常完成。
6. 公开网页读取、模型调用、Tavily/Exa 检索、本地模型和 SIQ 内部服务正常放行。
7. 明确的未知公网文件上传、系统提权、Docker socket 访问和 sandbox 逃逸行为被阻断。
8. 策略拒绝、网络请求和 sandbox 生命周期能够关联 SIQ `run_id`、`task_id`、profile 和 session。
9. 支持观察模式、灰度启用、快速回滚和 A/B 质量评测。

### 2.2 本期明确不做

1. 不引入 NemoClaw。
2. 不引入 NeMo Agent Toolkit，不改变多 Agent 调度逻辑。
3. 不将所有 SIQ 服务迁移到 OpenShell。
4. 不重构现有报告输出路径。
5. 不把所有公网访问改成严格域名白名单。
6. 不实现通用 DLP 或请求体语义识别系统。
7. 不实现任意单文件修改审批。
8. 不改变 Hermes 模型、Prompt 内容、temperature、上下文长度和 fallback 顺序。
9. 不修改现有数据库 schema，除非为审计记录增加独立、向后兼容的表或字段且获得单独评审。

### 2.3 OpenShell 项目内目录约束

所有由 SIQ 维护、生成或消费的 OpenShell 相关文件必须位于当前项目根目录下，不得把 SIQ policy、sandbox 定义、运行状态、日志或评测产物散落到 `~/.openshell`、`~/.config` 或其他个人目录。

规范目录结构如下：

```text
siq-research-engine/
├── infra/openshell/                 # 版本化基础设施源码
│   ├── README.md
│   ├── schemas/                     # registry、policy 辅助 schema
│   ├── policies/                    # base/profile policy 模板
│   ├── sandbox/                     # BYOC Dockerfile、entrypoint、依赖锁定
│   └── providers/                   # 无密钥 provider 模板
├── scripts/openshell/               # 启动、停止、诊断、编译、灰度和回滚命令
├── var/openshell/                   # 提交白名单管理的运行状态与可复现快照
│   ├── README.md                    # 提交：目录说明与数据分级
│   ├── examples/                    # 提交：脱敏 registry/policy/audit 示例
│   ├── manifests/                   # 提交：版本和资源清单的脱敏快照
│   ├── gateway/
│   ├── sandboxes/
│   ├── policies/
│   ├── registry/
│   ├── audit/
│   ├── logs/
│   ├── cache/
│   └── xdg/                         # wrapper 可定向的 XDG config/state/cache
├── artifacts/openshell/             # 基线、A/B、安全测试和发布证据
└── docs/runbooks/openshell/          # 运维、故障处理、升级和回滚说明
```

约束：

1. `infra/openshell/**` 和 `scripts/openshell/**` 必须进入版本控制，且对 sandbox 内智能体只读。
2. `var/openshell/**` 采用“默认忽略、文件级白名单提交”，而不是整个目录一律忽略。必须提交 `README.md`、脱敏 examples、稳定 manifest、schema-compatible policy 快照和安全扫描报告。
3. `artifacts/openshell/**` 采用同样的提交白名单。参赛所需的脱敏基线摘要、A/B 质量结果、安全测试结果和演示证据应提交；原始日志、大体积产物和含用户内容的 trace 不提交。
4. 所有管理命令必须通过项目 wrapper 设置 `SIQ_PROJECT_ROOT`、`SIQ_RUNTIME_ROOT`、`SIQ_ARTIFACTS_ROOT`，并在 OpenShell 支持时将 `XDG_CONFIG_HOME`、`XDG_STATE_HOME`、`XDG_CACHE_HOME` 定向到 `var/openshell/xdg/`。
5. wrapper 启动前必须打印实际使用的项目目录和 OpenShell 状态目录；检测到意外写入用户 home 时应告警或失败关闭。
6. 真实 API Key、token、数据库密码不得写入 `infra/openshell/providers/`；该目录只保存字段模板和 provider 名称。
7. OpenShell 二进制、Docker daemon 的镜像层、容器层和内核级运行时状态仍由操作系统或容器运行时管理，不复制到仓库。项目必须记录其版本和资源 ID，但不得把这些外部状态伪装成项目内文件。
8. 如果当前 OpenShell 版本不支持重定向某项 CLI metadata，必须在 T0/T4 中记录该例外、实际路径、敏感性和清理方式；不得静默散落状态。
9. 任何准备提交的 `var/openshell/**` 或 `artifacts/openshell/**` 文件必须先通过自动脱敏和 secret scan；扫描失败时禁止提交。

提交策略如下：

| 内容 | 是否提交 | 处理要求 |
|---|---|---|
| policy 模板和稳定 profile 规则 | 是 | 不包含密钥和机器专属认证信息 |
| 编译后 policy 示例 | 是 | 归一化项目根为 `${SIQ_PROJECT_ROOT}`，移除本机资源 ID |
| immutable registry 示例 | 是 | 使用公开/合成样本或脱敏路径，保留 schema 和摘要算法 |
| OpenShell/Docker/Hermes 版本 manifest | 是 | 允许版本号和镜像 digest，不含私有 registry token |
| A/B 质量摘要和安全测试报告 | 是 | 不含 Prompt、用户输入、附件正文和凭据 |
| 审计汇总、计数和拒绝类型样例 | 是 | host/path 使用安全投影，ID 脱敏 |
| gateway 认证、provider credentials | 否 | 必须忽略，禁止生成可提交副本 |
| XDG auth/config 中的 token | 否 | 必须忽略 |
| socket、PID、lock、临时端口状态 | 否 | 机器绑定且无复现价值 |
| 原始审计 JSONL 和完整网络日志 | 否 | 可能包含路径、用户行为或敏感 metadata |
| sandbox filesystem、session DB、response store | 否 | 可能含 Prompt、记忆和用户内容 |
| 大体积镜像层和容器数据 | 否 | 由 Docker/OpenShell 管理，提交 digest 即可 |

建议 `.gitignore` 使用显式例外，而不是简单忽略整个目录：

```gitignore
var/openshell/**
!var/openshell/README.md
!var/openshell/examples/
!var/openshell/examples/**
!var/openshell/manifests/
!var/openshell/manifests/**

artifacts/openshell/**
!artifacts/openshell/README.md
!artifacts/openshell/v0.6/
!artifacts/openshell/v0.6/*.sanitized.json
!artifacts/openshell/v0.6/*.sanitized.md
```

实际实现必须同时提供 `scripts/openshell/sanitize_artifacts.py` 和 `scripts/openshell/check_tracked_state.py`，不能只依赖开发者人工判断。

## 3. 设计原则

### 3.1 宽松运行，保护控制面和事实面

权限分为四个平面：

| 平面 | 内容 | 权限 |
|---|---|---|
| 控制面 | 项目代码、Prompt、profiles、skills、workflow、安全策略、凭据 | 智能体只读或不可见 |
| 事实面 | 固化 Wiki、原始文档、事实数据库、正式向量集合 | 智能体只读 |
| 工作面 | analysis、factcheck、tracking、legal、artifacts、var、workspace | 智能体正常读写 |
| 状态面 | 聊天、会议、任务、Agent memory | 宿主 API 正常读写 |

### 3.2 路径兼容优先

Hermes 在 sandbox 中继续看到：

```text
/home/maoyd/siq-research-engine
```

不得要求修改 AGENTS.md、Prompt 或业务脚本中的路径。OpenShell/BYOC 镜像、挂载或文件映射负责保持路径一致。

### 3.3 不把宿主应用数据库整体设为只读

`SIQ_APP_DATABASE_URL` 当前承载认证、聊天、Agent memory、会议、任务租约、quota 和审计。宿主 FastAPI 必须保留现有写权限。

Sandbox 内 Hermes 仅获得市场事实查询所需的只读连接。不得向 sandbox 暴露宿主 API 的通用写账号。

### 3.4 固化状态由 manifest 决定

是否只读不能只依赖目录名称。只有通过入库 finalization、拥有稳定身份和内容摘要的路径才进入 immutable registry。staging 路径仍由入库 worker 管理。

### 3.5 默认保持质量，拒绝必须可解释

策略拒绝应返回结构化、不可重试或可替代的错误，避免 Hermes 反复调用工具。例如：

```json
{
  "error": "policy_denied",
  "operation": "filesystem.write",
  "path": "/home/maoyd/siq-research-engine/apps/api/main.py",
  "retryable": false,
  "allowed_alternative": "write generated artifacts under artifacts/ or the profile output directory"
}
```

## 4. 当前架构约束

### 4.1 Hermes 启动链

当前启动入口：

- `start_all.sh`
- `scripts/hermes/run_gateway.sh`
- `scripts/hermes/profile_dir.sh`
- `infra/systemd-user/hermes-gateway-siq@.service`
- `infra/systemd-user/hermes-gateway-siq-ic@.service`

`run_gateway.sh` 会先将 `agents/hermes/profiles/<profile>` 同步到 `data/hermes/home/profiles/<profile>`，然后执行：

```bash
hermes gateway run --replace --accept-hooks
```

OpenShell 接入必须保留同步语义，推荐先在宿主完成 profile materialization，再启动 sandbox。

### 4.2 API 与 Hermes 契约

`apps/api/services/hermes_client.py` 按 profile 调用 `/v1/runs`，并支持 create、stream、collect 和 stop。不得改变前端和 API 上层调用契约。

只允许通过环境变量将 profile 的 runs URL 指向 OpenShell 转发端口。

### 4.3 本地模型地址

多个 profile 使用：

```text
http://127.0.0.1:8004/v1
http://127.0.0.1:8006/v1
```

进入 sandbox 后，`127.0.0.1` 指向 sandbox 自身。必须通过可路由宿主地址、OpenShell inference route 或内部 DNS alias 解决，不得静默失去 fallback。

### 4.4 Agent memory

Hermes 原生 memory 在多数 profile 中关闭。SIQ memory 由宿主 API 的以下模块管理：

- `apps/api/services/agent_memory_service.py`
- `apps/api/services/agent_memory_milvus.py`
- `apps/api/services/agent_runtime_memory.py`
- `apps/api/services/agent_chat_runtime_impl.py`

OpenShell 接入不得阻断这些宿主写入。

## 5. 目标架构

```text
Browser / Client
       |
       v
SIQ FastAPI --------------------------------------+
  | chat / workflow / memory / audit              |
  |                                               |
  +--> PostgreSQL app schema (read-write)          |
  +--> agent_memory schema (read-write)            |
  +--> Milvus memory collection (read-write)       |
  |                                               |
  +--> /v1/runs via forwarded port                 |
             |                                     |
             v                                     |
       OpenShell Gateway                           |
             |                                     |
             v                                     |
       Hermes Profile Sandbox                      |
       - project/control files read-only           |
       - finalized facts read-only                 |
       - derived outputs read-write                |
       - runtime state read-write                  |
       - governed network egress                   |
             |                                     |
             +--> cloud LLM/search allowlist       |
             +--> local model aliases              |
             +--> read-only market PostgreSQL      |
             +--> read-only knowledge Milvus       |
             +--> public GET/HEAD                  |
```

每个 Hermes profile 使用独立 sandbox，或至少使用独立 policy 和独立运行时状态。不得让多个 profile 共享可写 Hermes state 目录。

## 6. 路径权限基线

### 6.1 项目控制面只读

以下路径在 sandbox 中只读：

```text
apps/**
services/**
packages/**
scripts/**
db/**
infra/**
runtimes/**
agents/hermes/profiles/**
start_all.sh
docker-compose.yml
pyproject.toml
ruff.toml
mypy.ini
.git/**
```

这包括但不限于：

- `AGENTS.md`
- `config.yaml`
- `profile.yaml`
- `skills/**`
- profile scripts
- shared scripts
- IC workflow Prompt 和角色契约

### 6.2 凭据和宿主配置不可见或只读

以下路径默认不挂载；确需读取时仅允许专用 provider：

```text
/home/maoyd/.ssh
/home/maoyd/.aws
/home/maoyd/.config
/home/maoyd/.kube
/home/maoyd/.docker
/var/run/docker.sock
infra/env/*.env
env/*.env
```

示例文件 `*.example` 可随项目代码只读提供，真实 `.env` 不得进入 sandbox。

### 6.3 公司 Wiki 固化路径只读

对已经进入 immutable registry 的公司路径，以下内容只读：

```text
data/wiki/companies/*/company.json
data/wiki/companies/*/reports/**
data/wiki/companies/*/metrics/**
data/wiki/companies/*/evidence/**
data/wiki/companies/*/graph/**
data/wiki/companies/*/semantic/**
data/wiki/companies/*/obsidian/**
```

多市场 Wiki 中，经 finalization 的以下内容只读：

```text
reports/<report_id>/**
metrics/**
qa/**
evidence/**
document_full.json
table_index.json
source_map.json
artifact_manifest.json
manifest.json
company.json
```

### 6.4 Agent 派生产物可写

以下路径保持现有读写行为：

```text
data/wiki/companies/*/analysis/**
data/wiki/companies/*/factcheck/**
data/wiki/companies/*/tracking/**
data/wiki/companies/*/legal/**
artifacts/**
var/**
```

`data/wiki/companies/*/_index.json` 由宿主 Publisher 更新。迁移期允许保留现有 best-effort 更新失败不影响主任务的行为，但不得因此让公司根目录整体可写。

### 6.5 Deal OS 路径

只读：

```text
data/wiki/deals/*/data_room/raw/**
data/wiki/deals/*/parsed_documents/**
data/wiki/deals/*/sources/**
data/wiki/deals/*/evidence/snapshots/**
```

可写或由现有宿主服务管理：

```text
data/wiki/deals/*/phases/**
data/wiki/deals/*/discussion/**
data/wiki/deals/*/decision/**
data/wiki/deals/*/audit/**
```

本期优先保持 Deal OS 现有宿主 API 写路径。若某个 Hermes tool 直接写上述目录，应在路径审计中标记并单独决定，不得直接扩大整个 Deal 根目录权限。

### 6.6 Hermes 运行时路径

materialized profile 控制文件只读：

```text
data/hermes/home/profiles/*/AGENTS.md
data/hermes/home/profiles/*/config.yaml
data/hermes/home/profiles/*/profile.yaml
data/hermes/home/profiles/*/skills/**
data/hermes/home/profiles/*/scripts/**
data/hermes/home/profiles/shared/**
data/hermes/home/profiles/siq_ic_shared/**
```

运行状态可写：

```text
data/hermes/home/profiles/*/sessions/**
data/hermes/home/profiles/*/logs/**
data/hermes/home/profiles/*/memories/**
data/hermes/home/profiles/*/workspace/**
data/hermes/home/profiles/*/checkpoints/**
data/hermes/home/profiles/*/cache/**
data/hermes/home/profiles/*/state.db*
data/hermes/home/profiles/*/response_store.db*
```

如果 OpenShell filesystem policy 无法安全表达“只读父目录中的可写子目录”，必须使用独立 mount/volume，不得通过给父目录 `read_write` 来绕过。

## 7. 网络门禁 V0.6

### 7.1 放行

1. 任意公网 `GET` 和 `HEAD`，用于网页和文件读取。
2. 当前模型 provider 的必要推理路径。
3. Tavily、Exa 等已配置搜索 API 的必要 POST 路径。
4. SIQ 内部 API、PostgreSQL、Milvus、解析器和本地模型服务。
5. 未知域名、小于等于 128 KiB 的 JSON POST：观察模式记录，不阻断。

### 7.2 阻断

1. 未知公网 `multipart/form-data` 文件上传。
2. 未知公网 `application/octet-stream`。
3. 未知公网 `PUT` 文件上传。
4. `curl -T`、`curl --upload-file`、`scp`、`sftp`、`rsync`、`rclone` 到公网。
5. 未经批准的对象存储预签名上传。
6. 超过 128 KiB 且未命中模型、搜索或批准服务规则的外部请求体。
7. 云 metadata endpoint，例如 `169.254.169.254`。
8. 任意未批准的原始 TCP/UDP、WebSocket 上传通道。

### 7.3 模型和搜索请求例外

模型和搜索请求不能应用通用 128 KiB 阈值。每条规则必须至少绑定：

- source profile
- host
- port
- method
- path
- provider 名称
- credential provider
- 超时和最大请求体

禁止只按 `*.amazonaws.com`、`*.aliyuncs.com` 等公共云大域名开放写权限。

## 8. 进程门禁 V0.6

阻断：

- `sudo`、`su` 和 privilege escalation
- mount、修改 namespace 和加载内核模块
- Docker/Podman socket
- 特权容器
- 裸磁盘设备
- 修改防火墙和路由
- 修改 OpenShell gateway/policy
- 访问宿主敏感目录

保持可用：

- Python、Node、shell
- Git 只读和正常仓库检查
- PDF、文档和财务计算工具
- profile scripts
- 报告渲染和验证脚本
- 当前任务临时进程

设置：

```bash
PYTHONDONTWRITEBYTECODE=1
PYTHONPYCACHEPREFIX=/tmp/siq-pycache
```

## 9. 数据库和 Milvus 权限

### 9.1 宿主 API

保持 `SIQ_APP_DATABASE_URL` 现有读写能力。不得将其替换为只读账号。

### 9.2 Hermes PostgreSQL

创建或复用市场事实只读账号，仅允许：

- CONNECT
- schema USAGE
- SELECT
- 必要只读 view/function

禁止 DDL、DML 和可写 `SECURITY DEFINER` 函数。继续使用 `agents/hermes/profiles/shared/scripts/pg_query.py` 的 SQL 级只读校验。

### 9.3 Agent memory

保持以下宿主写入：

- `ChatMessage`
- `ChatSessionMemory`
- `agent_memory.sessions`
- `agent_memory.messages`
- `agent_memory.memory_items`
- `agent_memory.session_summaries`
- `agent_memory.feedback_events`
- Milvus `siq_agent_memory_active`

Sandbox 不获得这些写凭据。

### 9.4 Milvus 知识集合

Hermes 只允许 Search、Query、Get 和 Describe。禁止 Insert、Upsert、Delete、Drop、Create/Alter Index。入库 worker 和宿主 memory service 使用独立身份。

## 10. Immutable Path Registry

### 10.1 新增组件

新增建议模块：

```text
apps/api/services/immutable_path_registry.py
scripts/openshell/build_immutable_path_registry.py
infra/openshell/schemas/immutable-paths.schema.json
infra/openshell/policies/
```

实际命名可遵循仓库现有模式调整，但不得把生成逻辑散落到多个启动脚本。

### 10.2 Registry 数据来源

优先读取现有：

- `artifact_manifest.json`
- `manifest.json`
- `company.json`
- `parse_run_id`
- `filing_id`
- `report_id`
- quality/finalization 状态
- 文件内容 SHA-256

只有满足 finalization 条件的路径才能标记为 immutable。

### 10.3 Registry 输出

建议输出：

```text
var/openshell/registry/immutable-paths.json
var/openshell/registry/immutable-paths.sha256
```

Schema 至少包含：

```json
{
  "schema_version": "siq.immutable_paths.v1",
  "generated_at": "2026-07-15T00:00:00Z",
  "project_root": "/home/maoyd/siq-research-engine",
  "entries": [
    {
      "path": "data/wiki/companies/600519-贵州茅台/reports/2025-annual",
      "kind": "finalized_report",
      "owner": "ingestion",
      "identity": {
        "company_id": "600519",
        "report_id": "2025-annual",
        "parse_run_id": "..."
      },
      "manifest_sha256": "...",
      "recursive": true
    }
  ]
}
```

Registry 生成必须：

- 路径 canonicalize 后仍在允许的 SIQ data root 内
- 拒绝符号链接逃逸
- 排序稳定
- 输出原子写入
- 生成摘要
- dry-run 可预览差异
- 默认不修改任何文件权限

## 11. OpenShell 集成任务拆分

### T0：建立基线和保护现有改动

目标：在任何实现前建立可比较基线。

任务：

1. 记录当前 git 状态，不清理、不覆盖用户未提交改动。
2. 记录 OpenShell、Docker、Hermes、Python、Node 和 GPU 环境版本。
3. 运行 OpenShell `doctor check`。
4. 诊断当前 gateway 的协议错误；不得直接销毁现有 gateway，除非用户明确批准。
5. 运行现有 Hermes/API 相关测试基线。
6. 选择 `siq_analysis` 作为首个 PoC profile。

交付物：

- `artifacts/openshell/v0.6/baseline.json`
- `artifacts/openshell/v0.6/baseline.md`

验收：基线中明确记录成功、失败和未运行项。

### T1：实现路径分类和 registry 生成器

目标：从 SIQ 实际数据结构生成固化路径清单。

任务：

1. 定义 `siq.immutable_paths.v1` schema。
2. 支持 CN company Wiki。
3. 支持 HK、JP、KR、EU、US 当前 market package。
4. 支持 Deal evidence snapshot，未固化 Deal 工作流目录不得误锁。
5. 增加 symlink/path traversal 防护。
6. 增加 deterministic output 测试。
7. 增加 dry-run 和 diff 输出。

测试至少覆盖：

- finalized report 被收录
- staging report 不被收录
- analysis/factcheck/tracking/legal 不被收录
- 缺 manifest 不误判为 finalized
- 路径逃逸被拒绝
- 重复运行输出一致

### T2：定义 OpenShell policy 模板和编译器

目标：将 registry、静态控制面和可写工作面编译为 profile policy。

任务：

1. 创建公共 policy 模板。
2. 为 `siq_analysis` 生成首个 profile policy。
3. 配置 filesystem、process 和 network policy。
4. 检测 read-only/read-write 路径重叠。
5. 对不安全父级 `read_write` 直接编译失败。
6. 输出 policy 摘要和规则来源。
7. 支持 `--check`、`--dry-run` 和 `--output`。

交付物建议：

```text
infra/openshell/policies/base.yaml
infra/openshell/policies/profiles/siq-analysis.yaml
scripts/openshell/build_policy.py
```

注意：`infra/openshell/policies/` 只保存可审查模板和稳定 profile 规则；包含机器绝对状态、动态 immutable paths 或本机资源 ID 的编译结果必须写入 `var/openshell/policies/`。

### T3：制作 SIQ Hermes BYOC sandbox

目标：提供与当前 Hermes 环境能力一致的 sandbox 镜像。

任务：

1. 安装当前兼容 Hermes 版本。
2. 安装 profile scripts 所需 Python/Node/system 依赖。
3. 保留 UTF-8、中文字体和报告渲染依赖。
4. 提供非 root `sandbox` 用户。
5. 不复制真实凭据。
6. 设置 Python cache 到 `/tmp`。
7. 提供 healthcheck。
8. 锁定镜像 digest 和依赖版本。

建议路径：

```text
infra/openshell/sandbox/Dockerfile
infra/openshell/sandbox/entrypoint.sh
infra/openshell/sandbox/README.md
```

### T4：实现 Hermes gateway OpenShell 启动适配

目标：保持现有 profile 同步和 `/v1/runs` 契约。

建议新增：

```text
scripts/openshell/run_hermes_gateway.sh
scripts/openshell/profile_env.sh
scripts/openshell/status.sh
scripts/openshell/rollback_to_host.sh
```

任务：

1. 复用现有 profile canonicalization。
2. 在宿主完成 materialization。
3. 创建/复用命名 sandbox。
4. 挂载或映射相同绝对项目路径。
5. 为 gateway 配置 `--forward` 端口。
6. 将 `SIQ_HERMES_<PROFILE>_RUNS_URL` 指向转发地址。
7. 正确传递非敏感环境变量。
8. 使用 OpenShell provider/inference route 管理敏感凭据。
9. 支持 start、stop、status、repair。
10. 支持 `SIQ_HERMES_RUNTIME=host|openshell` 快速切换。

不得删除或改变现有 `scripts/hermes/run_gateway.sh` 的默认行为，直到灰度验收通过。

### T5：修复 sandbox 内部服务寻址

目标：保证模型、搜索、PostgreSQL、Milvus 和 SIQ 内部 API 可达。

任务：

1. 为本地模型提供稳定 alias，禁止继续依赖 sandbox `127.0.0.1`。
2. 验证 `8004/8006` fallback 实际生效。
3. 验证 embedding `8013`。
4. 验证 PostgreSQL 和 Milvus 只读访问。
5. 验证 Tavily、Exa 和云模型。
6. 验证 API 到 forwarded Hermes gateway 的 stop/stream 行为。
7. 记录 DNS、TLS、代理和超时配置。

### T6：实现宽松网络上传门禁

目标：阻断明确文件外传，不影响正常查询和模型调用。

任务：

1. 定义模型、搜索和批准服务规则。
2. 放行公网 GET/HEAD。
3. 对未知小 JSON POST 进入 audit-only。
4. 阻断 multipart/octet-stream/PUT 文件上传。
5. 阻断常见文件传输二进制到未知公网。
6. 阻断 metadata endpoint。
7. 记录 policy decision，但不得记录请求正文、Prompt、凭据或用户数据。
8. 增加重定向后重新检查目标的测试。

如果 OpenShell 原生 policy 无法表达请求体大小或 content-type 门禁，应新增 SIQ egress proxy，仅将该能力放在 proxy 中。不得用脆弱 shell 正则冒充网络级强制控制。

### T7：处理共享索引和派生产物写入

目标：保证事实核查等现有功能不因公司根目录只读而退化。

任务：

1. 审计 `update_company_index.py` 的调用点。
2. 将 `_index.json` 更新迁移到宿主 Publisher 或受控 API。
3. 保持 factcheck 主结果写入成功。
4. 保持 analysis/tracking/legal 输出路径不变。
5. Publisher 只允许重建已知索引，禁止接受任意目标路径。
6. 使用原子写、锁和内容校验。

迁移期必须保留向后兼容：索引更新失败不得导致主报告任务失败，但必须产生明确审计事件。

### T8：审计与可观测性

目标：关联 SIQ 业务运行和 OpenShell 执行动作。

每条审计至少包含：

- schema version
- timestamp
- profile
- sandbox ID
- SIQ run ID
- session ID（脱敏或稳定内部 ID）
- operation class
- target host/path 的安全投影
- allow/deny/audit-only
- policy version/digest
- error code
- duration

禁止记录：

- API Key
- Authorization header
- Prompt 正文
- 用户附件正文
- 数据库密码
- 完整请求体

建议输出：

```text
var/openshell/audit/*.jsonl
```

并提供聚合指标：

- policy deny count
- audit-only count
- sandbox start failures
- tool failure rate
- external upload blocks
- immutable path write blocks
- P50/P95 gateway overhead

增加参赛证据导出流程：

1. `scripts/openshell/sanitize_artifacts.py` 将原始运行结果转换为可提交的 `*.sanitized.json` 和 `*.sanitized.md`。
2. 脱敏器必须移除 token、Authorization、cookie、数据库 DSN、用户 home、绝对机器路径、Prompt、用户输入和附件正文。
3. 保留 profile 名称、规则 ID、allow/deny 类型、延迟、成功率、质量指标、版本号和 digest，以便评审复现与核验。
4. `scripts/openshell/check_tracked_state.py` 检查所有已跟踪的 OpenShell 文件，命中疑似 secret 或禁止字段时非零退出。
5. 将检查加入 `scripts/check_all.sh` 或独立 CI job；不得要求 CI 访问真实凭据或运行 gateway。

### T9：测试与质量评测

#### 单元测试

1. registry 路径分类。
2. policy 编译和重叠检测。
3. profile 名称、端口和环境映射。
4. 审计脱敏。
5. Publisher 路径约束。
6. 网络规则分类。

#### 集成测试

至少验证：

1. Hermes 能读取项目代码但不能修改。
2. Hermes 能读取 AGENTS.md/skills 但不能修改。
3. Hermes 能读取固化 Wiki，但写入被拒绝。
4. Hermes 能正常写 analysis/factcheck/tracking/legal。
5. Hermes 能正常写 workspace/checkpoint/session。
6. 宿主 API 能正常保存聊天和 memory。
7. PostgreSQL 市场事实查询正常，DML 被拒绝。
8. Milvus 查询正常，upsert/delete 被拒绝。
9. 模型主路由和 fallback 均可用。
10. Tavily/Exa 正常。
11. 未知公网文件上传被拒绝。
12. 公网 GET/HEAD 正常。
13. stop、timeout、stream 和 reconnect 行为保持。

#### 安全测试

```text
尝试修改 apps/api/main.py                       -> 必须失败
尝试修改 profile AGENTS.md                      -> 必须失败
尝试修改 data/wiki/.../reports/...              -> 必须失败
尝试写 data/wiki/.../analysis/...               -> 必须成功
尝试读取真实 .env                               -> 必须失败
尝试访问 Docker socket                          -> 必须失败
尝试 curl --upload-file 到未知域名              -> 必须失败
尝试正常调用模型和搜索                          -> 必须成功
```

#### A/B 质量评测

同一模型、temperature、Prompt、输入和数据分别运行：

```text
A：当前宿主 Hermes
B：OpenShell Hermes
```

比较：

- 任务成功率
- answer citation rate
- numeric accuracy
- hallucination block rate
- evidence coverage
- 工具成功率
- fallback model success rate
- 报告完整率
- P50/P95 首 token 延迟
- P50/P95 总时长
- timeout rate
- policy false positive rate

### T10：灰度发布与回滚

发布阶段：

1. `siq_analysis` 本地 PoC。
2. `siq_analysis` 观察模式。
3. `siq_analysis` 高风险阻断模式。
4. factchecker、tracking、legal。
5. IC profiles。
6. assistant 最后迁移。

每阶段至少运行完整 smoke 和目标 profile 质量集。

必须保留：

```text
SIQ_HERMES_RUNTIME=host
```

作为即时回滚开关。回滚不得要求数据迁移，不得改变 session ID 和 API 契约。

## 12. 验收标准

### 12.1 功能验收

- 普通聊天、流式输出、停止和超时行为与现状一致。
- analysis、factcheck、tracking、legal 产物仍写入原路径。
- 聊天、会议、任务和 Agent memory 正常写入。
- 模型主路由和所有生产 fallback 均通过验证。
- 公开网页、Tavily 和 Exa 正常。

### 12.2 安全验收

- Agent 无法修改项目代码。
- Agent 无法修改 Prompt、profiles、skills 和 workflow。
- Agent 无法修改 immutable registry 中的固化数据。
- Agent 无法读取真实凭据文件。
- Agent 无法访问 Docker socket 或提权。
- 明确未知公网文件上传被阻断并审计。

### 12.3 质量验收

- numeric accuracy 不下降。
- answer citation rate 不下降。
- IC golden suite 保持通过。
- 任务成功率下降不超过 2 个百分点。
- P95 总时长增加不超过 20%。
- policy false positive rate 小于 0.5%。
- 不允许发生 fallback 静默失效。

### 12.4 运维验收

- 有启动、停止、状态、修复和回滚 runbook。
- policy 和 registry 有 schema version 及 digest。
- 审计不包含敏感正文和凭据。
- 新增入库数据能进入 registry，并在 sandbox 轮换后生效。
- gateway 故障不会破坏宿主 API 数据。

## 13. 推荐验证命令

实现者应根据实际新增文件补充命令，至少运行：

```bash
cd /home/maoyd/siq-research-engine

openshell doctor check

cd apps/api
uv run python -m pytest tests/test_hermes_client.py
uv run python -m pytest tests/test_hermes_pg_query.py
uv run python -m pytest tests/test_agent_memory_service.py
uv run python -m pytest tests/test_agent_memory_milvus.py
uv run python -m pytest tests/test_agent_runtime_memory.py
uv run python -m pytest tests/test_ic_agent_output_quality.py

cd /home/maoyd/siq-research-engine
scripts/check_all.sh
```

新增 OpenShell 测试必须提供可跳过的环境探测，普通单元测试不得要求运行中的 OpenShell gateway 或公网密钥。

## 14. 实施顺序和依赖

```text
T0 基线
  -> T1 registry
  -> T2 policy compiler
  -> T3 BYOC image
  -> T4 gateway adapter
  -> T5 service routing
  -> T6 network gate
  -> T7 Publisher compatibility
  -> T8 audit
  -> T9 A/B verification
  -> T10 staged rollout
```

T1/T2 可以与 T3 并行开发，但 T4 进入真实 PoC 前必须完成 T0、T2 和 T3。T9 未通过不得进入下一 profile 灰度。

## 15. Codex 执行约束

1. 开始前读取仓库根 `AGENTS.md` 和目标目录下更具体的说明。
2. 当前 worktree 可能包含大量用户未提交改动，不得清理、reset、checkout 或覆盖无关文件。
3. 每个阶段先检查现有实现和测试，不复制已有 helper。
4. 使用 `apply_patch` 修改文件。
5. 不提交真实凭据、绝对密钥值、token 或本地 `.env`。
6. 不将 NemoClaw 引入依赖树。
7. 不在未批准情况下销毁当前 OpenShell gateway。
8. 不一次性迁移全部 profiles；首个目标固定为 `siq_analysis`。
9. 每个阶段产出变更说明、验证命令、失败项和回滚方式。
10. 若 OpenShell 当前版本无法表达某条门禁，应明确记录能力缺口并使用最小宿主侧补充组件，不得声称策略已生效。

## 16. 完成定义

本项目只有在以下条件全部满足时才算 V0.6 完成：

1. `siq_analysis` 至少完成一次宿主与 OpenShell A/B 真实运行。
2. 原输出路径和 API 契约未变化。
3. 代码、Prompt、workflow 和固化数据写入测试全部被拒绝。
4. 正常分析报告和 memory 写入测试成功。
5. 模型、搜索、本地服务和 fallback 全部可达。
6. 未知公网文件上传测试被拒绝。
7. 质量门槛全部通过。
8. 回滚到 host runtime 已演练成功。
9. 运维文档和审计说明完整。
10. 扩展到其他 profile 前完成一次人工架构与安全评审。
11. 仓库包含可复现的脱敏 policy、registry、版本 manifest、A/B 质量摘要和安全测试证据。
12. 所有已跟踪 OpenShell 状态通过 secret scan，且 gateway credentials、原始 audit、session DB 和机器绑定状态未进入 Git。
