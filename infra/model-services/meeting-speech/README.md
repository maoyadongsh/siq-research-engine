# SIQ 会议语音服务

本目录包含会议转写领域的隔离语音进程。它不会 import、启动、停止或修改现有端口 `8899` 上的 FunASR 短语音服务。

该服务是内部模型边界。浏览器必须连接经过认证的 SIQ meeting stream gateway，而不是直接连接本进程。gateway 负责用户授权、一次性 stream ticket、持久音频存储、稳定 segment 事务和公共事件 cursor。本服务负责有界 PCM 接入、sequence ACK、VAD、流式 partial、句末 ASR 和可选匿名说话人 hook。

## 运行合同

- 默认监听：`127.0.0.1:8901`。
- WebSocket：`/v1/stream/{meeting_id}`。
- 健康检查：`/health`、`/health/live`、`/health/ready`。
- 低基数指标：`/metrics`。
- 内部认证：`X-SIQ-Service-Token`；`production`、`prod`、`docker` profiles 下强制要求。
- 浏览器 `Origin` header 默认拒绝，除非显式列入 `SIQ_MEETING_SPEECH_ALLOWED_ORIGINS_CSV`。
- FunASR 加载错误会让服务保持存活但不 ready。没有自动 Mock 回退。
- 句子 finalization 可以使用本地 Paraformer 模型，或显式配置且有界的 HTTP 调用到现有 `8899 /asr`；HTTP 失败绝不会静默切换 backend。
- Mock 模式要求 `SIQ_MEETING_SPEECH_ALLOW_DEGRADED_MOCK=1`，健康状态会报告 degraded/non-production-capable，并在受保护 profiles 中被拒绝。

gateway 应使用：

```text
SIQ_MEETING_ASR_WS_URL=ws://127.0.0.1:8901/v1/stream/{meeting_id}
SIQ_MEETING_ASR_SERVICE_TOKEN=<same value as SIQ_MEETING_SPEECH_INTERNAL_SERVICE_TOKEN>
```

不要把 service token 放进 WebSocket URL。只通过内部请求 header 发送。

## WebSocket v1

第一条消息必须是 JSON：

```json
{
  "type": "stream.start",
  "schema_version": "siq.meeting.stream.v1",
  "meeting_id": "6f71e3f8-a550-47c0-b5b5-2cb8cae539f8",
  "client_stream_id": "4da63e17-30d0-443f-937f-d5da3ac36313",
  "stream_epoch": 1,
  "audio": {
    "encoding": "pcm_s16le",
    "sample_rate": 16000,
    "channels": 1,
    "chunk_ms": 200
  },
  "last_acked_sequence": -1,
  "hotwords": [],
  "hotword_version": 3
}
```

新 epoch 使用 `last_acked_sequence=-1`。重连可以在 `SIQ_MEETING_SPEECH_RESUME_TTL_SECONDS` 内复用同一个 meeting/client/epoch tuple；客户端 ACK 不能超过服务端保留状态。如果状态过期，服务会返回 `RESUME_STATE_NOT_FOUND`。gateway 必须打开新 epoch，并重放其持久存储的 PCM，而不是假装 ASR 上下文仍然存在。

音频为 `16 kHz`、单声道、有符号 `PCM16LE`。推荐 frame 为 200 ms；v1 默认接受 100-1000 ms。二进制 payload 使用固定 32 字节网络字节序 header：

```text
struct !4sBBHIQQI

offset  size  field
0       4     magic = ASCII "SIQA"
4       1     version = 1
5       1     flags (bit 0 END_OF_STREAM, bit 1 DISCONTINUITY)
6       2     header_size = 32
8       4     stream_epoch (uint32)
12      8     sequence (uint64，从 0 开始每帧加一)
20      8     capture_time_ms (uint64，会议单调时间轴)
28      4     payload_size (uint32)
32      N     PCM16LE payload
```

未知版本/flags、奇数字节 PCM、超大 frame、冲突重复 sequence 和无界 gap 都会被拒绝。乱序 frame 只会在已配置 frame/byte window 内保留。`DISCONTINUITY` 会 finalize 当前句子，并重置流式模型上下文，但不会压缩会议时间轴。

文本控制消息都使用 `siq.meeting.stream.v1`：

- `stream.pause`
- `stream.resume`
- `stream.stop`
- `stream.heartbeat`
- `stream.resume_request` 携带 `last_acked_sequence`
- `stream.hotwords.update` 携带 request、version、boundary sequence 和 immutable terms

