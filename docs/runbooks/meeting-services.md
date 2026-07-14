# 会议转写服务运行手册

会议转写是独立、默认关闭的新增域。关闭会议主开关时，不显示导航、不启动会议进程，既有问答、短语音、一级市场会议和 Hermes profile 均保持原行为。

## 服务关系

| 组件 | 默认地址/进程 | 职责 |
| --- | --- | --- |
| SIQ API | `127.0.0.1:18081` | 鉴权、会议对象、票据、WebSocket 网关、审计 |
| Meeting Speech | `127.0.0.1:8901` | 16 kHz PCM 流式 ASR、VAD、匿名说话人、最终化窗口 |
| FunASR | `127.0.0.1:8899` | 现有短语音接口；可作为 Meeting Speech 的稳定片段 finalizer |
| Hermes meeting target | 从 `18710` 起动态分配 | 只处理稳定文本纠错和纪要，不接收音频或声纹 |
| AI worker | `meeting_ai_worker.py` | 领取纠错、滚动纪要和会后任务 |
| Import worker | `meeting_import_worker.py` | 校验、标准化长录音并排队最终转写与说话人重聚类 |
| Native finalization worker | `meeting_native_capture_worker.py` | 对账 sealed iOS capture、注册既有音频块、异步发布 WAV 后排最终转写 |
| Export worker | `meeting_export_worker.py` | 异步生成 TXT、Markdown、SRT、VTT、JSON、DOCX |
| Retention worker | `meeting_retention_worker.py` | 可选安全删除和显式启用的音频保留扫描 |
| Voiceprint worker | `run_meeting_voiceprint_worker.py` | 可选、需授权的私有声纹注册和匹配 |

浏览器只访问 SIQ API，不直连 `8899`、`8901`、本地模型或云端模型。

## 开关

```bash
SIQ_MEETINGS_ENABLED=1
SIQ_MEETING_REALTIME_ASR_ENABLED=1
SIQ_MEETING_AI_ENABLED=1
SIQ_MEETING_VOICEPRINT_ENABLED=0
SIQ_MEETING_CORRECTION_LEARNING_ENABLED=0
SIQ_MEETING_IMPORT_ENABLED=0
SIQ_MEETING_IOS_NATIVE_CAPTURE_ENABLED=0
SIQ_MEETING_DELETE_WORKER_ENABLED=0
SIQ_MEETING_RETENTION_SCAN_ENABLED=0
```

- `SIQ_MEETINGS_ENABLED` 控制整个会议域和前端导航。
- 其余组件开关彼此独立。关闭 AI 不影响录音和字幕；关闭声纹不影响匿名说话人；订正学习关闭时仍保存人工逐字稿版本，但不产生可用于后续会议的学习候选。
- 声纹自动命名使用单独开关，并且只有加载经过验证的阈值版本后才允许开启。
- 长录音导入使用独立开关和 `/meetings/import` 页面，不复用聊天短语音接口；详见 [meeting-recording-import.md](./meeting-recording-import.md)。
- iOS 原生 capture 只有在专用开关开启时才启动 finalization worker；缺批不会生成 WAV，详见 [meeting-native-capture-finalization.md](./meeting-native-capture-finalization.md)。

## 启动

本机统一启动器：

```bash
systemctl --user restart siq-research-engine.service
systemctl --user status siq-research-engine.service --no-pager
```

`start_all.sh` 会把主开关传给 Vite，并在 API 健康后启动 `scripts/meeting/run_meeting_services.sh`。会议服务组内任一必需子进程退出时，组会整体退出，由上层监管器重启，避免留下只有页面没有处理能力的半活状态。

仅诊断会议服务组：

```bash
SIQ_ENV_FILE=/home/maoyd/siq-research-engine/env/backend.env \
  /home/maoyd/siq-research-engine/scripts/meeting/run_meeting_services.sh
```

生产或长期本机运行也可启用 `infra/systemd-user/siq-meeting-services.service`，但不要同时让它和 `start_all.sh` 启动同一组进程。

## 健康检查

```bash
curl -fsS http://127.0.0.1:18081/health
curl -fsS http://127.0.0.1:18082/health
curl -fsS http://127.0.0.1:8901/health
curl -fsS http://127.0.0.1:8901/metrics
curl -fsS http://127.0.0.1:8899/openapi.json >/dev/null
curl -fsS http://127.0.0.1:18710/health
ss -ltnp | rg ':(15173|18081|8899|8901|18710)\b'
pgrep -af 'meeting_(ai|export|retention|native_capture)_worker|meeting_stream_gateway|meeting_speech_service|run_meeting_services'
```

