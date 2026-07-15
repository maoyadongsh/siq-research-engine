# SIQ iOS 原生会议采集

`apps/ios-meeting-capture` 是 SIQ 会议智能化的隔离式原生采集候选实现，包含 Capacitor 8 类型桥、iOS 15+ Swift Package 和独立宿主应用。它解决 Web 音频链路难以可靠覆盖的锁屏、弱网、后台上传、崩溃恢复和本地回放问题。

该模块尚未进入默认发布路径。默认 Web 方案仍是 `AudioWorklet + WebSocket + IndexedDB`；只有完成本文末尾的真机、隐私、安全和签名门禁后，才能启用 `SIQ_MEETING_IOS_NATIVE_CAPTURE_ENABLED`。文档中的“已实现”指代码合同已存在，不代表 App Store、锁屏长时录音或生产设备矩阵已经验收。

## 产品定位与商业价值

投资访谈、投委会和内部会议的音频具有高保密、高价值、长时运行和弱网容错需求。原生采集层的商业意义是让会议资产不依赖 WebView 存活，并以可校验批次持续上传，最终与转写、说话人、纪要、行动项和证据回放形成闭环。

| 能力 | 工程实现 | 业务价值 |
| --- | --- | --- |
| 原生音频所有权 | Swift 管理 `AVAudioSession`、`AVAudioEngine`、文件和后台任务 | 锁屏或 WebView 暂停时仍有明确的录音责任边界 |
| 防丢批次 | sample offset 时间轴、fsync、SHA-256、原子 sidecar/manifest | 弱网、闪退和恢复过程可判断每段音频是否真正持久化 |
| 有序后台上传 | capture 独立 `URLSession`、outbox、精确 ACK 校验 | 避免重复、乱序或“客户端认为成功但服务端未落盘” |
| 中断显式化 | interruption gap、虚拟 sequence、确定性静音回放 | 电话或音频路由中断不会被伪装成正常会议内容 |
| 安全删除 | seal、server checkpoint、WAV hash/size、cleanup receipt | 只有服务端具备完整可播放副本后才删除本地证据 |
| 凭据分层 | Keychain capture token + 内存 user bearer + trusted origin | 后台上传权限与用户控制面权限分离，降低凭据泄漏面 |

## 技术栈与系统关系

- TypeScript 6：定义 Web/Capacitor 可调用的强类型桥合同。
- Capacitor 8：承载 SIQ Web 工作台并连接原生插件。
- Swift / AVFoundation：负责录音、连续 WAV、本地播放与音频中断。
- Foundation `URLSession` / Keychain / CryptoKit：负责后台传输、设备绑定凭据和摘要校验。
- SIQ Meeting API：提供 batch upload、checkpoint、gap、rollover、seal、playback 与 cleanup 合同。

```text
Web Meeting UI
  -> typed Capacitor bridge
  -> Swift recorder + protected store + ordered outbox
  -> Meeting capture API / checkpoint / seal
  -> ASR + speaker + minutes + export + evidence playback
```

## Frozen boundary

- A user must explicitly call `prepare` and `start` while the app is in the foreground. The plugin never starts on launch, push, process recovery, or remote input.
- `AVAudioSession` and `AVAudioEngine` are owned by Swift. Capture, file writes, manifest updates, and background uploads do not require a live WebView callback.
- PCM S16LE, 16 kHz, mono batches use sample offsets as their timeline identity. Interruptions create explicit gaps; clock changes cannot move the sample timeline backwards.
- Each batch is fsynced, SHA-256 sealed, paired with an atomic sidecar, and then committed to the atomic manifest. A continuous protected WAV is maintained for immediate local playback.
- The store derives `siq.meeting.native_capture.manifest.v1` canonical entries and digest using the backend's sorted compact JSON contract. Stop queues the authenticated capture-token seal only after the ordered outbox is empty. A successful seal is followed by idempotent user-session gap declarations, so an interruption is not misreported as received audio.
- The bridge returns only `capture-asset:<capture-id>`. It never returns an absolute sandbox path or a general `file://` URL.
- Capture tokens and their bound installation IDs are stored together in Keychain with `AfterFirstUnlockThisDeviceOnly`. Tokens are sent only in `Authorization`; every capture-scoped request also sends `X-SIQ-Device-Installation-Id`. Neither value enters manifests, filenames, task descriptions, events, or error payloads.
- Each capture uses its own background `URLSession`; restored task keys include capture ID, epoch, sequence, and SHA-256. Cold launch enumerates protected capture directories, reconstructs matching sessions, validates every restored request against the manifest, and resumes the ordered outbox without starting the microphone. A batch becomes locally ACKed only after a bounded JSON response exactly matches the local capture, coordinates, digest, and byte size. Redirects, empty 2xx responses, malformed ACKs, and server checkpoints that no longer prove a batch durable leave or return it to the pending outbox.
- `getCheckpoints` performs an authenticated server read and combines four explicit authorities: the local capture manifest and the server ingest, realtime, and finalization checkpoints. It does not substitute local upload-task completion for server durability. Foreground rollover first reconciles and drains the old epoch, persists one replayable request boundary/key, uses the WebView user session for the control-plane call, and fences new local batches behind the new epoch until the server reply is validated.
- An audio interruption first persists a pending gap. The local playback WAV receives deterministic silence so playback time stays continuous, while the upload manifest receives virtual, non-uploaded sequence entries. Rollover/seal freezes those entries and the server receives an explicit `system_interruption` gap only after final seal. The UI receives the exact sample and sequence range.
- The playback bridge consumes only `capture-asset:<capture-id>`. `AVAudioPlayer` owns local playback; once an authenticated server Range URL is ready, `AVPlayer` prepares it, seeks to the current local position, and switches only if the latest generation is still current. A failed or stale switch preserves the local player.
- Local deletion is fail-closed behind an authenticated cleanup receipt. The bridge boolean is only user intent; native code first refreshes the capture-token checkpoint, requires sealed ingest, ready server packaging/playback, an empty server missing range, and an exact local/server WAV SHA-256 and byte-size match. It atomically persists the receipt before cancelling uploads, removing the capture token, and deleting the protected directory. A cold-start recovery completes any staged cleanup before it creates an uploader.
- `gap` and `rollover` are foreground, user-session APIs. Capture-token scope remains limited to batch upload, checkpoint read, and seal; it is not expanded to meeting control. Parent-domain session cookies are copied only for the configured trusted API host.
- Foreground control calls support either the trusted WebView cookie session plus a matching CSRF header, or the current user bearer supplied by the shell. The bearer is memory-only: it is not written to Keychain, manifests, filenames, background tasks, events, or crash payloads. A cold recovery can resume capture-token uploads and seal, but waits for foreground reauthentication before any pending user-session gap call.
- The checked-in bridge validates `SIQMeetingAPIOrigin` as an origin-only HTTPS URL and injects it at document start as an immutable `__SIQ_NATIVE_CONFIG__`. The shared Web API client accepts that override only from the `capacitor://localhost` shell; ordinary Web deployments retain their existing same-origin `/api` behavior.