内部输出使用 `siq.meeting.speech.event.v1`。重要事件类型包括 `stream.ready`、`audio.ack`、`audio.gap.detected`、`flow.control`、`asr.partial`、`asr.final`、`hotwords.update.ack`、`speaker.track.observed`、`pipeline.degraded` 和 `error`。ACK payload 包含 `stream_epoch`、`ack_sequence`、`duplicate`、`buffered_frames` 和 `buffered_bytes`。

`asr.partial` 是临时事件。`asr.final` 有意携带 `durability="gateway_pending"`，且没有 durable cursor。stream gateway 必须原子写入 stable segment 和 outbox event，然后发布带数据库 cursor 的公共 `transcript.segment.stable` envelope。把原始 speech-service final 当作 durable 会违反 meeting taskbook。

### 低延迟与实时热词

浏览器采集 200 ms PCM frame，并在需要追赶时每 160 ms drain 一次。outbox 保持 600 frame 上限，保留此前 120 秒 memory/durability window。Paraformer online 默认使用偏准确性的 `chunk_size=0,10,5`；对于 200 ms 输入，完整 online window 前公布的音频累积上限是 600 ms。模型推理和网络时间仍通过 partial latency metrics 单独观测。

活跃会议可以不断开连接更新不可变词表：

```json
{
  "type": "stream.hotwords.update",
  "schema_version": "siq.meeting.stream.v1",
  "request_id": "11111111-1111-4111-8111-111111111111",
  "hotword_version": 4,
  "effective_sequence": 42,
  "hotwords": ["Nemotron"]
}
```

服务先发出 `hotwords.update.ack`，`status="queued"`。在解码 `effective_sequence` 前，它会 finalize 所有已缓冲旧 segment，切换 decoder vocabulary 和 cache，然后发出 `status="applied"` 与 `applied_sequence`。边界前 frame 保留旧版本，即使它们晚于控制消息到达。每个 partial 和 final 都携带 `hotword_version`；gateway 会在 stable segment 上持久化识别时版本。暂停期间最多可等待 8 个有序更新，request ID 幂等，冲突复用会被拒绝。

## 有界行为

服务绝不会在内存中累积整场会议。

- 每帧 PCM 和时长限制在推理前校验。
- sequence reorder 具备独立 frame-count、byte-count 和 gap 限制。
- sentence PCM 受 `SIQ_MEETING_SPEECH_MAX_SEGMENT_SECONDS` 限制，并在边界强制 finalize。
- 断开的模型 session 有短 TTL 和全局 resident-session 上限。
- 同步 FunASR 调用从 FastAPI event loop 移出，并有异步 timeout。
- 连接本身提供 backpressure；有界队列满时返回显式 `flow.control`/error 事件。
- 说话人是可选 hook。`speaker_adapter=funasr` 使用 ERes2NetV2 embeddings 和 per-session 有界 cosine-centroid cluster，发出匿名 `speaker-N` track。它永远不猜测真实身份，不持久化 embedding，也不跨会议共享 centroid。

### Final-ASR 窗口

`POST /v1/finalize-window` 是内部 token 保护 endpoint，只供 capture 停止后的 durable meeting worker 使用。每个请求包含一个有界 16 kHz 单声道 PCM16 window 和以下 header：

- `X-SIQ-Finalization-Id`：一次 durable processing attempt 的 UUID。
- `X-SIQ-Finalization-Protocol`：有序 legacy 模式或 `siq.meeting.final_asr.independent_window.v1`。
- `X-SIQ-Window-Index`：从 0 开始的稳定 window index。
- `X-SIQ-Window-Start-Ms`：会议时间轴位置。
- `X-SIQ-Discontinuity`：该 window 前是否存在 manifest gap。
- `X-SIQ-Final-Window`：是否为最后一个 window。
- `X-SIQ-Language` 和有界 JSON `X-SIQ-Hotwords`。

独立模式要求 `X-SIQ-Final-Window: true`，因为每个重叠 window 都是完整 decoder domain。Window 可以在 `FINALIZATION_MAX_SESSIONS` 范围内乱序到达和完成。精确重复的 `(run ID, index)` 会共享或重放 checksum 绑定任务；内容变化返回 409。缓存任务有有界数量和 TTL。ordered protocol 仍供旧调用方使用，并在连续 windows 之间保留有界 decoder/anonymous-diarization 状态。响应标识已接受 protocol，并包含 final text、timestamps 和匿名 track keys，绝不包含音频或 speaker embeddings。

