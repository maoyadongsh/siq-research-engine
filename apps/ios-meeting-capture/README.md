# SIQ iOS 原生会议采集

`apps/ios-meeting-capture` 是 SIQ 会议智能化的隔离式原生采集候选实现，包含 Capacitor 8 类型桥、iOS 15+ Swift Package 和独立宿主应用。它解决 Web 音频链路难以可靠覆盖的锁屏、弱网、后台上传、崩溃恢复和本地回放问题。

该模块尚未进入默认发布路径。默认 Web 方案仍是 `AudioWorklet + WebSocket + IndexedDB`；只有完成本文末尾的真机、隐私、安全和签名门禁后，才能启用 `SIQ_MEETING_IOS_NATIVE_CAPTURE_ENABLED`。文档中的“已实现”指代码合同已存在，不代表 App Store、锁屏长时录音或生产设备矩阵已经验收。

它属于应用中心的会议转写能力，同时服务一级市场投委会、专家访谈和内部复盘场景。采集层只负责安全、可靠、可校验地把音频批次交给会议 API；ASR、说话人、纪要、行动项、证据入库和智能体消费仍由后端控制面治理。

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

## 在多模态智能体体系中的位置

iOS 客户端只负责可靠采集和上传，不在端侧做不可审计的最终转写或纪要：

```text
iPhone microphone / approved audio route
  -> native epoch + sequence + local durable chunks
  -> authenticated native capture API
  -> server-side manifest/finalization
  -> meeting-speech / FunASR
  -> stable transcript + speaker timeline
  -> Hermes correction/minutes/action items
  -> project evidence / scoped memory
```

这种边界让设备断网、锁屏或进程被系统挂起时仍能恢复上传，也让 ASR 模型、Nemotron/其他纪要模型、声纹授权和数据留存都由服务端统一治理。端侧音频不是长期记忆；只有经过稳定化、权限校验和项目归属的 transcript/纪要才可被智能体消费。

## 技术栈与系统关系

- TypeScript 6：定义 Web/Capacitor 可调用的强类型桥合同。
- Capacitor 8：承载 SIQ Web 工作台并连接原生插件。
- Swift / AVFoundation：负责录音、连续 WAV、本地播放与音频中断。
- Foundation `URLSession` / Keychain / CryptoKit：负责后台传输、设备绑定凭据和摘要校验。
- SIQ Meeting API：提供 batch upload、checkpoint、gap、rollover、seal、playback 与 cleanup 合同。

```text
Web 会议界面
  -> 强类型 Capacitor 桥
  -> Swift 录音器 + 受保护存储 + 有序 outbox
  -> 会议采集 API / checkpoint / seal
  -> ASR + 说话人 + 纪要 + 导出 + 证据回放
```

## 冻结边界

- 用户必须在应用处于前台时显式调用 `prepare` 和 `start`。插件不得在启动、推送、进程恢复或远程输入时自行开始录音。
- `AVAudioSession` 和 `AVAudioEngine` 由 Swift 持有。采集、文件写入、manifest 更新和后台上传不依赖仍然存活的 WebView 回调。
- PCM S16LE、16 kHz、单声道 batch 使用 sample offset 作为时间轴身份。音频中断会生成显式 gap，系统时钟变化不能让 sample 时间轴倒退。
- 每个 batch 都会 fsync、SHA-256 seal、配对原子 sidecar，再提交到原子 manifest。客户端同时维护受保护的连续 WAV，用于即时本地回放。
- 存储层使用后端排序紧凑 JSON 合同生成 `siq.meeting.native_capture.manifest.v1` 规范条目和摘要。只有有序 outbox 清空后，stop 才会排队执行带 capture token 的 seal。seal 成功后，再以幂等用户会话请求声明 gap，避免把中断误报成已接收音频。
- 桥接层只返回 `capture-asset:<capture-id>`，绝不返回绝对沙箱路径或通用 `file://` URL。
- capture token 与绑定的 installation ID 一起保存在 Keychain，并使用 `AfterFirstUnlockThisDeviceOnly`。token 只放在 `Authorization` 中发送；每个 capture 作用域请求也发送 `X-SIQ-Device-Installation-Id`。这两个值不得进入 manifest、文件名、任务描述、事件或错误 payload。
- 每个 capture 使用独立后台 `URLSession`；恢复任务 key 包含 capture ID、epoch、sequence 和 SHA-256。冷启动会枚举受保护 capture 目录，重建匹配 session，按 manifest 校验每个恢复请求，并在不启动麦克风的情况下恢复有序 outbox。只有有界 JSON 响应精确匹配本地 capture、坐标、摘要和字节数后，batch 才能成为本地 ACK。重定向、空 2xx、畸形 ACK 或不再证明 batch 已持久化的服务端 checkpoint 都会让 batch 保留或回到 pending outbox。
- `getCheckpoints` 执行认证服务端读取，并合并四类权威状态：本地 capture manifest、服务端 ingest checkpoint、实时 checkpoint 和 finalization checkpoint。它不会用本地上传任务完成状态替代服务端持久化证明。前台 rollover 会先 reconcile 并 drain 旧 epoch，持久化一个可重放请求边界/key，使用 WebView 用户会话调用控制面，并在服务端响应通过校验前隔离新本地 batch。
- 音频中断会先持久化 pending gap。本地回放 WAV 写入确定性静音以保持回放时间连续，上传 manifest 写入虚拟且不上传的 sequence 条目。rollover/seal 会冻结这些条目，服务端只在最终 seal 后收到显式 `system_interruption` gap。UI 会收到精确 sample 和 sequence 范围。
- 回放桥只消费 `capture-asset:<capture-id>`。`AVAudioPlayer` 持有本地回放；认证服务端 Range URL 准备好后，`AVPlayer` 会准备并跳转到当前本地位置，且只在最新 generation 仍有效时切换。切换失败或过期会保留本地播放器。
- 本地删除在认证 cleanup receipt 后失败关闭。桥接布尔值只是用户意图；原生代码会先刷新 capture-token checkpoint，要求 sealed ingest、服务端 packaging/playback 已就绪、服务端 missing range 为空，并且本地/服务端 WAV 的 SHA-256 和字节数完全一致。它会先原子持久化 receipt，再取消上传、移除 capture token、删除受保护目录。冷启动恢复会在创建 uploader 前完成任何 staged cleanup。
- `gap` 和 `rollover` 是前台用户会话 API。capture-token scope 只限于 batch upload、checkpoint read 和 seal，不扩展到会议控制。父域 session cookie 只复制给已配置的可信 API host。
- 前台控制调用支持可信 WebView cookie 会话加匹配 CSRF header，或 shell 提供的当前用户 bearer。bearer 只存在内存中，不写入 Keychain、manifest、文件名、后台任务、事件或崩溃 payload。冷恢复可以恢复 capture-token 上传和 seal，但任何 pending 用户会话 gap 调用都必须等待前台重新认证。
- 已提交的桥接代码会验证 `SIQMeetingAPIOrigin` 是仅含 origin 的 HTTPS URL，并在 document start 注入不可变 `__SIQ_NATIVE_CONFIG__`。共享 Web API client 只接受来自 `capacitor://localhost` shell 的覆盖；普通 Web 部署仍使用现有同源 `/api` 行为。