## Source layout

- `src/`: the typed Capacitor bridge consumed by the native shell.
- `ios/Sources/MeetingCapturePlugin/`: recorder, durable store/outbox, Keychain, background uploader, controller, and Capacitor plugin.
- `ios/App/App.xcodeproj`: a checked-in, standalone Capacitor SPM application target linked to the repository's local `SIQMeetingCapture` Swift package.
- `ios/App/App/`: the native host, explicit plugin bridge registration, microphone/background declarations, privacy manifest, and background-session completion forwarding.
- `ios/Tests/`: simulator/Xcode unit tests for persistence and opaque playback handles.
- `scripts/contract.test.mjs`: Linux-safe static contract checks. These checks do not claim that iOS background recording works.

## Local checks

```bash
npm install --ignore-scripts
npm run check
npm --prefix ../web run build
npm run ios:sync
```

`ios:sync` copies the current `apps/web/dist` bundle and refreshes Capacitor's generated runtime files; these generated files remain ignored. On macOS, open `ios/App/App.xcodeproj`, set the `SIQ_MEETING_API_ORIGIN` build setting to the exact trusted HTTPS origin, choose a signing team, and run the app plus Swift tests from Xcode. The app target already links the local Swift package, registers `MeetingCapturePlugin` explicitly, includes the privacy manifest, and declares background audio. The plugin rejects API URLs with another origin, user info, query, fragment, or path. The checked-in `capacitor.config.ts` freezes the application identity and web bundle location.

## Parameters requiring real-device freeze

The current prototype uses `playAndRecord`, `spokenAudio`, a preferred 16 kHz sample rate, a 20 ms preferred I/O buffer, 5-second batches, and `completeUntilFirstUserAuthentication` file protection. These are provisional. MT-081 and MT-086 must freeze them only after supported-device tests confirm audio quality, Bluetooth behavior, locked-device writes, energy use, temperature, and the security tradeoff.

Pure Web, PWA, Simulator, and WKWebView-only results are not evidence for locked-screen capture. Force quit, device reboot, OS process termination, or revoked microphone permission stop capture; the product must not claim otherwise.

## Release evidence still required

Before enabling `SIQ_MEETING_IOS_NATIVE_CAPTURE_ENABLED`, retain per-device evidence for locked-screen runs at 1, 10, 30, and 60 minutes plus a 4-hour soak. Verify sample counts, batch hashes, gaps, duplicate suppression, Wi-Fi/cellular transitions, 30-minute offline recovery, calls and route changes, low-power and low-disk behavior, crash/upgrade recovery, local playback P95 under 2 seconds, energy, thermal state, storage, and upload traffic.

The privacy manifest and microphone/background-audio wording are review inputs, not App Store approval. Legal/privacy review, signing, provisioning, supported-device matrix, Xcode compilation, and physical-device results are mandatory release gates.

This remains an isolated implementation candidate, not an M8 release claim. Linux checks cover the bridge contract and Swift source invariants; they do not type-check Apple frameworks. The checked-in XCTest suite covers idempotent stop, opaque playback handles, canonical digests, open-batch crash recovery, persistent rollover boundaries, bidirectional server checkpoint reconciliation, interruption-gap materialization, and staged cleanup recovery, but it still must run under Xcode. The cleanup receipt is an authenticated durability proof derived from the existing checkpoint contract; a separately signed server deletion endpoint is not currently part of the backend contract. Signing, provisioning, supported-device Xcode compilation, security/privacy review, App Store review, and the physical-device matrix below still block enabling the feature flag.