相关设置包括 `SIQ_MEETING_SPEECH_FINALIZATION_ENDPOINT_ENABLED`（默认 true）、`SIQ_MEETING_SPEECH_FINALIZATION_MAX_WINDOW_SECONDS`（60）、`SIQ_MEETING_SPEECH_FINALIZATION_MAX_SESSIONS`（2）、`SIQ_MEETING_SPEECH_FINALIZATION_MAX_CACHED_WINDOWS`（2048）和 `SIQ_MEETING_SPEECH_FINALIZATION_SESSION_TTL_SECONDS`（300）。finalizer 为 `funasr_http` 时，还要让 `SIQ_MEETING_SPEECH_HTTP_FINALIZER_MAX_CONCURRENCY` 与下游模型实测容量对齐。

该限制只适用于有界 finalization 请求。实时 Paraformer streaming 保留自身 chunk/window 和 VAD 设置，因此提高 finalization limit 不会给实时 partial 或 stable transcript 事件增加 60 秒延迟。

## 启动

现有 `funasr-vllm` Conda 环境已经包含 FastAPI、NumPy、Uvicorn 和本地 FunASR checkout。启动前启用两个产品开关并提供 token：

```bash
cd /home/maoyd/siq-research-engine/infra/model-services/meeting-speech
export SIQ_MEETINGS_ENABLED=1
export SIQ_MEETING_REALTIME_ASR_ENABLED=1
export SIQ_MEETING_SPEECH_INTERNAL_SERVICE_TOKEN='set-outside-source-control'
./start_meeting_speech.sh
```

任一 feature flag 关闭时，脚本保持 no-op。默认端口 `8901`，使用 CPU Paraformer streaming/final 模型、FSMN VAD 和标点。模型在后台加载；liveness 可以 healthy，同时 readiness 报告 `initializing` 或 `unavailable`。

只在 VAD 句子边界复用现有高精度 `8899` 服务时，配置：

```bash
export SIQ_MEETING_SPEECH_FINALIZER=funasr_http
export SIQ_MEETING_SPEECH_HTTP_FINALIZER_URL=http://127.0.0.1:8899/asr
export SIQ_MEETING_SPEECH_HTTP_FINALIZER_HEALTH_URL=http://127.0.0.1:8899/openapi.json
export SIQ_MEETING_SPEECH_HTTP_FINALIZER_MAX_CONCURRENCY=1
```

每个句子会包装成内存中的 16 kHz 单声道 WAV，并使用现有 multipart 字段（`file`、`language`、`hotwords`、`spk=false`、`timestamp=true`）发送。默认禁用逐请求 speaker hints，因为它们跨请求没有身份，且会重复整会 embedding pass。`SIQ_MEETING_SPEECH_HTTP_FINALIZER_SPEAKER_HINTS_ENABLED=1` 只为诊断恢复。句子 buffer、并发调用、等待时间、响应字节、重定向和请求 timeout 都有界。adapter 不调用 `8899 /ws`，不改变现有服务进程，也不发送整场会议音频。实时 partial 仍来自本服务独立 Paraformer online 模型。

### 匿名说话人与声纹 worker 边界

启用已评测匿名 session 聚类：

```bash
export SIQ_MEETING_SPEECH_SPEAKER_ADAPTER=funasr
export SIQ_MEETING_SPEECH_SPEAKER_MODEL=iic/speech_eres2netv2_sv_zh-cn_16k-common
```

track 按 session 限制数量，并在 retained stream state 过期时消失。track key 包含 stream epoch，因此新的 capture epoch 不会与旧 epoch 匿名标签冲突。低于质量下限、低于 RMS 下限或高于 clipping-ratio 上限的 segment 仍保持匿名。新 track 需要重复候选证据；边界匹配可以分配，但不能更新 track 的有界 robust prototype window。分配、更新、候选、Top-2 margin、确认、过期和信号质量边界均可独立配置。encoder 失败时，ASR final text 仍返回，并带显式 speaker degradation marker。

`/metrics` 只暴露固定 speaker-quality outcomes。使用 `meeting_speech_speaker_assignment_total{result="assigned|unassigned|failed"}` 区分成功分配、故意匿名和 adapter 失败。使用 `meeting_speech_speaker_track_total{result="created|reused"}` 监控 fragmentation pressure。这些指标不包含 meeting、user、track、name、text 或 embedding labels。`unassigned` 包括质量拒绝、模糊匹配和等待确认的候选；应结合已评测 policy 和音频质量证据排查，不要把每个样本都视作模型故障。

