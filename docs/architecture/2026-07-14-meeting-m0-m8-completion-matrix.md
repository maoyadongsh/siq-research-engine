# Meeting M0-M8 逐项完成度矩阵

本文对照 `2026-07-13-meeting-realtime-transcription-taskbook.md`，记录截至 2026-07-15 当前共享工作区中 MT-000 至 MT-087 的实现、自动化和发布证据状态。它是审计快照，不是发布批准书，也不替代 `2026-07-14-meeting-m0-m7-completion-audit.md` 中要求的证据索引、校验和与签字。

## 1. 口径与结论

### 1.1 状态口径

- **代码/自动化状态**：`完成` 表示任务书要求的主要代码路径和针对性自动化均存在；`部分` 表示已有实质实现，但仍缺合同、集成、平台验证或关键自动化；`缺失` 表示任务的核心交付尚不存在。
- **发布证据状态**：`pass` 只表示该 MT 的全部验收证据已绑定候选提交并可复核；`partial` 表示仅有代码、单测、静态检查或部分产物；`fail` 表示已经执行且未通过；`not_run` 表示要求的真实运行尚未执行；`blocked` 表示被前置 gate 或外部条件阻断。
- 单元测试、mock E2E、Swift/Node 静态合同和 Simulator 都不是授权会议音频、独立声纹验证集、4 小时 soak、Xcode 编译、iPhone 真机或安全/隐私签字的替代品。
- 工程合同以 `679d456fe3c8e8ce8e3e1d003453cd8f221e7357` 为审阅来源；其后的门禁/审计提交不得改变受监控合同 digest。本地测试仍不能替代候选 CI artifact 或真实发布证据。

### 1.2 综合结果

| 口径 | 结果 | 说明 |
| --- | ---: | --- |
| 实现/自动化成熟度 | **41 / 49 = 83.7%** | 49 个 MT 等权；`完成=1`、`部分=0.5`、`缺失=0`。这是工程成熟度，不是发布完成度。 |
| 严格发布证据完成度 | **0 / 49 = 0%** | 当前没有任何一个 MT 的全部发布证据绑定干净候选提交并闭环。若只看单测会高估完成度。 |
| 总体发布状态 | **blocked** | MT-000 的基线与审查来源 exact delta 已闭环，但当前 HEAD 的后续 IC 提交使一个已批准哈希漂移；授权 ASR/词库与声纹评测、M7 负载/4h 长稳/演练/签字、M8 Xcode/真机证据仍缺失。 |

阶段成熟度来自下文逐项状态，不代表阶段门禁通过：

| 阶段 | 完成 | 部分 | 缺失 | 加权成熟度 | 阶段门禁 |
| --- | ---: | ---: | ---: | ---: | --- |
| M0 | 2 | 2 | 0 | 3.0/4 = 75.0% | **未通过**：MT-000 审查来源合同通过但当前候选哈希漂移且 CI artifact 未生成，MT-001/003 真实评测缺失 |
| M1 | 3 | 2 | 0 | 4.0/5 = 80.0% | **未通过**：生产 PostgreSQL/旧版本兼容与全资源 BOLA 证据未闭环 |
| M2 | 4 | 2 | 0 | 5.0/6 = 83.3% | **未通过**：真实 ASR 指标、真实会议与 4h 存储证据缺失 |
| M3 | 8 | 0 | 0 | 8.0/8 = 100.0% | **未通过**：仅表示主要代码与自动化完成；真实回放/最终 ASR 质量及词库 A-B 证据缺失 |
| M4 | 5 | 0 | 0 | 5.0/5 = 100.0% | **未通过**：仅表示代码成熟；独立声纹阈值、隐私评审与恢复演练均未完成 |
| M5 | 6 | 0 | 0 | 6.0/6 = 100.0% | **未通过**：真实并发模型隔离、故障注入和延迟证据缺失 |
| M6 | 3 | 0 | 0 | 3.0/3 = 100.0% | **未通过**：真实长录音发布指标、导出审计和绑定候选的 E2E 证据缺失 |
| M7 | 1 | 3 | 0 | 2.5/4 = 62.5% | **未通过**：生产硬化证据主体未执行 |
| M8 | 2 | 5 | 1 | 4.5/8 = 56.3% | **未通过且 flag 不得开启**：Xcode target、原生插件、Web 工作台与清理回执已有，Apple SDK 编译和真机证据仍缺失 |