主 API 与独立 stream gateway 的 `/metrics` 使用 `SIQ_METRICS_TOKEN`
鉴权，提供会议连接、ASR 延迟、音频缺口、任务队列、模型隔离和声纹决策等低基数指标。
监控系统加载 `infra/monitoring/meetings/prometheus-alerts.yml`，Grafana 导入
`infra/monitoring/meetings/grafana-dashboard.json`。两份配置都禁止把 meeting ID、
user ID、姓名、model ref、逐字稿或 embedding 放入 label。

模型目标端口不是业务常量。先读取服务端生成的 `SIQ_MEETINGS_HERMES_TARGETS_FILE`，或由管理员调用 `POST /api/meetings/v1/models/refresh` 后查看脱敏目录。

## 日志

```bash
journalctl --user -u siq-research-engine.service -f
journalctl --user -u siq-meeting-services.service -f
```

日志不得打印原始音频、逐字稿正文、声纹向量、模型 API key、内部 URL 查询参数或下载票据。对外错误只返回稳定的公开错误码；内部诊断保存在受控日志中。

## 分层排障

1. 页面没有“会议转写”：检查 Vite 进程环境中的 `VITE_SIQ_MEETINGS_ENABLED=1`，修改开关后必须重启前端构建/开发服务。
2. 页面出现但 API 返回 `503`：读取 `/api/meetings/v1/capabilities` 的 `configuration_errors`，修复后端开关、ASR URL 或受保护部署 token。
3. 无法开始录音：检查浏览器麦克风权限、一次性 stream ticket、Origin、`8901/health` 和单用户/全局并发限制。
4. 有录音但没有字幕：检查 `8901` 的 online ASR、队列和 gap 指标；不得用 Hermes 输出伪装实时字幕。
5. 有字幕但没有纪要：检查模型目录、目标 gateway 和 AI worker。该故障不得中止 ASR，失败任务可单独重试。
6. 导出长期排队：检查 export worker 和 `meeting_jobs` 的 lease；API 不会内联生成 DOCX。
7. 删除长期排队：确认删除 worker 已显式启用，外部 HMAC 删除台账路径可写且位于数据库备份目录之外。
8. 声纹不可用：先检查用户授权、样本质量、加密 keyring 和阈值版本；不得绕过授权或把低置信度结果显示为确定实名。
9. 原生 capture 长期 `pending_upload`：按 checkpoint 缺失范围补传；仅在本地资产确定不可恢复时声明 durable gap。`retry_wait/failed` 的存储故障按 native finalization runbook 修复后重试。

## 降级与回滚

按影响从小到大执行：

1. 设置 `SIQ_MEETING_AI_ENABLED=0`，保留录音与实时字幕。
2. 设置 `SIQ_MEETING_VOICEPRINT_ENABLED=0`，保留匿名说话人。
3. 设置 `SIQ_MEETING_CORRECTION_LEARNING_ENABLED=0`，保留人工 revision，停止产生未来识别候选。
4. 停止接收新会议，等待活跃会议结束和 durable jobs 排空。
5. 设置 `SIQ_MEETINGS_ENABLED=0` 并重启。导航消失，会议 API fail closed，已有数据库与文件不删除。

回滚代码不得删除 `meeting_*` 表或会议存储。恢复服务时先启动数据库/API，再启动 Speech、export worker、可选 Hermes/voiceprint/retention worker，最后重新开放导航。

## 删除与恢复

安全删除、外部墓碑台账和恢复对账使用 [meeting-retention-and-deletion.md](./meeting-retention-and-deletion.md)。备份恢复验收必须先挂载外部删除台账，再运行：

```bash
cd apps/api
uv run python scripts/reconcile_meeting_deletion_tombstones.py \
  --require-ledger-file --apply
```

声纹删除恢复检查使用 [meeting-voiceprint-worker.md](./meeting-voiceprint-worker.md)，模型任务恢复使用 [meeting-ai-worker.md](./meeting-ai-worker.md)，导出恢复使用 [meeting-exports.md](./meeting-exports.md)。
原生录音封口、WAV 发布和崩溃接管使用 [meeting-native-capture-finalization.md](./meeting-native-capture-finalization.md)。