`POST /v1/speaker/embedding` 是单独内部 worker 能力，默认关闭。即便在本地模式，启用它也需要配置内部 service token。调用方必须发送：

- `X-SIQ-Service-Token`。
- `X-SIQ-Voiceprint-Consent: <UUID>`，对应业务 worker 已验证的授权。
- `X-SIQ-Voiceprint-Purpose: enrollment` 或 `match`。
- `X-SIQ-Audio-Encoding: pcm_s16le` 或 `wav`。
- 默认 1-15 秒 16 kHz 单声道 PCM16 样本。

endpoint 返回归一化 embedding 和 `persisted=false`；它永远不存储音频、consent 引用或向量。业务 worker 仍负责对象授权、consent 状态、加密、留存、匹配阈值、审计、撤销和删除。该模型服务不是 consent authority。

同一 endpoint 支持已授权内部 finalization worker 的会议级非身份 diarization。发送 `X-SIQ-Speaker-Purpose: diarization`、`X-SIQ-Meeting-ID: <UUID>` 和 `X-SIQ-Diarization-Run-ID: <UUID>`，不要发送任何 `X-SIQ-Voiceprint-*` header。混合 voiceprint 和 diarization scope 会被拒绝。响应使用 `siq.meeting.speaker_embedding.v1`，回显 meeting/run scope，设置 `purpose=diarization` 与 `persisted=false`，并继续受同一 token、时长、并发和内存处理边界约束。

`POST /v1/speaker/cluster` 只接受由上述 endpoint 产生、已认证、meeting-scoped 的 embedding batch。它会对整场会议运行一次 FunASR spectral `ClusterBackend`，使用 `0.80` cosine-center merge，自动说话人数上限为 15，最小样本数为 20。响应包含整数 label 和绑定 diarizer identity，绝不包含 embedding 或音频。这遵循 FunASR/3D-Speaker 整段录音 pipeline，同时允许 final ASR windows 保持并行。匿名全局 clusters 可以自动合并碎片化 window tracks；人工和 voiceprint-backed identities 仍受 review 保护。

voiceprint worker 应配置 `SIQ_MEETING_SPEAKER_EMBEDDING_URL=http://127.0.0.1:8901/v1/speaker/embedding`，并只在服务端环境中复用内部 service token。

无需加载模型的本地协议 smoke test 可显式启用 Mock：

```bash
export SIQ_MEETING_SPEECH_ADAPTER=mock
export SIQ_MEETING_SPEECH_ALLOW_DEGRADED_MOCK=1
./start_meeting_speech.sh
```

Mock 输出是协议测试信号，不是转写；health 永远标识为非生产可用。

## 测试

```bash
cd /home/maoyd/siq-research-engine/infra/model-services/meeting-speech
pytest -q
```

聚焦测试套件不会下载或初始化模型权重。生产启用前，必须在授权音频上用真实 FunASR adapter 运行单独 M0/M2 质量和 soak 门禁。

## 技术创新与商业价值

会议语音链路的难点不只是 ASR 准确率，而是把低延迟、可恢复传输、稳定文本、说话人、热词、权限和删除义务组合成同一合同。

| 机制 | 技术价值 | 商业价值 |
| --- | --- | --- |
| sequence/epoch/ACK 与有界重排 | 网络抖动、重连和乱序可恢复且不压缩会议时间轴 | 长会不会因一次断线丢失整段记录 |
| partial 与 stable segment 分离 | 模型临时输出不能冒充已持久化事实 | 纪要、行动项和审计只消费稳定文本 |
| 实时热词边界版本 | 词表更新在明确 sequence 生效并记录版本 | 公司名、术语和人名可现场纠正且可回放 |
| 匿名 track 与声纹 worker 隔离 | ASR 降级不阻断文本；身份识别需 consent/加密/审计 | 兼顾会议可用性与生物特征合规 |
| 独立 final-ASR windows | 停止后可并行精修且具备幂等 checksum | 实时体验与最终纪要精度不必二选一 |
| Hermes immutable target | stable transcript 选择本地/云模型后冻结执行快照 | 纪要模型来源可追踪，模型配置变化不改写历史 |

会议音频、transcript、声纹和智能体记忆是四个不同的数据域。音频可按留存策略删除，stable transcript 可作为会议证据，声纹受独立 consent/tombstone 约束，纪要结论再按项目 scope 进入长期记忆。分域设计是机构级会议产品可审计、可撤回和可私有化部署的基础。