## 2. 已确认的横向证据

| 项目 | 当前结果 | 证据与限制 |
| --- | --- | --- |
| 不可变旧合同基线 | `pass` | `scripts/meeting/baselines/pre-meeting-6727ce3.contract.json`，SHA-256 `bf72d31d4fe4a2b4be384d0ba985ef72c3817e93aaed7520e05f80a38a277781` |
| 基线自校验 | `pass` | `pre-meeting-6727ce3.self-verify.json`，SHA-256 `393e81b20c1ac7ced5f30ad99493190434be78676bf210773865f65eef6bc662` |
| 候选合同 exact delta | **`blocked`（当前 HEAD）** | `scripts/meeting/baselines/nonmeeting-closeout-679d456.approved-delta.json` 在审查来源 `679d456` 上通过；当前 HEAD `c876e87` 的既有 IC 提交把 `golden_case_manifest.json` 的批准摘要 `2ed83fe9...` 改为 `213ed284...`。当前验证为 `mismatched=1, missing=0, unexpected=0`，必须由 IC 治理独立审批，会议任务不覆盖或重签该文件。 |
| 会议后端针对性回归 | 开发验证通过 | 最近一次运行 `242 passed`（`test_meeting_*.py` + `test_meetings_router.py`）；无绑定候选提交的 release artifact，不能提升发布证据为 `pass`。 |
| 独立 speech service | 开发验证通过 | 最近一次运行 `44 passed`；主要为协议、mock/adapter 和服务测试，不是授权音频容量报告。 |
| 发布工具测试 | 开发验证通过 | 最近一次运行 `83 passed`；评估器能 fail closed，但空模板/合成输入不是发布数据。 |
| Web 单测/检查/构建 | 开发验证通过 | 当前共享候选 `434/434`、lint、TypeScript 和 production build 通过；未绑定远端候选 artifact。 |
| Web E2E flag 门禁 | 开发验证通过 | meeting disabled `1/1`、enabled `13/13`；默认套件首轮 `51 passed, 1 skipped, 1 screenshot capture failed`，失败用例单独重跑 `1/1` 通过。浏览器 E2E 不等于真实麦克风或 iPhone 后台。 |
| iOS 静态合同 | 开发验证通过 | `apps/ios-meeting-capture` 的 TypeScript/Node 静态合同 `13/13`，`cap sync ios` 成功并生成/更新本地 Xcode target；Linux 无 Xcode/iOS SDK，Swift 编译、XCTest、签名和真机均未运行。 |
| 强制全量非回归 | **开发验证通过，候选 CI 待运行** | 既有冻结源码完整 API 为 `2304 passed, 7 skipped, 0 failed`；当前 meeting API `242/242`、Speech `44/44`、Web `434/434` 加 lint/build、meeting E2E `13/13` 与 flag-off `1/1`、iOS contract `13/13`、meeting release tools `83/83` 均通过。默认 Web E2E 的一个 Chromium 截图调用首轮失败、单项重跑通过；仍无同一最终候选的远端 CI artifact，因此严格发布证据不提升为 `pass`。 |

## 3. M0：合同冻结与技术预研

