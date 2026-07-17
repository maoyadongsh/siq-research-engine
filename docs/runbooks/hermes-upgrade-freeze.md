# Hermes 升级冻结与回退基线

> 决策日期：2026-07-15
> 状态：冻结，不执行升级
> 适用项目：`${SIQ_PROJECT_ROOT}`

## 决策

为保证 SIQ 当前参赛版本稳定，暂不把 Hermes 升级到新版本，也不在现有运行环境中做依赖、启动器或配置替换。后续 OpenShell 的开发和验证必须以当前 Hermes 为基线，不能把升级和 sandbox 接入合并验证。

本次冻结没有改变 SIQ 代码、运行中的 Hermes 进程、profile 配置或 OpenShell gateway。

## 已冻结的运行基线

| 项目 | 当前值 |
| --- | --- |
| SIQ Git HEAD | `0faf0c2cdccf034a5101fbf6691b0a75daf01cc4` |
| Hermes 显示版本 | `0.13.0`（`2026.5.7`） |
| Hermes source commit | `ddb8d8fa842283ef651a6e4514f8f561f736c72e` |
| Hermes Python | `3.11.15`（现有 venv） |
| OpenShell CLI | `0.0.13`（仅记录，不升级） |
| 现有 gateway | `nemoclaw`，状态异常；不得复用或销毁 |
| 备份目录 | `var/openshell/backups/hermes-pre-upgrade-20260715T032730Z/` |

Hermes 工作树在基线时已经存在本地修改和一个未跟踪文件。它们属于当前 SIQ 运行行为的一部分，不能用上游干净版本覆盖。备份目录中的 bundle、补丁、未跟踪文件归档、启动器和依赖清单均为敏感运行材料，默认不提交 Git，也不得上传到外部服务。

## 回退材料

备份目录包含：

- `hermes-repository.bundle`：完整 Git 历史和 refs；
- `hermes-working-tree.patch`：基线时的工作树修改；
- `hermes-untracked-files.tar.gz`：基线时的未跟踪文件；
- `hermes-launcher`：当前启动器副本；
- `python-packages.json`：当前 venv 的依赖清单；
- `siq-profile-control-files.sha256`：SIQ profile 控制文件哈希。

已在临时目录完成回退演练：bundle 可克隆到指定 source commit，工作树补丁通过 `git apply --check`，未跟踪文件归档可读取。演练未修改项目目录和运行环境。

再次演练时使用以下只读验证流程（将 `BACKUP` 指向实际备份目录）：

```bash
set -e
BACKUP="var/openshell/backups/hermes-pre-upgrade-20260715T032730Z"
TMP="$(mktemp -d /tmp/siq-hermes-rollback.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT
git bundle verify "$BACKUP/hermes-repository.bundle"
git clone "$BACKUP/hermes-repository.bundle" "$TMP/repo"
git -C "$TMP/repo" checkout ddb8d8fa842283ef651a6e4514f8f561f736c72e
git -C "$TMP/repo" apply --check "$PWD/$BACKUP/hermes-working-tree.patch"
tar -tzf "$BACKUP/hermes-untracked-files.tar.gz" >/dev/null
```

## 已通过的基线检查

以下检查在冻结前完成，后续任何 OpenShell 或 Hermes 变更都必须与它们比较：

- SIQ Hermes/API 合同测试：`63 passed`；
- Hermes 本地补丁合同测试：`145 passed`（34 warnings，均为 aiohttp 警告）；
- IC R1 profile dry-run：`6/6 allowed`，无真实模型调用；
- 12 个 Hermes gateway `/health`：全部 HTTP 200；
- SIQ API `/health`：HTTP 200；
- 基线文档生成前 Git 工作树：clean，`git diff --check` 无输出；其后的 OpenShell T0-T2 开发改动不属于该 clean 快照。

测试命令和完整环境细节不得写入包含 token、环境变量或用户内容的日志。基线报告只能保存脱敏的计数、版本、哈希和退出状态。

## 冻结期间禁止的操作

以下操作必须等到用户明确批准“解冻 Hermes 升级”后才能执行：

1. 在 `${HERMES_HOME}` 内执行 `git pull`、切换分支、安装依赖或重建 venv；
2. 执行 `hermes update`，替换用户级 Hermes launcher，或修改现有 Hermes launcher；
3. 将上游 Hermes 代码、profile 或 prompt 直接复制覆盖当前运行目录；
4. 修改当前 profile 的模型、fallback、toolset、temperature 或 gateway 端口来模拟升级；
5. 用新 OpenShell CLI 操作现有 `nemoclaw` gateway；
6. 执行 OpenShell 官方破坏性升级流程、`gateway destroy`、`sandbox delete --all` 或设置 `OPENSHELL_ACK_BREAKING_UPGRADE=1`。

OpenShell 可以继续做独立的只读审计、策略设计和脱敏文档工作；任何实际 sandbox 接入都必须显式固定当前 Hermes source commit、dirty patch digest、runtime profile 快照和模型端点可用性。

## 解冻门槛

只有同时满足以下条件，才可重新评估 Hermes 升级：

1. 用户明确批准升级，并指定可接受的停机和回滚窗口；
2. 重新生成一份新的 bundle、工作树补丁、依赖清单和 profile 哈希；
3. 在项目外的临时目录建立独立 Python 3.13（或目标版本）venv，不接触现有 venv；
4. 对 `/v1/runs`、SSE terminal event、multimodal input、runtime metadata、fallback 和模型控制接口完成兼容性 diff；
5. 在隔离 gateway 上完成 A/B、R1 dry-run、API 合同、健康检查和最小真实任务评测；
6. 评测结果没有回归，且能按本文件的回退演练恢复到当前基线。

在这些条件满足之前，当前版本就是 SIQ 的唯一受支持 Hermes 版本。
