# OpenShell Git 发布规则

目标是让参赛仓库尽量完整地呈现 OpenShell 集成。除凭据值和从 SIQ 业务输入派生的私有正文外，
OpenShell 相关源码、配置模板、策略、测试、文档、运行证据和日志默认都属于可发布资产。

本文中的“凭据”不只指字段名包含 `password` 或 `key` 的值，还包括 API key、access token、
cookie、Authorization header、数据库 DSN、TLS/SSH 私钥、短期 broker identity 和其他可用于
认证或冒充运行身份的材料。字段结构、变量名和已脱敏占位符可以提交，真实值不能提交。

## 直接提交

以下可复现资产在提交前通过仓库 secret scan 后直接进入 Git，不采用逐文件保密白名单：

- `infra/openshell/**`：policy、schema、BYOC、patch、无密 provider 模板和参考说明；
- `scripts/openshell/**`：管理脚本、门禁、probe、评测器和测试；
- `docs/runbooks/openshell/**` 与 OpenShell 架构任务书；
- `.gitignore`、CI、`start_all.sh` 等 OpenShell 集成改动。

模板只能使用占位符，不能包含真实凭据。新增的非敏感 OpenShell 源码或文档不需要因为位于
OpenShell 目录而额外排除。

## Manifest 绑定的运行证据

`artifacts/openshell/tracked-artifacts.json` 使用 schema `siq.openshell.tracked-artifacts.v1`。清单条目精确记录：

- `path`；
- `classification`；
- `sha256`；
- `size_bytes`。

允许的运行证据路径只有：

- 固定 baseline/readiness/README；
- `artifacts/openshell/**/*.sanitized.json|md`，其中日志必须是同目录成对的 `logs.sanitized.json|md`；
- `var/openshell/manifests/*.sanitized.json|md`。

`.gitignore` 的通配例外不是授权。`check_tracked_state.py` 会从 Git index 读取 manifest 和 blob，拒绝未登记文件、缺失文件、mode 漂移、摘要或大小不一致、敏感内容以及 Prompt/对话/正文类字段。

新增或重建清单：

```bash
python3 scripts/openshell/build_tracked_artifact_manifest.py \
  --project-root "$PWD" \
  --artifact public_document=artifacts/openshell/README.md \
  --artifact sanitized_evidence=artifacts/openshell/v0.6/example.sanitized.json
```

刷新已有条目：

```bash
python3 scripts/openshell/build_tracked_artifact_manifest.py \
  --project-root "$PWD" --refresh
```

暂存文件和 manifest 后检查实际待提交 blob：

```bash
git add artifacts/openshell/tracked-artifacts.json <清单中的文件>
python3 scripts/openshell/check_tracked_state.py \
  --repo-root "$PWD" --require-allowlist --json
python3 scripts/openshell/check_staged_secrets.py --repo-root "$PWD"
python3 scripts/maintenance/check_large_file_changes.py --repo-root "$PWD"
```

`check_staged_secrets.py` 只物化并扫描 Git index 的 stage-zero blob，不读取脏工作树；扫描前后
还会核对 index 清单未变化。它固定使用 gitleaks `8.24.2`：默认优先匹配版本的本地二进制，
否则使用 `ghcr.io/gitleaks/gitleaks:v8.24.2` 的只读、无网络容器。扫描器缺失、版本不匹配或
执行异常都非零失败。gitleaks 的 stdout/stderr 会被捕获，命中时只输出稳定失败原因，绝不回显
secret 值。检查器还会在仓库外生成只读可信配置并显式启用 gitleaks 默认规则，同时绑定空的
ignore 文件；index 内的 `.gitleaks.toml` 和 `.gitleaksignore` 不能削弱发布门禁。该发布前检查
有意不加入普通离线 `scripts/check_all.sh`，避免强制开发机安装扫描器或依赖 Docker daemon。

## 日志可以提交

日志属于参赛证据，可以提交。为避免日志中的偶发请求头、DSN、token 或业务正文绕过代码审查，
运行中的原件保留在 ignored 的 `var/openshell/`，可提交副本由专用导出器生成：

- structured audit JSONL 先做逐条 strict-schema 验证，再聚合 decision、operation、error code 和延迟；
- gateway/broker/forward operational log 只保留 byte/line/severity counts 与 SHA-256；
- 不复制任何原始日志消息、Prompt、请求/响应正文或附件正文；
- 导出文件再次经过 `check_sanitized_artifacts.py`，随后进入 manifest。

导出后成对的 `logs.sanitized.json|md` 是正式可提交日志，不只是本机临时文件。自由格式
`*.sanitized.log` 在具备逐行严格 schema 前仍失败关闭。即使源日志当前肉眼看起来
没有密码或 key，也必须先走同一脱敏和 manifest 流程，因为后续一行日志可能带入认证头或业务正文。

## 不进入 Git 的原件

以下不生成“可提交原件”，也不能使用 `git add -f` 绕过：

- key、密码、token、cookie、nonce、Authorization、DSN、私钥和其他真实凭据值；
- gateway/session/response 数据库与 XDG auth 状态；
- 原始 audit、gateway/broker/Hermes log、完整 trace 和从业务输入派生的私有正文。

PID、socket、lock、sandbox filesystem、数据库备份、toolchain 二进制、镜像层和 build cache
也继续忽略，但原因是它们是机器瞬态、不可移植或不适合 Git，而不是把 OpenShell 实现隐藏起来。
它们的版本、摘要、状态和测试结论可以脱敏后提交。