| MT | 代码/自动化状态 | 发布证据 | 关键文件/测试 | 明确缺口 |
| --- | --- | --- | --- | --- |
| MT-000 现有功能合同快照 | **完成** | **blocked** | immutable baseline、exact approved delta、`test_meeting_contract_baseline.py`、`meeting-contract-gate.yml` | 基线自校验和审查来源 `679d456` 的 105 个精确差异通过；当前 HEAD 因后续 IC 提交产生 1 个批准摘要漂移。IC 治理审批、候选 CI、聊天短语音/Hermes 性能比较和绑定候选的全量回归 artifact 尚未形成。 |
| MT-001 FunASR 2pass 预研 | **部分** | **not_run** | `infra/model-services/meeting-speech/`；`evaluate_asr_release.py`；speech service tests | 无授权 30-60 分钟、2/4/8 人与重叠样本报告；partial/stable/DB/visible/ACK/时间戳/CER 指标、30 分钟/4h 资源曲线、候选方案取舍均未实测。现有 8899 行为也未随候选证据复核。 |
| MT-002 Hermes Run 级模型隔离预研 | **完成** | **partial** | `decisions/2026-07-14-meeting-hermes-immutable-target-pool.md`；`meeting_hermes_runner.py`；`scripts/hermes/meeting_targets.py`；相关 tests | ADR、不可变 target pool 与 exact profile delta 已有，但两场真实并发不同模型、pinned outage、零串用及运行前后 profile hash 的候选级证据未执行。 |
| MT-003 声纹评测与隐私基线 | **部分** | **not_run** | `evaluate_voiceprint_release.py`；voiceprint worker/repository/tombstone/restore tests；auto-match fail-closed 配置 | 无授权且独立的开发/验证集、真实 encoder 固定报告、2-8 人 DER、Top-1、FAR 校准、加密轮换审查与具名安全/隐私批准；因此 auto-match 必须保持关闭。 |

## 4. M1：独立领域骨架

| MT | 代码/自动化状态 | 发布证据 | 关键文件/测试 | 明确缺口 |
| --- | --- | --- | --- | --- |
| MT-010 Feature Flag 与配置 | **完成** | **partial** | `meeting_config.py`；`meeting_stream_deployment.py`；env examples；`test_meetings_router.py`；`meeting-feature-disabled.spec.ts` | 开关默认关闭、失败闭合和直达路由已自动化；仍缺绑定候选的 flag-off 进程/网络审计与生产配置验证。 |
| MT-011 数据表与幂等迁移 | **部分** | **blocked** | migrations `002`-`005`/`007`/`008`；`test_meeting_migration.py`、import/native migration tests | SQLite 可重复迁移及 native flag-off 不创建、不反射、不校验原生表已有覆盖；生产 PostgreSQL 启用态迁移实跑、旧版本应用兼容及迁移前后旧 schema 空 diff 未形成发布证据。exact delta 中 2 个旧 DB 新表均为非会议的后台任务/IC lease。 |
| MT-012 Repository、状态机与 Outbox | **完成** | **partial** | `meeting_repository.py`、`meeting_state_machine.py`、`meeting_event_store.py`；repository/state-machine tests | 状态、幂等 stable/outbox、cursor、乐观锁、alias/revision 优先级有测试；缺生产 PostgreSQL 并发/故障恢复和候选级执行记录。 |
| MT-013 权限与对象隔离 | **部分** | **partial** | `meeting_permissions.py`；meetings/stream/import/export/voiceprint/native tests | 多条 owner 404/BOLA 路径已有自动化；尚无一份覆盖 session/audio/segment/artifact/export/voiceprint/native capture 全矩阵的双用户发布扫描与安全签字。 |
| MT-014 前端路由与空骨架 | **完成** | **partial** | `routes.tsx`、`featureRouteGate.ts`、`MeetingUnavailable.tsx`、Meeting pages；routes/flag-off E2E | 懒加载、导航隐藏、直达路由不请求会议 API 和单布局已验证；仍缺绑定候选的桌面/移动端全路由截图与全量非回归 artifact。 |

## 5. M2：实时录音、传输与字幕

