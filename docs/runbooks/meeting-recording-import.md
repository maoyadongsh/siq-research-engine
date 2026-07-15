# 会议长录音导入运行手册

长录音导入是会议域内独立、默认关闭的新增能力。它不调用 `/api/chat/transcribe`，不创建第二套会议列表；处理成功后生成普通 `MeetingSession(audio_source=import)`，继续使用既有逐字稿、发言人、声纹、纪要、订正和导出页面。

## 数据流

1. 浏览器在 `/meetings/import` 创建 owner-scoped 上传会话。
2. 文件按顺序分片，每块提交 ordinal、offset 和 SHA-256。相同块可幂等重放，乱序或内容冲突返回 `409`。
3. `complete` 只确认持久分片清单并排队，不在 API 请求内转码或转写。
4. Import worker 流式拼接、用 `ffprobe` 校验容器/音轨/时长，再用 `ffmpeg` 输出 16 kHz 单声道 PCM。任一时刻不会把整份录音读入内存。
5. 标准化音频按 10 秒写入既有 `MeetingAudioChunk` 清单，创建普通会议并排队 `FINAL_TRANSCRIPT`。
6. Meeting Speech 默认按 60 秒有界窗口并行执行最终 ASR。导入录音首次生成稳定段后，从全场最多 256 个临时轨道选择 1024 个有效语音样本，使用 ERes2NetV2 embedding 和 FunASR 全局谱聚类生成统一匿名发言人分区。至少 20 个有效样本时自动应用匿名轨道合并；人工命名、声纹确认和声纹自动匹配轨道仍进入审核保护。启用 AI 时再使用该会议选择的 Hermes 模型生成最终纪要。
7. 成功写入会议音频后删除上传分片、拼接文件和标准化临时文件。取消也立即删除临时块。

## 开关与限制

```bash
SIQ_MEETINGS_ENABLED=1
SIQ_MEETING_IMPORT_ENABLED=0
SIQ_MEETING_IMPORT_ROOT=/var/lib/siq-research-engine/data/backend/meeting_imports
SIQ_MEETING_IMPORT_MAX_FILE_BYTES=4294967296
SIQ_MEETING_IMPORT_OWNER_QUOTA_BYTES=8589934592
SIQ_MEETING_IMPORT_MAX_ACTIVE_PER_OWNER=3
SIQ_MEETING_IMPORT_MIN_CHUNK_BYTES=262144
SIQ_MEETING_IMPORT_MAX_CHUNK_BYTES=16777216
SIQ_MEETING_IMPORT_MAX_DURATION_SECONDS=14400
SIQ_MEETING_IMPORT_UPLOAD_TTL_SECONDS=86400
SIQ_MEETING_IMPORT_FFMPEG_BIN=/usr/bin/ffmpeg
SIQ_MEETING_IMPORT_FFPROBE_BIN=/usr/bin/ffprobe
```

`SIQ_MEETING_IMPORT_ENABLED` 默认必须为 `0`。启用时，API 会检查 ffmpeg 和 ffprobe 可执行文件；缺失或路径无效会在 capability 中 fail closed。前端构建/开发进程还需要 `VITE_SIQ_MEETING_IMPORT_ENABLED=1`，`start_all.sh` 会从后端开关自动传递。

首期允许 `wav`、`flac`、`mp3`、`m4a`、`webm`、`ogg`。扩展名只是第一层过滤，worker 会读取真实容器和音轨，内容不匹配时拒绝。

本机当前工具路径示例：

```bash
SIQ_MEETING_IMPORT_FFMPEG_BIN=/home/maoyd/.local/bin/ffmpeg
SIQ_MEETING_IMPORT_FFPROBE_BIN=/home/maoyd/miniconda3/envs/vllm/bin/ffprobe
```

## API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/meetings/v1/imports` | 创建上传会话，要求 `Idempotency-Key` |
| `PUT` | `/api/meetings/v1/imports/{id}/chunks/{ordinal}` | 上传一个二进制分片，要求 offset/hash 头 |
| `GET` | `/api/meetings/v1/imports/{id}` | 查询上传和后处理状态 |
| `POST` | `/api/meetings/v1/imports/{id}/complete` | 校验清单并排队 |
| `POST` | `/api/meetings/v1/imports/{id}/retry` | 重试可恢复的 ingest 或后处理失败 |
| `DELETE` | `/api/meetings/v1/imports/{id}` | 取消并删除临时块；会议已建立后改用会议删除流程 |

所有查询和写入都在数据库条件中包含 `owner_user_id`。服务端不接受客户端路径，磁盘路径只由 owner、upload UUID、ordinal 和哈希派生。

## 启动与检查

统一启动器会在 import 开关开启时同时启动 Meeting Speech 和 Import worker：

```bash
systemctl --user restart siq-research-engine.service
systemctl --user status siq-research-engine.service --no-pager
pgrep -af 'meeting_import_worker|meeting_speech_service'
curl -fsS http://127.0.0.1:8901/health
```

API capability：

```bash
curl -fsS -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:18081/api/meetings/v1/capabilities
```

确认 `recording_import.available=true`，并核对 `formats`、文件上限和时长上限。

会议详情页的发言人区域会显示全场聚类结果和受保护身份的待确认合并建议。FunASR 原生全局谱聚类只自动整理匿名窗口轨道；旧的阈值式跨轨合并策略仍受真实多人录音验证报告和独立 operator gate 约束，不得通过打开开关绕过。

## 恢复与清理

- 浏览器刷新后会保存 upload id；由于浏览器不会永久持有本地 `File`，继续上传时需重新选择同一文件。服务端从 `next_ordinal` 继续。
- worker 使用数据库 lease。进程崩溃后，过期的 `processing` 记录可由新 worker 重新领取；源文件拼接、会议创建、音频块和后处理 job 都有幂等边界。
- 临时错误进入 `retry_wait`；达到自动重试上限后显示公开错误码，用户可手动重试。
- `cancelled` 上传和已完成 ingest 的 staging 目录必须清空。若磁盘故障导致一次清理失败，应在修复存储后按 upload owner/id 重放清理，不能直接删除会议音频根目录。
- 数据库迁移为 `apps/api/migrations/003_create_meeting_import_tables.sql`。回滚功能只关闭开关，不删除 `meeting_import_*` 或既有 `meeting_*` 表。

日志和 API 响应不得包含原始音频、逐字稿正文、绝对 staging 路径、鉴权 token 或模型凭据。