## 源码布局

- `src/`：原生 shell 消费的强类型 Capacitor 桥。
- `ios/Sources/MeetingCapturePlugin/`：录音器、持久 store/outbox、Keychain、后台 uploader、controller 和 Capacitor 插件。
- `ios/App/App.xcodeproj`：已提交的独立 Capacitor SPM 应用目标，链接仓库本地 `SIQMeetingCapture` Swift package。
- `ios/App/App/`：原生宿主、显式插件桥注册、麦克风/后台声明、隐私 manifest 和后台 session completion 转发。
- `ios/Tests/`：持久化和 opaque playback handle 的模拟器/Xcode 单元测试。
- `scripts/contract.test.mjs`：可在 Linux 安全运行的静态合同检查。这些检查不声明 iOS 后台录音已经可用。

## 本地检查

```bash
npm install --ignore-scripts
npm run check
npm --prefix ../web run build
npm run ios:sync
```

`ios:sync` 会复制当前 `apps/web/dist` bundle，并刷新 Capacitor 生成的运行文件；这些生成文件仍然被 Git 忽略。在 macOS 上，打开 `ios/App/App.xcodeproj`，把 `SIQ_MEETING_API_ORIGIN` build setting 设置为精确可信 HTTPS origin，选择签名团队，然后在 Xcode 中运行 App 和 Swift 测试。App target 已链接本地 Swift package，显式注册 `MeetingCapturePlugin`，包含隐私 manifest，并声明后台音频。插件会拒绝带有其他 origin、user info、query、fragment 或 path 的 API URL。已提交的 `capacitor.config.ts` 冻结了应用身份和 Web bundle 位置。

## 需要真机冻结的参数

当前原型使用 `playAndRecord`、`spokenAudio`、首选 16 kHz 采样率、首选 20 ms I/O buffer、5 秒 batch，以及 `completeUntilFirstUserAuthentication` 文件保护。这些都是临时参数。MT-081 和 MT-086 必须在支持设备测试确认音频质量、蓝牙行为、锁屏写入、能耗、温度和安全取舍后才能冻结这些参数。

纯 Web、PWA、Simulator 和仅 WKWebView 结果都不能作为锁屏采集证据。强制退出、设备重启、OS 进程终止或麦克风权限被撤销都会停止采集；产品不得宣称相反能力。

## 仍需补齐的发布证据

启用 `SIQ_MEETING_IOS_NATIVE_CAPTURE_ENABLED` 前，必须为每类设备保留锁屏 1、10、30、60 分钟以及 4 小时 soak 的证据。需要验证 sample count、batch hash、gap、重复抑制、Wi-Fi/蜂窝切换、30 分钟离线恢复、来电和音频路由变化、低电量和低磁盘行为、崩溃/升级恢复、本地回放 P95 小于 2 秒、能耗、热状态、存储和上传流量。

隐私 manifest 和麦克风/后台音频文案只是审核输入，不等于 App Store 批准。法务/隐私审查、签名、provisioning、支持设备矩阵、Xcode 编译和真机结果都是强制发布门禁。

该模块仍是隔离实现候选，不是 M8 发布声明。Linux 检查覆盖桥接合同和 Swift 源码不变量，但不会 type-check Apple frameworks。已提交 XCTest 覆盖幂等 stop、opaque playback handle、规范摘要、open-batch 崩溃恢复、持久 rollover 边界、双向服务端 checkpoint reconcile、中断 gap 物化和 staged cleanup 恢复，但仍必须在 Xcode 下运行。cleanup receipt 是从现有 checkpoint 合同派生的认证耐久性证明；单独签名的服务端删除接口目前不属于后端合同。签名、provisioning、支持设备 Xcode 编译、安全/隐私审查、App Store 审查和真机矩阵仍然阻止启用该 feature flag。