| MT | 代码/自动化状态 | 发布证据 | 关键文件/测试 | 明确缺口 |
| --- | --- | --- | --- | --- |
| MT-020 Meeting Speech Service | **部分** | **partial** | `infra/model-services/meeting-speech` FunASR adapter/runtime/protocol/metrics；speech 全量测试；2026-07-15 授权录音只读探针 | 修复 partial 身份字段后前端不再静默丢弃句中结果；200ms 采集、`0,5,2` online chunk 和 400ms 首次解码音频预算已有自动化。重启真实 8901 后用现有授权会议录音连续发言区间按 200ms 实时节奏执行 12 次，语音服务首个非空 partial 为 P50 `492.0ms`、P95/最大 `540.4ms`，均在第 3 帧出字；该聚合探针不保存文本。它证明语音服务已低于 1.2s 目标，但不替代真实麦克风经浏览器/API 网关到像素可见的端到端 P95，也未完成 CER、容量、4h 与 8899 非回归发布报告。 |
| MT-021 Stream Ticket 与 WebSocket Gateway | **完成** | **partial** | `meeting_stream_ticket.py`、`meeting_stream_gateway.py`、`meeting_stream.py`；stream gateway/foundation/deployment tests | 一次性 ticket、Origin/lease、frame/ACK/去重/限流/heartbeat/flush 有实现；缺真实代理、网络故障、负载和 gateway restart 的发布运行。 |
| MT-022 浏览器 AudioWorklet | **完成** | **partial** | `audioCapture.ts`、`audioProtocol.ts`、`captureAdapter.ts`；protocol/adapter tests；real-microphone E2E 配置 | 16k mono PCM、sequence/epoch 和显式权限路径已有；浏览器自动化不证明多设备切换、权限撤销和真实硬件背景行为。 |
| MT-023 断线缓存与恢复 | **完成** | **partial** | `meetingOutbox.ts`、`meetingStream.ts`、`useMeetingRealtime.ts`；outbox/stream tests；responsive E2E | IndexedDB outbox、checkpoint/replay、gap 和刷新恢复有自动化；缺真实长网络抖动、gateway restart 与零 stable 丢失/重复证据。 |
| MT-024 实时逐字稿 UI | **完成** | **partial** | `TranscriptTimeline.tsx`、event reducer、pagination/virtualization modules；timeline/reducer/gateway tests；responsive E2E | partial 的 `segment_token -> utterance_id` 网关映射、原位替换、stable 去重、跟随、ARIA 与 DOM 窗口化已实现；缺授权真人事件流下的首字可见延迟、375-1920 候选截图、人工可访问性审计和长会性能曲线。 |
| MT-025 音频分片持久化 | **部分** | **not_run** | `meeting_audio_store.py`、stream gateway、manifest/finalization tests | 原子 chunk、摘要/manifest、ACK 顺序和 gap 检查有代码；4h 持续存储、句柄/RSS 上界及真实不可写存储安全停录未执行。 |

## 6. M3：说话人、回放与订正反馈

