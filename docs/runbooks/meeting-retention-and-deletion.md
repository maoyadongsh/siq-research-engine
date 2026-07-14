# 会议删除与音频保留运行手册

本文说明会议删除 worker、外部删除台账、数据库/文件恢复对账，以及可选的 90 天音频保留扫描。会议 DELETE API 只将会话置为 `deleted` 并写入 durable job；正文、音频和导出文件由独立 worker 异步清理。

## 安全边界

- 删除任务只处理 `meeting_jobs.job_kind=delete` 且会话已经由 API 授权置为 `deleted` 的记录。
- 外部台账是追加写 JSONL，使用 32 字节 HMAC-SHA256 密钥和前项 HMAC 链认证。
- 台账必须放在 `SIQ_BACKEND_DATA_ROOT` 之外，不能与应用数据库使用同一备份恢复边界。
- 台账目录和文件权限分别为 `0700` 和 `0600`；符号链接、异常权限、链断裂或内容篡改均失败关闭。
- 文件清理路径只由可信的 `owner_user_id` 和 `meeting_id` 重新构造，不读取数据库中的任意文件路径，也不跟随 owner 目录符号链接。
- 普通 retention 扫描默认关闭，只删除超期会议音频，不删除逐字稿、纪要或会议元数据。

## 所需配置

| 变量 | 必需 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `SIQ_MEETING_DELETE_WORKER_ENABLED` | 启动 worker 时必需 | `false` | 必须显式设为 `true` 才运行删除 worker |
| `SIQ_MEETING_DELETION_TOMBSTONE_PATH` | 生产必需 | `$SIQ_RUNTIME_ROOT/security/meeting-deletion-tombstones.jsonl` | 外部追加写台账；必须位于后端数据库备份根之外 |
| `SIQ_MEETING_DELETION_TOMBSTONE_HMAC_KEY` | 必需 | 无 | 32 字节随机值的 base64url 编码；放入 secret manager，不写入仓库 |
| `SIQ_MEETING_AUDIO_ROOT` | 建议显式设置 | `$SIQ_BACKEND_DATA_ROOT/meeting_audio` | 会议音频受控根目录；兼容 `SIQ_MEETINGS_AUDIO_ROOT` |
| `SIQ_MEETING_EXPORT_ROOT` | 建议显式设置 | `$SIQ_BACKEND_DATA_ROOT/meeting_exports` | 会议导出受控根目录 |
| `SIQ_MEETING_DELETE_WORKER_ID` | 否 | 主机名、PID、随机后缀 | durable lease owner；并行实例必须不同 |
| `SIQ_MEETING_DELETE_LEASE_SECONDS` | 否 | `300` | 删除任务租约，范围 30 至 3600 秒 |
| `SIQ_MEETING_DELETE_RETRY_DELAY_SECONDS` | 否 | `30` | 可重试失败的等待时间 |
| `SIQ_MEETING_DELETE_POLL_SECONDS` | 否 | `1` | 空队列轮询间隔 |
| `SIQ_MEETING_RETENTION_SCAN_ENABLED` | 否 | `false` | 是否启用自动音频过期扫描 |
| `SIQ_MEETING_AUDIO_RETENTION_DAYS` | 否 | `90` | 音频保留天数，范围 1 至 3650 |
| `SIQ_MEETING_RETENTION_SCAN_BATCH_SIZE` | 否 | `50` | 单轮扫描上限 |
| `SIQ_MEETING_RETENTION_SCAN_INTERVAL_SECONDS` | 否 | `3600` | 扫描间隔，范围 60 至 86400 秒 |

生成 32 字节 base64url 密钥的一种方式：

```bash
openssl rand -base64 32
```

密钥应由部署 secret manager 注入。台账和密钥需要独立备份，但不能存放在同一个介质或普通数据库备份中。

## 启动命令

持续运行：

```bash
cd /home/maoyd/siq-research-engine/apps/api
SIQ_MEETING_DELETE_WORKER_ENABLED=true \
uv run python scripts/meeting_retention_worker.py
```

只领取一个任务并执行一轮已启用的 retention 扫描：

```bash
cd /home/maoyd/siq-research-engine/apps/api
SIQ_MEETING_DELETE_WORKER_ENABLED=true \
uv run python scripts/meeting_retention_worker.py --once
```

未设置 `SIQ_MEETING_DELETE_WORKER_ENABLED=true` 时，进程输出 `MEETING_DELETE_WORKER_DISABLED` 并以状态码 2 退出，防止部署误启动。

SIGTERM/SIGINT 会停止领取新任务，并允许当前删除完成后退出。进程意外退出时，过期租约可由其他实例接管；即使崩溃发生在最后一次配置尝试中也不会永久卡在 `running`。

## 删除顺序与保留内容

每个任务按以下幂等顺序执行：

