# ADR: iOS 会议原生采集隔离域

- 状态：接受用于继续实现；发布证据未通过
- 日期：2026-07-14
- 对应任务：MT-080 至 MT-087
- 决策负责人：Meeting、Web 与 iOS 维护者

## 背景

纯 Web、PWA 和依赖 WebView JavaScript 的录音链路不能承诺 iOS 锁屏后持续运行。会议产品若要提供这一能力，必须让录音、本地持久化和后台上传由原生进程持有，同时保持现有桌面/Web 采集、聊天短语音和一级市场会议室合同不变。

## 决策

采用独立的 Capacitor iOS shell 和 Swift `MeetingCapturePlugin`，代码位于 `apps/ios-meeting-capture`。原生能力使用独立开关 `SIQ_MEETING_IOS_NATIVE_CAPTURE_ENABLED`，默认关闭。前端只在以下条件全部成立时选择 `ios_native`：

1. 前端显式允许原生能力；
2. Capacitor runtime 确认平台为 iOS；
3. 原生插件已注册；
4. 后端 capability 明确返回 `ios_native.available=true`。

任一条件不满足时使用已有 `web_audio_worklet`。不得依据 User-Agent 选择原生路径，也不得声称 Web/PWA 支持锁屏持续录音。

原生层拥有 `AVAudioSession`、`AVAudioEngine`、sample-offset 时间轴、受保护本地资产、原子 manifest/outbox 和 capture 专属 background `URLSession`。WebView 只消费权威状态快照和不透明本地播放句柄，不能获得沙箱绝对路径、capture token 或通用 `file://` 能力。

服务端新增合同固定在 `/api/meetings/v1/sessions/{meeting_id}/native-captures` 下，覆盖 capture 创建与撤销、批次 PUT、checkpoint、gap、rollover 和 seal。capture token 绑定 owner、meeting、capture 与 device installation，只包含 `batch:write`、`checkpoint:read` 和 `capture:seal`；gap 与 rollover 保持主会话鉴权。

批次身份固定为 `capture_id + stream_epoch + sequence`，时间轴使用 `first_sample + sample_count`。相同坐标和摘要为幂等重放；内容、元数据或幂等键冲突必须拒绝。seal 使用不可变 manifest entries 和 canonical SHA-256，服务端只在声明内容可由持久化批次或显式 gap 重建后推进 finalization。

录音、本地封口、服务端收齐、本地回放、服务端回放和会后 AI 是不同状态。服务端回放不能等待最终 ASR、说话人处理、Hermes 或导出；本地唯一副本在经认证的完整性确认前不得清理。

## 安全与容量边界

- 录音只能由用户在前台明确开始；恢复逻辑不得自动启动已停止或未授权的 capture。
- capture token 只进入 Authorization header 和 Keychain，不进入 URL、manifest、WebView storage、文件名、日志或指标标签。
- 本地资产使用 Data Protection、排除备份、路径 containment 和符号链接拒绝；实际保护等级必须由锁屏真机和安全评审冻结。
- 服务端在数据库锁后重新验证 token 撤销、期限、scope、owner 与设备绑定；写入、删除和保留路径使用一致的 `User -> Meeting -> Finalization -> Capture -> Token` 子序列，避免撤销竞态和锁序环。
- 服务端验证编码、摘要、sample 范围、manifest revision、请求体、单 capture 时长/容量、每 owner 累计保留容量、入口并发与存储余量。默认 owner 累计硬上限为 16 GiB，不能小于单 capture 上限。
- ACK 前必须 fsync 音频文件、完整目录链、最终 hard-link 以及临时链接删除；finalization attempt 是 worker 发布的代际 fence，失去 lease/state 后不能注册 chunk 或发布 WAV。
- 后台上传是尽力而为；未被 iOS 调度不等于音频丢失，只要本地 checkpoint 和 outbox 仍完整。

## 当前实现边界

当前仓库已经提供隔离目录、类型化 Capacitor bridge、Swift recorder/store/uploader 骨架、后端批次合同与 finalization worker、Web adapter 合同以及 Linux 可执行的静态检查。这些内容只批准继续实现，不构成 M8 完成或发布批准。

以下条件未满足前，开关必须保持关闭：

- Swift 尚未完成 server seal/checkpoint/gap/rollover、冷启动 orphan/background-session 全量重建和可验证 cleanup receipt；
- 本地播放句柄尚未接入真实播放器及本地到鉴权 Range 的 UI 切源；
- 尚无可签名 Xcode target、Swift XCTest、支持机型/iOS 矩阵或物理 iPhone 结果；
- 锁屏 1/10/30/60 分钟、4 小时长稳、离线/网络切换/来电/路由/低磁盘/崩溃升级、功耗温升和 P95 2 秒回放证据均未产生；
- App Store、隐私、安全和数据删除材料尚未获得具名批准。

因此当前 native status 必须保持 `ingestComplete=false`、`serverPlaybackState=not_ready`，本地清理保持 fail-closed。

## 后果

- iOS 构建、签名和运行证据与默认 Web 构建完全隔离，功能关闭时不会增加非会议页面或聊天短语音的运行依赖。
- 功能关闭时 API 不创建、反射或严格校验 native 表；启用前必须显式应用 `004`、`005`、`007`、`008` 并通过 schema gate。已有 native 数据仍受删除和保留策略约束。
- 同一会议可能同时存在实时 WebSocket 与批次补传，服务端必须按稳定音频身份去重，而不能按传输通道生成两份证据。
- 原生插件和后端 schema 可以独立迭代，但任何合同变化都需要同步 Swift/Web fixture、OpenAPI 和 migration gate。
- 真机参数和平台合规结论不能由 Simulator、Node 静态合同或 Linux Swift 语法解析替代。

## 回滚

关闭 `SIQ_MEETING_IOS_NATIVE_CAPTURE_ENABLED`，停止 native finalization worker，并拒绝创建或写入新的 native capture。已有批次、manifest、gap、token 撤销记录和最终化状态继续按保留策略保存；超期且无有效 token、无活动任务/lease、无 canonical link 的终态孤儿 capture 可由保留 worker 清理音频正文，但保留 manifest/epoch/gap 审计元数据。回滚不得删除未上传的设备唯一副本，也不得修改现有 Web AudioWorklet、stream ticket 或 Range 回放语义。