| MT | 代码/自动化状态 | 发布证据 | 关键文件/测试 | 明确缺口 |
| --- | --- | --- | --- | --- |
| MT-030 实时匿名 Speaker Track | **完成** | **partial** | speech speaker adapter/metrics、segment patch、`evaluate_diarization_release.py`、speaker mapping/evaluator tests | 匿名 track、晚到 patch、unknown、非阻塞路径和低基数 assigned/unassigned/failed、created/reused 指标已有自动化，DER/碎片化/过合并/纯度门禁会对空或小样本失败关闭；授权 2-8 人、重叠真实会议的至少一小时评估报告与延迟证据仍 **not_run**。 |
| MT-031 人工重命名与优先级 | **完成** | **partial** | segment speaker PATCH；repository alias/version/幂等；`TranscriptTimeline.tsx`、`SpeakerPanel.tsx`；speaker mapping tests；responsive E2E | 已覆盖仅改单段的受控拆轨、同 track 全场批量改名、文本不变、BOLA、version/409、幂等重放及 375px 浏览器交互；仍缺绑定干净候选的真实后端并发与发布审计 artifact。 |
| MT-032 会后音频封装和 Range 回放 | **完成** | **partial** | `meeting_audio_store.py`、playback router；`MeetingAudioPlayer.tsx`、`playbackTracking.ts`；audio replay/stop finalization tests、responsive E2E | Range、鉴权、seek/倍速、按时间有界加载、当前句非纯颜色高亮、自动滚动、手动暂停/恢复跟随和点击句子跳转已有；现已禁止请求线程即时封装，但仍缺真实长录音封装、Range 代理兼容与绑定候选的真实浏览器证据。 |
| MT-033 会后最终 ASR 与 Speaker 重聚类 | **完成** | **partial** | `meeting_finalization.py`、`meeting_speaker_recluster.py`、AI worker、speech independent-window protocol、`evaluate_diarization_release.py`；finalization/recluster/worker tests；2026-07-15 授权样本只读重跑 | 最终 ASR 使用稳定 run ID、默认并发 2、2 秒重叠、独立窗口幂等缓存与词时间戳/中点确定性去重；全场重聚类、运行时 `diarizer_ref` 指纹、人工/声纹 review-only 保护和临时向量不落 PostgreSQL/Milvus 均保留。相同授权长录音的 1019.873s 有效音频重跑为 37 窗口、272.560s、RTF `0.2672`，较历史 331.494s/RTF `0.325` 快约 17.8%，证明并行化真实生效；但仍高于 RTF `0.25` 门槛，且单样本不是 P95 发布报告。 |
| MT-034 句级人工订正 | **完成** | **partial** | repository correction APIs；Meeting detail/live UI；repository/AI worker tests | expected revision、409、撤销、意图与 diff 已实现；缺端到端冲突合并和真实用户可用性验证。 |
| MT-035 Correction Feedback 管道 | **完成** | **partial** | correction event/contracts/repository；repository/boundary tests | 同事务 revision+feedback、分类/provenance、opt-out/revert 已覆盖；缺授权/用途审查及离线训练数据治理签字。 |
| MT-036 个人术语候选和版本 | **完成** | **partial** | `meeting_lexicon_service.py`、repository、`MeetingLexicon.tsx`；lexicon tests | 候选、确认/拒绝、手工条目、不可变版本/hash、pause/delete/rollback 和 owner scope 已实现；缺生产数据迁移与真实恢复验证。 |
| MT-037 将订正用于后续识别 | **完成** | **partial** | stream start/hotword update、speech pending-version queue、AI glossary、版本事件；gateway/speech boundary tests；ASR evaluator paired cases | 运行中词库由 heartbeat 检测，按精确 audio sequence 排队应用，支持 queued/applied ACK、请求幂等、v2→v3 和旧 outbox 音频保留旧版本；partial/final 记录识别时版本。真实词库前后 CER、实体召回和误触发 A-B 仍 **not_run**。 |

## 7. M4：跨会议声纹

| MT | 代码/自动化状态 | 发布证据 | 关键文件/测试 | 明确缺口 |
| --- | --- | --- | --- | --- |
| MT-040 Voiceprint Consent | **完成** | **partial** | `SpeakerPanel.tsx` “本场全部/保存声纹”与授权对话框；voiceprint repository tests | 同意前不持久化、policy/purpose/scope/source 与 meeting flag 路径已有；缺隐私文本批准和真实审计链复核。 |
| MT-041 样本选择和加密注册 | **完成** | **partial** | `meeting_voiceprint_worker.py`；AES-GCM/HKDF、sample quality、cleanup tests | 清晰非重叠样本、collecting、加密/version 和失败清理有测试；缺真实 encoder、密钥轮换演练及异常进程/临时文件运行扫描。 |
| MT-042 跨会议匹配 | **完成** | **partial** | voiceprint worker/repository；threshold policy；match tests | owner-private active consent、score/margin/duration/quality 和 unknown/suggestion/auto gate 已实现；阈值没有独立验证集报告，auto-match 不得开启。 |
| MT-043 确认、拒绝和撤销 | **完成** | **partial** | voiceprint decision APIs/repository；idempotency/suppression/audit tests | 幂等 decision、拒绝抑制、人工优先和无 embedding audit 已覆盖；缺真实竞态与安全审阅。 |
| MT-044 声纹管理和删除 | **完成** | **not_run** | `MeetingVoiceprints.tsx`；retention/tombstone/restore/reconcile tests；runbook | 列表、pause/resume/re-enroll/revoke/delete、密文清理和外部 tombstone 有实质实现；真实备份恢复、未来 match=0、具名隐私/安全批准未执行。 |