1. 原子领取 DELETE job 并建立租约。
2. 取消本会议其他异步任务，撤销流连接 lease 和 ticket。
3. 在外部台账 fsync 一条 HMAC tombstone。
4. 清理音频目录和导出目录。
5. 在单个数据库事务中清理逐字稿、修订、订正、说话人、音频索引、匹配、模型快照、产物、非删除任务、业务事件和关联幂等记录。
6. 将 session 改为不含正文的最小墓碑，只保留 owner、meeting ID 和删除时间；只保留一个已成功 DELETE job 和一个 `session.deleted` 审计事件。

删除会议不会隐式删除用户已经独立确认、用于未来会议的个人词库或已明确授权的长期声纹模板。具体处理如下：

- `scope=current_meeting` 的词库项删除。
- `scope=user_future_meetings` 的词库项保留，并清空被删除会议和候选来源引用。
- 个人词库不可变版本保留。
- 本场 speaker、voiceprint match 和临时数据删除。
- 用户私有 voice profile 与有效 consent 保留；用户需要通过声纹管理页单独撤销或删除声纹。

这种边界避免“删除一场会议”误删用户为未来会议明确建立的个人能力，同时保证本场正文、音频和派生产物不可读取。

## 数据库恢复验收

数据库或文件备份恢复后，在 API 对外开放前必须挂载原外部台账和同一 HMAC 密钥，并执行以下门禁。

第一步，仅验证。若旧备份使已删除内容复活，该命令按预期返回失败：

```bash
cd /home/maoyd/siq-research-engine/apps/api
uv run python scripts/reconcile_meeting_deletion_tombstones.py \
  --require-ledger-file
```

第二步，重放并清理所有台账 tombstone：

```bash
uv run python scripts/reconcile_meeting_deletion_tombstones.py \
  --require-ledger-file \
  --apply
```

第三步，再次仅验证；只有 `status=passed` 且进程状态码为 0 才能接受恢复：

```bash
uv run python scripts/reconcile_meeting_deletion_tombstones.py \
  --require-ledger-file
```

报告中的关键字段：

- `residual_session_count`：仍含敏感数据库行、墓碑不完整或最小审计不合法的会话数。
- `residual_storage_count`：音频/导出目录仍存在的会话数。
- `ownership_mismatch_count`：数据库 owner 与外部台账不一致的会话数；必须人工调查，工具不会跨 owner 清理。
- `absent_session_count`：当前备份中没有 session 的 tombstone；apply 模式仍会清理对应受控文件目录。

`--require-ledger-file` 是恢复环境的强制门禁。没有台账文件时空集合不能被视为“没有删除记录”。

## 故障处置

| 错误码 | 含义 | 处置 |
| --- | --- | --- |
| `DELETE_LEDGER_CONFIGURATION_INVALID` | 密钥长度、路径或备份边界不合法 | 停止 worker，修复 secret/path；不要新建空台账替代原台账 |
| `DELETE_LEDGER_INTEGRITY_FAILED` | HMAC、链、权限或 JSONL 完整性失败 | 隔离实例，从可信独立备份恢复台账，完成审计后再运行 |
| `DELETE_LEDGER_UNAVAILABLE` | 台账挂载不可读写 | 恢复持久卷权限/可用性；job 会按策略重试 |
| `DELETE_STORAGE_PATH_INVALID` | 受控目录结构异常或存在 owner symlink | 不要绕过检查；隔离目录并人工确认没有路径逃逸 |
| `DELETE_STORAGE_UNAVAILABLE` | 音频/导出文件无法清理 | 修复存储后重试；台账已保证恢复时仍会再次清理 |
| `DELETE_JOB_LEASE_LOST` | 本实例不再拥有任务租约 | 检查多实例时钟与租约配置；由当前 lease owner 或过期接管完成 |
| `DELETE_TOMBSTONE_OWNER_MISMATCH` | 台账与恢复数据库 owner 冲突 | 停止恢复上线，人工调查数据来源和数据库完整性 |

禁止通过删除或改写外部台账来“修复”失败。外部台账是防止旧备份复活已删除内容的权威记录。

## 回滚与停用

- 停止自动音频过期：设置 `SIQ_MEETING_RETENTION_SCAN_ENABLED=false`；不会删除或改变已有 transcript。
- 停止领取删除任务：设置 `SIQ_MEETING_DELETE_WORKER_ENABLED=false` 并停止 worker；已经排队的用户删除请求仍保留，恢复 worker 后继续处理。
- tombstone 已写入后，不能通过部署回滚恢复会议正文。对账工具会再次清理任何从旧备份恢复的副本。
- 不要删除会议表、台账或受控存储根；功能回滚仅停止新任务领取和扫描。

## 验证

专项测试：

```bash
cd /home/maoyd/siq-research-engine/apps/api
uv run pytest -q tests/test_meeting_retention.py
```

测试覆盖 HMAC 篡改、外部路径约束、文件 symlink 防护、完整数据库擦除、个人未来词库/声纹保留、崩溃后最终尝试接管、恢复重放、默认关闭 retention 和 CLI 失败关闭。