## 8. M5：Hermes 模型、纠错与纪要

| MT | 代码/自动化状态 | 发布证据 | 关键文件/测试 | 明确缺口 |
| --- | --- | --- | --- | --- |
| MT-050 动态模型目录 | **完成** | **partial** | `meeting_model_catalog.py`、model catalog router/UI/tests | 只读目录、脱敏、locality/capability/availability、TTL/admin refresh 已实现；缺真实多 target 健康变化和目录刷新运行证据。 |
| MT-051 会议专用 Hermes Runner | **完成** | **partial** | immutable target pool ADR；`meeting_hermes_runner.py`；runner tests | 最小输入、快照、allowlist、结构化输出与云端脱敏已有；缺真实并发不同模型零串用、故障注入及候选 profile hash 通过证据。 |
| MT-052 模型选择和切换 | **完成** | **partial** | model setting/snapshot repository、selector UI、AI worker tests | none/pinned/auto、no fallback、云确认、生效 ordinal 与旧 job snapshot 已实现；缺真实切换不影响 ASR 和并发压力证据。 |
| MT-053 Stable Text Correction | **完成** | **partial** | AI worker correction patch/schema/provenance；worker tests | base revision、关键实体复核、人工锁、diff/undo/provenance 与超时隔离有测试；缺真实模型质量/超时率与 prompt injection 发布证据。 |
| MT-054 Rolling Minutes | **完成** | **partial** | AI scheduler/worker、rolling artifact UI/tests | debounce、水位幂等、合并、临时标签、更新时间/模型已有；缺真实负载下 freshness P95 <= 90s。 |
| MT-055 Final Minutes | **完成** | **partial** | final artifact schema/worker/UI；AI worker/review artifact tests | final 水位、结构化产物、source segment、schema fail-closed、再生成版本均有；修复后样本已证明纪要与纠错并行，`final_minutes` queue-to-complete `282.410s`、创建到首次 ready `616.93s`，仍超过 `180s` 门槛，尚无满足样本量的 P95 发布报告。 |

## 9. M6：会后完善与长录音导入

| MT | 代码/自动化状态 | 发布证据 | 关键文件/测试 | 明确缺口 |
| --- | --- | --- | --- | --- |
| MT-060 会后工作台 | **完成** | **partial** | `MeetingDetail.tsx`、artifacts/player/speaker components；detail/review artifact tests | tabs、stale、版本/再生成和证据跳转已有；缺真实产物全流程、版本对比视觉验收及候选 E2E artifact。 |
| MT-061 导出 | **完成** | **partial** | export router/service/worker；`test_meeting_exports.py`；exports runbook | TXT/MD/SRT/VTT/JSON 与 DOCX、layer/version、ticket、转义/完整性/审计路径已有，PDF 明确非阻断不可用；缺真实大文件与代理下载、安全复核。 |
| MT-062 长录音文件导入 | **完成** | **partial** | import router/service/storage/worker；`MeetingImport.tsx`；import/migration tests；runbook | 独立路由、可续传 chunk、限制、复用 meeting/postprocess 已实现；一条修复后样本的创建到 `postprocess queued` 为 `2.999s`（包含上传/拼接/探测/转码/持久化，不是纯转码），但最终 ASR/纪要仍超门槛，缺优化后 RTF/P95 发布报告。 |

## 10. M7：生产硬化与发布

| MT | 代码/自动化状态 | 发布证据 | 关键文件/测试 | 明确缺口 |
| --- | --- | --- | --- | --- |
| MT-070 安全与隐私测试 | **部分** | **not_run** | 多个 BOLA/ticket/limits/path/symlink/redaction/tombstone/prompt schema tests；release evaluators | 单项安全自动化较多，但没有覆盖任务书完整矩阵的独立运行报告、敏感数据扫描 artifact、渗透结果或具名安全/隐私签字。 |
| MT-071 性能与长稳 | **部分** | **partial** | `evaluate_performance_release.py`、worker lane/priority、independent-window bounded finalization tests；2026-07-15 实时/最终 ASR 聚合探针 | 评估器已冻结 RTF/AI/rolling/final/soak/recovery 门槛，调度已拆 lane；真实 8901 首个 partial 的 12 次 P95 为 `540.4ms`，相同授权长录音默认 2 路 final-ASR 重跑为 272.560s、RTF `0.2672`，证明首字和并行化均有实效，但 final RTF 仍未过 `0.25` 且样本量不足以形成发布 P95。`C_release`、+20% 负载、4h 数字采样、重启/DB/存储/Hermes 30m/双模型实跑仍缺失。 |
| MT-072 可观测性和运维手册 | **完成** | **partial** | `meeting_metrics.py`、speech/recluster metrics、`infra/monitoring/meetings/*`、`docs/runbooks/meeting-*.md` | 实时 speaker assigned/unassigned/failed、track created/reused 以及 durable recluster result/decision 均为固定枚举低基数指标，Prometheus 告警、Grafana 看板、policy/report/hash 和临时向量边界 runbook 已落盘；真实 scrape、看板导出、告警通知与排障演练仍 **not_run**。 |
| MT-073 灰度与回滚演练 | **部分** | **not_run** | feature flags、独立 worker lanes、service scripts、flag enabled/disabled E2E、runbooks | 技术开关与 fail-closed 自动化存在；白名单及 5/25/100%、30 分钟会议、网络/组件故障、drain/恢复、旧应用与 voiceprint restore 的真实演练记录不存在。 |

## 11. M8：iOS 原生采集、补传与立即回放

M8 当前是可审计的隔离实现候选，不是发布批准。`SIQ_MEETING_IOS_NATIVE_CAPTURE_ENABLED` 不得在生产开启；默认 Web `AudioWorklet + WebSocket + IndexedDB` 路径不依赖该目录。

| MT | 代码/自动化状态 | 发布证据 | 关键文件/测试 | 明确缺口 |
| --- | --- | --- | --- | --- |
| MT-080 iOS Capture ADR 与隔离骨架 | **完成** | **partial** | isolation ADR；`apps/ios-meeting-capture` Package/Capacitor/Xcode target；Web capture adapter/runtime/API；backend capability/native router；Web/Node contract tests | 已生成可打开的 `App.xcodeproj`，以本地 SPM 包链接插件并显式注册；三重 capability 选择和原生 HTTPS API origin 注入均 fail closed，普通 Web 保持原路径。仍未用 Apple SDK 编译、签名或冻结真机参数。 |
| MT-081 Swift AVAudioSession/AVAudioEngine 插件 | **部分** | **not_run** | Swift Plugin/Controller/Recorder；prepare/start/pause/resume/stop、route/interruption/media-reset 静态合同 | 代码骨架存在；Linux 未用 Xcode/iOS SDK 编译，Swift XCTest 未跑。后台 WebView 暂停、强退/重启/终止、权限与支持矩阵参数必须由真机冻结。 |
| MT-082 本地录音资产、Outbox 与恢复 | **部分** | **not_run** | Swift Store/Models/RecoveryCoordinator；protected file、fsync/atomic manifest、open-batch journal、SHA-256、quota、opaque handle；static/XCTest source | 冷启动枚举、open batch/sidecar/finalized WAV 恢复和后台 task 重绑已有代码与静态合同；Linux 未编译 XCTest，强退窗口、系统接管和 stop P95 2s 仍无真机证据。 |
| MT-083 Capture Token 与 Batch Ingest API | **部分** | **partial** | backend migrations/router/service/storage/limits；native API tests；Swift Keychain/Uploader/ServerClient | 插件已用 Keychain bearer + device ID 调用 batch/checkpoint/seal，并验证 ACK 身份、URL、响应上限和重定向；token 续期/撤销、iOS background URLSession 系统接管重启仍未端到端运行。 |
| MT-084 Checkpoint 对账与 Stream Rollover | **部分** | **partial** | backend checkpoint/rollover/gap contracts/tests；Swift Store/Controller；Web operational-state tests | 权威 checkpoint reconciliation、有序补传、可重放 rollover 和新 epoch fencing 已有；persisted gap 上报、双路径同音频去重以及 stable/event 跨 epoch 去重仍无真机端到端证据。 |
| MT-085 停止、同步与立即回放状态机 | **完成** | **partial** | backend native worker/finalization；Swift playback controller/cleanup receipt；Web `useNativeMeetingCapture`/`NativeCapturePanel`；playback/recovery tests；runbook | 会议工作台已接入原生选择、冷恢复绑定、前台 checkpoint/retry/rollover、采集/上传/字幕/封装/回放五状态、本地立即播放、服务端同位置切源/失败回落及 verified cleanup receipt。缺 Xcode 编译、真机 stop P95 2s 和离线/切网端到端证据。 |
| MT-086 iOS 安全、隐私与故障恢复 | **部分** | **not_run** | Info.plist、PrivacyInfo、AppDelegate、Data Protection、Keychain、备份排除、trusted origin/path/task checks；native retention tests | orphan 恢复、上传 task 身份重验、symlink/manifest/WAV 边界和服务端保护逻辑已有；配置和自动化仍只是评审输入，无 App Store/法务/隐私批准、真机故障矩阵或孤儿恢复演练。 |
| MT-087 真机长稳、灰度与非回归验收 | **缺失** | **not_run** | 仅有 Web/后端/iOS 静态回归基础 | 无支持机型/iOS 矩阵、锁屏 1/10/30/60 分钟、4h soak、网络/离线/中断/崩溃/升级、功耗/温升/流量/gap/回放延迟证据；Simulator 也不能替代。 |

## 12. 剩余阶段门禁顺序

1. 先由 IC 治理独立审查当前 `golden_case_manifest.json` 变更并更新批准合同，再在最终候选 CI 重跑 MT-000：immutable baseline、审阅来源 commit 和当前候选都必须通过同一 exact delta；批准范围以外的新增、缺失或 hash 变化必须失败。
2. 用授权且独立的数据执行 MT-001、MT-003 和 MT-037 评测，产出脱敏报告与 SHA-256；auto-match 在 FAR 门槛和评审通过前保持关闭。
3. 在同一候选提交上跑强制全量后端/前端/脚本/E2E 非回归，并保存命令、时间、退出码、测试数和 artifact 校验和。
4. 执行 MT-070 至 MT-073 的安全、负载、`C_release + 20%`、4h soak、故障恢复、告警和灰度/回滚演练；真实导入样本需重新证明 final ASR、rolling/final minutes 达标。
5. 在 macOS/Xcode 对现有 Capacitor target 执行 Apple SDK 编译、签名和 XCTest，冻结 AVAudioSession/编码参数；随后完成 iPhone 锁屏、来电/路由/切网/离线/崩溃升级、回放切源、清理回执与 4h 真机验收。
6. 仅当上述任务均绑定同一候选提交、release evidence bundle 校验通过且安全/隐私/发布负责人签字后，才能把总体状态从 `blocked` 改为 `pass`。
