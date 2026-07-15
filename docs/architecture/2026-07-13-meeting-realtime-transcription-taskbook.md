# SIQ 会议实时转写与智能纪要新增功能开发任务书

> 日期：2026-07-13
>
> 文档编号：SIQ-MEETING-TRANSCRIPTION-2026-07-13
>
> 版本：1.0
>
> 状态：需求冻结 / 可直接进入技术预研与分阶段开发
>
> 适用仓库：`/home/maoyd/siq-research-engine`
>
> 交付性质：Additive Only（仅新增功能，不改变项目现有任何功能）
>
> 需求权威：本文第 0、1、17、20-25 节为实施和验收的强制合同

## 0. 快速结论

本任务新增一个独立的“会议转写”产品域，为 SIQ 提供接近飞书妙记使用体验的会议实时语音转文字、时间戳、说话人分离、跨会议声纹识别、音频回放、实时 AI 纪要以及会后整理能力。

目标链路如下：

```text
浏览器麦克风 / 后续系统音频
  -> SIQ 同源鉴权会议流网关
  -> 流式 ASR 中间结果（partial）
  -> 句尾二遍 ASR 确定结果（final）
  -> 说话人聚类
  -> 已授权声纹库身份匹配
  -> 可追溯逐字稿
  -> Hermes 会议工作流
       -> 用户选择的可用本地或云端模型
       -> 受控文本修订
       -> 滚动纪要、决定、观点、待办
  -> 会后全量复核与最终纪要
```

必须遵守以下核心决策：

1. 会议功能使用独立导航、路由、前端 feature、API router、服务、数据表、存储目录和 worker。
2. 不复用或改造现有 `/primary-market/meeting`；该路由继续表示一级市场投研决策会议室。
3. 不改造现有聊天短语音。`/api/chat/transcribe`、60 秒限制、原始音频回放和自动发送行为必须保持不变。
4. 当前 `8899 /ws` 可作为预研参考，但不能直接作为生产会议实时转写底座；会议功能应使用独立的 FunASR 2pass 流式服务或等价实现。
5. Nemotron 8007 只是 Hermes 可选模型之一，不得在会议业务代码中硬编码端口、模型名或供应商。
6. 用户可从 Hermes 已配置且运行时检测可用的本地或云端模型中选择“AI 整理模型”。
7. 用户明确选择模型时必须固定该模型，禁止静默切换；只有“自动选择”模式才允许按显式策略降级。
8. 会议选择模型不得调用当前会改写共享 profile YAML 的全局模型切换逻辑。
9. LLM 不进入毫秒级字幕关键路径，不得无痕覆盖原始 ASR 文本。
10. 声纹识别是独立的说话人 embedding 注册和匹配能力，不由 ASR 文本或 LLM 猜测身份。
11. 首次把“发言人 1”重命名为具体人员时，只在用户明确同意后保存跨会议声纹。
12. 所有声纹自动命名、LLM 修订、纪要结论都必须可撤销、可追溯并关联原始音频时间戳。
13. 用户对具体发言句的人工订正必须保存 revision 和结构化 correction feedback。
14. 只有用户允许贡献且被分类为真实识别错误的修改，才可进入个人术语候选。
15. 订正通过版本化热词、上下文和后处理提升未来识别；禁止每次修改直接在线训练生产模型。

## 0.1 Additive Only 强制约束

“只新增、不改变现有功能”是发布阻断项，不是一般性建议。

本任务允许的改动类型只有：

- 新增会议路由和导航项。
- 新增会议前端 feature、页面、组件、Hook、样式和测试。
- 新增会议 API router、service、contract、worker、模型和测试。
- 新增会议专属数据库表、索引和幂等迁移。
- 新增会议专属音频与产物存储根目录。
- 新增会议专属 Hermes 工作流 profile、模型注册适配层或隔离 gateway。
- 在既有入口文件中增加最小注册代码，例如注册新路由、挂载新 router、增加新 feature flag。
- 对共享组件做保持原有默认行为的向后兼容扩展，且必须有原功能回归测试证明。

以下既有功能和契约必须保持原样：

| 既有能力 | 不变要求 |
| --- | --- |
| 聊天短语音 | `/api/chat/transcribe`、60 秒上限、自动发送、音频附件回放行为不变 |
| 问答助手 | `/chat`、浮动 ChatBot、已有会话和附件行为不变 |
| 一级市场投研会议室 | `/primary-market/meeting` 页面、API 和 R0-R4 工作流语义不变 |
| Hermes profiles | 现有 `siq_assistant`、`siq_analysis`、`siq_*` 和 legacy profiles 不被会议任务改写 |
| Hermes 全局模型设置 | 现有设置页和文字命令切换模型的行为不变 |
| FunASR 短语音服务 | `8899 /asr`、`/v1/audio/transcriptions` 和现有 `/ws` 协议不破坏 |
| 既有数据库 | 不修改、重命名、删除已有表和列，不改变已有约束语义 |
| 既有 API | 不改变已有路径、请求字段、响应字段、状态码和权限行为 |
| 既有前端导航 | 不删除、重命名或改变已有导航目标；只插入新增“会议转写”项 |
| 既有任务系统 | 不把现有 job service 顺手迁移到会议 worker，不改变现有任务终态 |

## 0.2 开发禁止项

本任务明确禁止：

- 禁止把会议功能写进 `chat_voice_service.py` 或复用聊天短语音状态机。
- 禁止把长会议录音作为聊天消息上传或放入聊天历史。
- 禁止把 `/primary-market/meeting` 重命名、复用或重定向到本功能。
- 禁止前端直接连接 `8899`、`8007` 或任何云端模型 API。
- 禁止在浏览器暴露 Hermes token、模型 API key、声纹 embedding 或内部服务地址。
- 禁止硬编码 Nemotron、MiniMax、Kimi、StepFun、Qwen、Gemma 等模型名称或端口到会议业务流程。
- 禁止调用 `set_profile_model_mode()` 或 `set_all_profile_model_modes()` 实现每场会议的模型切换。
- 禁止为了会议功能修改全部 Hermes profile 的默认模型或 fallback 顺序。
- 禁止让选择本地模型的会议静默把逐字稿发送到云端。
- 禁止 LLM 直接覆盖 `raw_text` 或 `asr_final_text`。
- 禁止把每次人工编辑不加区分地作为 ASR 正确答案或立即写入热词。
- 禁止单次订正自动触发生产模型训练、参数更新或全局词库发布。
- 禁止把个人订正和术语默认共享给其他用户或现有聊天语音链路。
- 禁止以 LLM 输出反向伪造 ASR 置信度、时间戳或说话人身份。
- 禁止未授权建立、共享或长期保留声纹。
- 禁止低置信度声纹直接显示为确定实名。
- 禁止在 API 进程事件循环中执行长时间 ASR、全场说话人聚类或大模型整理。
- 禁止一次性把数小时 PCM 或压缩音频读入内存。
- 禁止依赖单个浏览器 Blob 作为会议唯一录音副本。
- 禁止在本功能 PR 中混入无关重构、全仓格式化、依赖升级或既有数据迁移。

## 0.3 允许触碰的最小公共文件

下列公共文件仅允许做括号内的最小加法式修改：

| 文件 | 允许修改 | 禁止修改 |
| --- | --- | --- |
| `apps/web/src/app/routes.tsx` | 注册 `/meetings` 新路由和一个新导航项 | 改变现有路由、标签、权限或排序语义 |
| `apps/web/src/components/layout/Layout.tsx` | 仅把 `/meetings` 加入隐藏全局 ChatBot 的新页面集合 | 改变其他页面 ChatBot 显示规则 |
| `apps/api/main.py` | 注册新增 meetings router | 重排或改变已有 router dependency |
| `apps/api/database.py` 或模型加载入口 | 仅注册新增会议 metadata / 幂等迁移 | 修改已有表、启动迁移和兼容逻辑 |
| `infra/env/local.example` | 追加 `SIQ_MEETING_*` 示例变量 | 修改已有变量默认含义 |
| `infra/env/production.example` | 追加 `SIQ_MEETING_*` 示例变量 | 修改已有生产参数语义 |
| `start_all.sh` 或独立 service unit | 仅在 feature flag 开启时启动新增会议服务 | 改变默认已有服务启动顺序和端口 |

如果实施发现必须扩大上述公共文件改动范围，必须先更新本文、说明必要性、补充非回归测试并单独评审，不能在功能实现中顺带完成。

## 1. 背景与冻结需求

### 1.1 产品背景

SIQ 当前已经具备：

- 问答助手短语音输入：按住说话、松开发送、FunASR 转写、自动交给智能体、保留音频回放，单条上限 60 秒。
- 本机 FunASR 服务：中文识别、热词、时间戳、匿名说话人聚类和 WebSocket 接口。
- Hermes 多智能体运行时：可配置本地和云端模型。
- 本地 Nemotron 3 Nano Omni：可作为 Hermes 的一个本地模型选项。
- 现有一级市场投研会议室：面向投委会 R0-R4 决策流程，与真实会议录音转写不是同一产品域。

本任务解决的是长时会议的持续采集、实时转写和会后整理，不属于聊天消息能力，也不属于一级市场投研决策会议室。

### 1.2 已冻结的用户需求

#### 会议入口

- 增加独立顶级导航标签页，建议名称为“会议转写”。
- 会议功能不进入聊天窗口。
- 会议列表、实时会议和会后详情使用独立路由。
- 后续长录音文件导入也进入该产品域，不进入聊天窗口。

#### 实时转写

- 支持用户在浏览器开始会议并持续录音。
- 实时展示语音转文字结果。
- 支持临时结果、稳定结果和已优化结果的清晰状态。
- 支持句级或词级时间戳，点击时间戳可定位音频。
- 会议结束后执行全量复核，生成最终逐字稿。
- 目标体验接近飞书妙记，但必须通过本项目真实会议集量化验收，不能只以功能名称判断。

#### 说话人与声纹

- 支持说话人分离，至少能显示“发言人 1、发言人 2”。
- 用户可在本场会议中重命名发言人，并批量应用到相关片段。
- 重命名时提供“仅本次使用”和“保存声纹，未来自动识别”两个明确选项。
- 用户明确授权后，系统建立跨会议持久声纹。
- 下次会议启用声纹识别时，系统可自动把同一声音映射为已登记姓名。
- 高置信度可自动命名；中置信度只提示“可能是某人”；低置信度保持匿名。
- 用户可确认、拒绝、撤销、重新采集或删除声纹。
- 所有自动命名都保留原始 cluster ID 和匹配证据。

#### 音频

- 保存会议原始音频并支持会后回放。
- 实时转写与录音持久化相互独立，AI 服务故障不能导致录音丢失。
- 断网后支持短时本地缓存和恢复续传。
- 首期重点支持线下会议的浏览器麦克风。
- 在线会议远端声音需要标签页/系统音频、桌面端、虚拟声卡或会议机器人，作为后续能力明确规划。

#### AI 纠错与会议产物

- ASR 负责实时听写，LLM 不逐字阻塞字幕链路。
- 稳定句子可交给用户选择的 Hermes 模型进行受控术语和错字修订。
- 必须保存原始 ASR、ASR 确定文本、LLM 修订文本和人工确认文本。
- 用户可以在任一稳定发言句后点击“修改”，订正识别文字并保存人工 revision。
- 系统记录本次 ASR 原文、人工订正文、最小差异、说话人和 ASR 版本，形成可追溯订正事件。
- 用户可明确标记本次编辑是“识别错误”还是“仅修改表述”；只有识别错误可进入识别改进候选。
- 在用户启用“使用订正提升识别”后，系统可把高质量、重复出现或用户明确确认的专有名词沉淀为个人术语和易错词。
- 已激活术语可用于本场后续语音和未来会议的 ASR 热词、上下文偏置及受控后处理。
- 普通润色、长段改写、纯标点调整、未确认修改和撤销过的错误订正不得自动进入识别词库。
- 单次修改不得直接触发生产 ASR 模型在线训练或自动微调；模型训练只能使用经过授权、脱敏、审核和离线评测的数据集。
- 每 30-60 秒或达到足够稳定文本后更新一次“实时纪要草稿”。
- 会议结束后基于完整逐字稿生成最终智能纪要。
- 会后支持会议记录、议题、章节、决定、风险、待办和发言人观点。
- 每条决定、观点和待办应关联原始 transcript segment 和音频时间戳。

#### Hermes 模型选择

- 会议功能不指定固定模型。
- 用户可以选择 Hermes 中已配置且检测可用的本地或云端模型。
- 模型列表由后端运行时返回，前端不得写死。
- 会议模型选择与 ASR 引擎解耦；切换模型不影响实时字幕和录音。
- 用户明确选定模型后，后续 AI 任务固定使用该模型。
- 模型切换只影响未来任务；历史产物不静默覆盖，可由用户选择用新模型重新生成。
- 每份产物记录实际 provider、model、profile/runtime revision 和 prompt version。

### 1.3 本期非目标

以下内容不属于首个可发布版本，但数据模型和接口应允许后续扩展：

- 会议机器人自动加入飞书、腾讯会议、Zoom 或 Teams。
- 原生桌面端和系统级全局音频采集。
- 多轨专业录音设备控制。
- 端到端视频会议、屏幕共享和聊天能力。
- 通过文字内容猜测发言人身份。
- 未经授权的组织级声纹搜索。
- 将声纹作为登录认证凭据。
- 用会议功能替换现有飞书妙记、聊天短语音或一级市场投研会议室。
- 在首期承诺任意场景固定达到某个宣传准确率。

## 2. 行业能力参考与设计判断

### 2.1 可确认的产品能力

公开资料可确认：

- 飞书妙记支持实时语音转文字、说话人拆分、授权声纹实名和 AI 实时总结，但未公开说明每个实时字幕 token 都经过聊天大模型重写。
- 飞书逐字稿与智能纪要是不同内容层，逐字稿可带说话人和时间戳。
- 阿里云实时 ASR 区分中间结果与最终结果；Fun-ASR/Paraformer 提供流式和时间戳能力。
- 科大讯飞实时转写通过 WebSocket 输出中间/确定结果、时间戳、说话人分离和可选已注册声纹身份。

参考资料：

- [飞书 V7.44 实时转写、声纹与 AI 实时总结](https://www.feishu.cn/hc/zh-CN/articles/772105146309)
- [飞书妙记文字记录与智能纪要](https://www.feishu.cn/hc/zh-CN/articles/022111234449)
- [飞书妙记逐字稿导出 API](https://open.feishu.cn/document/minutes-v1/minute-transcript/get)
- [阿里云实时语音识别](https://help.aliyun.com/zh/model-studio/qwen-real-time-speech-recognition)
- [通义听悟接口与实现](https://help.aliyun.com/zh/tingwu/interface-and-implementation)
- [科大讯飞实时语音转写大模型](https://www.xfyun.cn/doc/spark/asr_llm/rtasr_llm.html)
- [科大讯飞声纹注册](https://www.xfyun.cn/doc/spark/asr_llm/voice_print.html)

### 2.2 本项目采用的判断

会议实时体验应采用分层处理，而不是让 LLM 每几百毫秒重写字幕：

```text
流式 ASR partial
  -> VAD 端点
  -> 二遍 ASR final
  -> 标点 / 数字格式化 / 热词
  -> 说话人聚类与声纹匹配
  -> LLM 受控修订
  -> 滚动 AI 纪要
  -> 会后全量最终产物
```

其中：

- `partial` 为可变草稿，不进入最终证据链。
- `asr_final` 为 ASR 稳定基础文本。
- `corrected` 为 LLM 建议或已应用修订，不能覆盖基础文本。
- `human_verified` 为人工确认版本，后续后台任务不得覆盖。

## 3. 术语和状态定义

### 3.1 核心术语

| 术语 | 定义 |
| --- | --- |
| VAD | 语音活动检测，用于判断语音开始和句尾 |
| Streaming ASR | 持续接收小音频块并产生中间识别结果的 ASR |
| 2pass | 第一遍低延迟流式识别，句尾再用高精度非流式模型复核 |
| Partial | 尚未稳定、可被后续结果替换的实时草稿 |
| ASR Final | 句尾后由 ASR 确定的稳定文本 |
| Diarization | 把音频分成不同匿名说话人 cluster |
| Voiceprint Enrollment | 用户授权后，把清晰语音转换为可跨会议匹配的声纹特征 |
| Voiceprint Identification | 将当前说话人 cluster 与已授权声纹库比较并映射到身份 |
| Rolling Minutes | 会议进行中周期性生成的临时 AI 纪要 |
| Final Minutes | 会后基于最终逐字稿生成的正式纪要 |
| Model Ref | Hermes 模型注册表中的稳定逻辑标识，不是端口或明文密钥 |
| Artifact Provenance | 产物使用的逐字稿版本、模型、prompt、时间和来源 segment 记录 |

### 3.2 文本层级

每个 transcript segment 至少保存：

```text
raw_text               原始流式/首遍识别文本
asr_final_text         句尾二遍 ASR 确定文本
normalized_text        标点、数字格式等确定性规范化文本
llm_corrected_text     LLM 受控修订文本
human_verified_text    人工确认文本
display_text           只读派生字段，按优先级选择展示层
```

展示优先级：

```text
human_verified_text
  > llm_corrected_text
  > normalized_text
  > asr_final_text
  > raw_text
```

该优先级只决定 UI 展示，不允许删除低层数据。

## 4. 当前项目能力与差距

### 4.1 聊天短语音现状

现有实现位于：

- `apps/web/src/components/chat/useVoiceRecorder.ts`
- `apps/web/src/components/chat/VoiceInputButton.tsx`
- `apps/api/services/chat_voice_service.py`
- `apps/api/routers/chat.py`

当前行为是录制完整 Blob、最长 60 秒、上传 `/api/chat/transcribe`、调用 FunASR HTTP、保存音频附件并自动发送给智能体。

结论：该链路适合微信式短语音，不适合长会议。会议功能只能复用通用 API 客户端和基础 UI token，不能复用其录制状态机和 endpoint。

### 4.2 当前 FunASR 8899

当前服务脚本：

- `/home/maoyd/modles_setup/start_funasr_vllm.sh`
- `/home/maoyd/services/FunASR/examples/industrial_data_pretraining/fun_asr_nano/serve_vllm.py`

当前能力：

- `POST /asr` 文件识别。
- `POST /v1/audio/transcriptions` OpenAI-compatible 文件识别。
- `WS /ws` 接收 `16kHz / mono / PCM16LE`。
- 支持语言、热词、时间戳和匿名说话人聚类。

已确认差距：

- `/ws` 虽持续接收音频，但不输出句中 partial 文本，只返回 VAD 已锁定的句子。
- 默认短句需等待约 2 秒静音才确认句尾。
- 5.55 秒中文样例在逻辑 7.30 秒才出现首句，不满足目标 partial 延迟。
- 实时过程中没有 speaker，`SPK0/SPK1` 只在 `STOP` 后全场聚类。
- `SPK0/SPK1` 是匿名 cluster，不是跨会议实名声纹。
- 整场音频持续 `np.concatenate`，长会议会持续增长内存。
- 同步推理运行在异步 WebSocket handler 中，存在事件循环阻塞风险。
- `8899` 监听 `0.0.0.0` 且无用户级鉴权，不能直接暴露给浏览器。

结论：保留 8899 服务供现有短语音使用；会议功能另建流式 ASR 接入层，不修改现有接口语义。

### 4.3 FunASR 可复用底座

本机 FunASR 仓库已有官方 2pass 在线/离线服务和 Paraformer streaming 示例，适合用作会议底座。另有 `serve_realtime_ws.py` 原型可输出滚动 partial，但存在同步推理、全量音频累计和反复全量聚类问题，只能作为协议与算法参考，不能直接作为生产实现。

首个技术预研必须比较：

1. FunASR 官方 CPU 2pass 服务。
2. 对现有模型服务增加隔离的 partial session adapter，但不改变 8899 既有协议。
3. 其他已验证的中文流式 ASR 引擎，仅在 FunASR 2pass 达不到指标时作为备选。

### 4.4 Hermes 与模型选择现状

当前 `apps/api/services/hermes_model_control.py` 已定义本地 Qwen、Gemma、Nemotron及云端 Kimi、MiniMax、StepFun 等模型模式。

现有切换方式会写入共享 Hermes profile YAML；该方式适合全局设置或单 profile 管理，不适合多用户同时选择不同会议模型。否则一场会议切换模型可能影响另一个会议或现有问答助手。

会议实现必须增加 request/session scoped 的 `model_ref` 解析和隔离运行目标，不能改写共享 profile。

### 4.5 前端路由现状

- `/primary-market/meeting` 已表示一级市场投研决策，不能复用。
- `Sidebar.tsx` 的 assistant 区当前是单项问答助手，会议应进入主导航 `nav`。
- `Layout.tsx` 当前在 Agent 页面隐藏全局 ChatBot；会议页面也应单独加入隐藏集合，避免双麦克风和遮挡。

### 4.6 任务与恢复现状

现有部分 durable job 仍由 API 进程线程执行，进程重启后不能可靠恢复长会议后处理。会议的录音分片、最终转写、说话人重聚类和 AI 纪要必须由独立可恢复 worker 执行。

结论：不得为复用方便把长会议任务塞进现有短任务 callable；应新增会议专属 job/outbox 和租约 worker，同时不迁移任何现有任务。

## 5. 产品信息架构与页面设计

### 5.1 导航与路由

新增主导航项：

```text
会议转写
```

推荐路由：

| 路由 | 页面 | 职责 |
| --- | --- | --- |
| `/meetings` | 会议列表 | 查看会议、状态、时长、参与者、纪要状态和创建实时会议 |
| `/meetings/new` | 新建会议 | 设置标题、语言、音频源、声纹开关和 AI 整理模型 |
| `/meetings/:meetingId/live` | 实时会议工作台 | 录音、实时字幕、说话人、实时 AI 纪要、连接状态 |
| `/meetings/:meetingId` | 会后详情 | 音频、纪要、逐字稿、观点、待办和版本管理 |
| `/meetings/lexicon` | 个人术语 | 管理由人工订正产生的候选、有效词库和版本 |
| `/meetings/voiceprints` | 声纹管理 | 管理本人已授权声纹、撤销、重采和删除 |
| `/meetings/import` | 导入录音 | 后续阶段导入长录音，不进入聊天窗口 |

路由要求：

- `/meetings` 与 `/primary-market/meeting` 完全独立。
- 会议详情必须做对象级权限检查，不能只依赖登录状态。
- 功能开关关闭时不注册可见导航；直接访问返回标准 404 或 feature disabled 页面，不影响其他路由。
- 会议页面隐藏全局浮动 ChatBot，但不改变其他页面 ChatBot 行为。

### 5.2 会议列表页

会议列表应支持：

- 会议标题、开始时间、时长、创建人。
- 状态：未开始、进行中、重连中、处理中、待复核、已完成、处理失败、已归档。
- 参与者/已识别说话人数量。
- 逐字稿和最终纪要状态。
- 当前 AI 整理模型及本地/云端标记。
- 搜索、按状态筛选、按时间排序。
- 继续异常中断的会议、打开详情、导出或删除。
- 空状态下直接提供“开始实时会议”主操作，不做营销落地页。

列表不得一次返回全文、声纹特征或大体积音频元数据。列表 API 使用分页，只返回摘要字段。

### 5.3 新建会议页

新建会议最少包含：

- 会议标题，默认可按日期生成，用户可编辑。
- 识别语言，首期默认简体中文。
- 音频源：首期为麦克风；标签页/系统音频在支持时单独显示能力检测。
- 输入设备选择和音量电平预览。
- `使用已授权声纹识别` 开关，默认关闭。
- `AI 整理` 开关，可独立关闭。
- `AI 整理模型` 选择器，由后端动态返回可用模型。
- 本地/云端数据边界提示。
- 云端模型首次用于本场会议时的明确确认。

用户可以选择“仅录音和转写”，即 `model_ref=null`；此时不运行任何 Hermes AI 任务。

### 5.4 实时会议工作台

桌面端建议布局：

```text
会议名称  ● 实时  00:37:42  延迟 1.1s  麦克风  AI: 本地模型   [暂停] [结束]
────────────────────────────────────────────────────────────────────────
发言人 / 参会人        实时逐字稿                         实时 AI 要点
张三 · 已确认          00:12:08  张三                     当前议题
发言人 2 · 待确认      已识别文本                         决定
可能是李四 · 82%       实时草稿...                        风险
声纹识别状态           [回到实时]                         待办
────────────────────────────────────────────────────────────────────────
音频输入电平  网络状态  ASR 状态  已保存时长  最近保存时间
```

布局规则：

- 顶部固定状态栏：标题、LIVE 状态、计时、连接、ASR 延迟、AI 状态、暂停、结束。
- 桌面主体建议 `220px / minmax(0, 1fr) / 340px`。
- 左侧展示匿名说话人、人工命名、声纹建议和匹配状态。
- 中间逐字稿为主区域，支持时间戳、文本状态、自动跟随和回到实时。
- 右侧使用 `实时纪要 | 决定 | 待办 | 关键词` 标签，不嵌套卡片。
- 小于 1280px 时右侧变为抽屉或标签页。
- 小于 900px 时使用 `逐字稿 | AI 要点 | 发言人` 单列切换。
- 移动端触控目标至少 44px；不得因长姓名或模型名撑破工具栏。

逐字稿展示状态：

| 状态 | UI 表现 | 是否可变 |
| --- | --- | --- |
| 实时草稿 | 弱色、末尾光标或轻量动态标记 | 可被同一 utterance revision 替换 |
| 已识别 | 正常文本，显示稳定时间戳 | 不可被新的 ASR partial 覆盖 |
| 已优化 | 小型“已优化”标记，可查看差异 | 可撤销到 ASR final |
| 已确认 | “已确认”标记 | 后台不得覆盖 |
| 待复核 | 低置信度、金额/日期/姓名提示 | 需要人工确认 |

自动跟随规则：

- 用户在底部附近时自动滚动到最新字幕。
- 用户主动向上查看历史后立即停止自动跟随。
- 停止跟随时显示稳定尺寸的“回到实时”按钮。
- 新 partial 不得导致已稳定段大幅跳动。
- `aria-live` 只播报 final segment，不对每个 partial 重复朗读。

### 5.5 暂停、恢复与结束

暂停必须真实停止采集和上传，并在时间轴记录空档：

```text
暂停 00:31:12 - 00:34:08
```

恢复后创建新的连续采集 epoch，不伪造暂停期间音频。

结束流程：

1. 用户确认结束。
2. 客户端发送最后分片并等待 ACK。
3. 流式 ASR flush 未完成 utterance。
4. 服务端关闭采集 lease。
5. 页面进入“会后处理中”，但不阻塞用户离开。
6. 后台依次执行音频封装、最终转写、说话人整理和最终纪要。

结束后的可见进度：

```text
保存录音 -> 最终转写 -> 说话人整理 -> 生成纪要 -> 可复核
```

某个可选步骤失败时保留前面已完成的产物，并支持单步骤重试。

### 5.6 会后详情页

会后详情顶部共用音频播放器和时间轴，主标签为：

```text
智能纪要 | 逐字稿 | 发言人观点 | 待办 | 文件与导出
```

#### 智能纪要

- 会议概览。
- 议题与章节。
- 关键决定。
- 分歧和未决问题。
- 风险。
- 待办及负责人、截止日期。
- 使用的 transcript revision、模型和生成时间。

“智能纪要”“发言人观点”“待办”和“关键词”读取同一份优先级为
`final_minutes -> rolling_minutes` 的结构化 `content_json`。这些标签只是同一版本产物的不同视图，
不得查询或生成独立的 `viewpoints`、`action_items`、`decisions` 或 `keywords` artifact。

#### 逐字稿

- 时间戳、说话人、文本。
- 点击时间戳定位音频。
- 切换查看显示文本、ASR 原文和修订差异。
- 每个稳定发言句提供铅笔图标“修改文字”，点击后在原位置进入编辑态。
- 编辑态提供保存、取消、撤销和“识别错误 / 仅修改表述”意图选择。
- 保存后显示差异，并允许用户选择是否将专有词加入个人术语候选。
- 编辑文本、重命名发言人、合并或拆分明显错误的 speaker track。
- 点击任一段的发言人名称时，明确选择“仅修改这一段”或“修改本场该发言人的全部发言”；前者受控拆分映射，后者只更新 track alias，均不改写逐字稿文本。
- 人工编辑后把 segment 标记为 `human_verified`。

#### 发言人观点

- 按发言人归纳主要立场。
- 观点对应的证据 segment 和时间戳。
- 支持显示同一议题下的观点差异。
- 未确认实名时继续使用“发言人 N”，禁止 LLM 猜姓名。

#### 待办

- 内容、负责人、截止日期、状态。
- 来源 segment 和生成模型。
- 用户编辑后保存人工版本，不被后续模型覆盖。

#### 文件与导出

- 原始会议音频回放和受控下载。
- Markdown、TXT、DOCX/PDF（后续）、SRT、VTT 和结构化 JSON。
- 导出时选择显示文本或 ASR 原文。
- 导出操作写审计事件。

### 5.7 纪要过期与重新生成

逐字稿、说话人或人工文本发生变化后，已有 AI 产物显示：

```text
纪要基于旧版逐字稿
```

系统不得静默重新生成。用户可以：

- 保留旧版本。
- 使用当前模型生成新版本。
- 切换模型后生成新版本。
- 对比两个纪要版本。

### 5.8 识别订正与个人术语

每个 stable segment 右侧提供熟悉的铅笔图标和 Tooltip“修改文字”，不用大面积文字按钮。点击后：

1. 原位显示文本编辑框，保留时间戳和发言人。
2. 用户修改后选择：
   - `识别错误`：记录为 ASR correction feedback。
   - `仅修改表述`：只保存人工文本 revision，不进入识别学习。
3. 系统展示最小 diff，保存时使用 segment revision 乐观锁。
4. 若检测到专有名词替换，可提示：`加入个人术语，后续会议优先识别`。
5. 用户可撤销本次修改；撤销后对应反馈从 active 变为 reverted，不再用于新词库版本。

“使用订正提升识别”必须是独立可关闭设置。关闭后仍保留逐字稿人工 revision 和审计，但不产生可用于后续识别的候选词。

建议增加会议模块内的“个人术语”页面或设置标签，支持：

- 查看激活术语、常见误识别写法和来源次数。
- 手动新增专有名词及可选权重。
- 确认、停用、编辑或删除候选词。
- 查看某术语的命中、误触发和撤销情况。
- 选择作用域：本场会议、个人未来会议；组织共享另立权限任务。
- 查看当前词库版本和生效时间。

术语建议必须区分：

| 编辑类型 | 是否可进入识别改进 |
| --- | --- |
| 专有名词/同音字订正 | 是，经过确认或质量门槛后 |
| 漏词/错词且有音频证据 | 是，先进入候选 |
| 标点和空格 | 否，归入格式化规则统计 |
| 数字显示格式 | 否，归入 ITN/规范化规则统计 |
| 大段润色或重写表达 | 否，仅人工 revision |
| 修改事实内容 | 否，不能当作 ASR ground truth |
| 已撤销或冲突修改 | 否，候选降权或停用 |

### 5.9 声纹管理页

声纹管理页只展示当前用户有权管理的身份：

- 显示名和内部 `voice_profile_id`。
- 授权状态和用途。
- 样本质量、样本数量和编码器版本，不展示 embedding。
- 创建时间、最近匹配时间。
- 暂停未来识别。
- 重新采集。
- 撤销授权。
- 删除声纹。
- 查看不含音频和向量的匹配审计。

首期建议声纹范围限定为创建者私有。组织共享声纹需要成熟 tenant、权限和合规模型，另立后续任务。

## 6. 端到端业务流程

### 6.1 开始会议

```text
用户打开新建会议
  -> 浏览器检测麦克风权限与设备
  -> API 返回会议能力和可用 Hermes 模型
  -> 用户选择声纹开关、AI 开关和模型
  -> 创建 meeting session
  -> 获取一次性 stream ticket
  -> 建立同源 WebSocket
  -> 服务端创建采集 lease 和 stream epoch
  -> 客户端开始发送带序号 PCM 音频块
```

如果 AI 模型不可用但 ASR 可用，允许用户继续“仅录音和转写”。如果 ASR 不可用，不允许伪装成正常会议，应明确阻止开始并保留会议草稿。

### 6.2 实时字幕

```text
AudioWorklet 采集
  -> 重采样到 16kHz mono PCM16LE
  -> 250-1000ms 音频块 + sequence
  -> 网关校验、ACK、顺序持久化
  -> streaming ASR partial
  -> 前端按 utterance_id 原位更新
  -> VAD 句尾
  -> 2pass ASR final
  -> segment 与 durable event 同事务提交
  -> 前端显示稳定句子
```

partial 事件可以丢失或替换；final segment 必须幂等、可重放、不可因重连重复。

### 6.3 异步说话人与 AI

稳定 segment 提交后并行触发：

```text
segment.final
  ├─ speaker worker：更新匿名 track
  ├─ voiceprint worker：在授权范围内匹配身份
  ├─ correction worker：按 3-5 句或 15-30 秒窗口受控修订
  └─ minutes worker：按 30-60 秒去抖更新临时纪要
```

这四条支线均不得反向阻塞音频接收或 stable segment 持久化。

### 6.4 用户订正反馈闭环

```text
用户点击某个 stable segment 的“修改文字”
  -> 以 expected_revision 打开原位编辑
  -> 保存人工 revision
  -> 选择“识别错误”或“仅修改表述”
  -> 系统计算 token/字符级最小 diff
  -> 记录 ASR 原文、订正文、speaker、时间戳和 ASR provenance
  -> 分类 punctuation / itn / lexical / entity / rewrite
  -> 符合条件的 lexical/entity 修改进入个人术语候选
  -> 明确确认或重复证据达到门槛后生成新词库版本
  -> 当前会议后续 ASR session 在能力允许时加载增量热词
  -> 未来会议启动时加载 active 个人术语
```

立即生效和长期学习必须分开：

- 人工 revision 保存后立即影响本句显示，并将本句设为 human locked。
- 用户明确选择“加入个人术语”时，可在本场后续音频中增量加载该术语。
- 系统自动发现的候选默认不立即激活，需重复证据、规则门槛或用户确认。
- 不自动批量改写历史相似句；可提示“检查其他相似出现”，由用户确认应用。
- 用户撤销本句订正时，反馈事件标记 reverted；仅由该事件支持的术语候选必须降权或停用。

订正对识别的提升路径：

1. ASR hotword：把正确专有名词带权加入后续流式识别。
2. Context bias：把常见误识别到正确词的映射作为受控上下文。
3. Deterministic post-correction：仅对高精度、低歧义映射做确定性修订。
4. LLM correction glossary：给会议 AI 修订提供个人术语表。
5. Offline evaluation/fine-tuning：只在授权、审核、脱敏和足量数据后离线进行，不属于单次编辑实时动作。

如果 speaker 已由人工确认或高置信声纹匹配，可记录该订正与 speaker/voice profile 的统计关联，用于分析特定发音造成的易错词；首期不得仅凭一个发言人的一次修改创建 speaker-specific 自动规则。

### 6.5 首次声纹注册

```text
发言人 1
  -> 用户重命名为“张三”
  -> 弹窗选择：仅本次 / 保存声纹用于未来识别 / 取消
  -> “仅本次”：只写 speaker alias
  -> “保存声纹”：展示用途、范围和保留策略
  -> 用户明确同意并记录 policy_version
  -> 选择多个清晰、非重叠、足够时长片段
  -> 质量检查
  -> 生成 speaker embedding
  -> 加密保存 voice profile
  -> 删除单独生成的临时样本文件
  -> 本次 speaker track 关联 voice profile
```

说明：会议原始音频按用户要求保留并支持回放；“删除临时样本”指删除为声纹注册额外生成的切片副本，不删除用户保留的会议音频。声纹用途撤销后，不再从历史会议音频重新生成声纹，除非用户重新明确授权。

注册质量要求：

- 多段非重叠语音。
- 达到配置的最低有效语音时长。
- 过滤高噪声、过短、多人同时发言和严重削波片段。
- 质量不足时显示“继续收集样本”，不能强行创建低质量模板。

### 6.6 跨会议声纹识别

```text
下一场会议启用“使用已授权声纹识别”
  -> diarization 生成匿名 speaker track
  -> 聚合该 track 多个清晰片段的 embedding
  -> 只检索当前用户授权有效的声纹库
  -> 计算 Top-1、Top-2、差值、有效时长和质量
  -> 高置信：自动应用实名，可撤销
  -> 中置信：显示“可能是张三”，等待确认
  -> 低置信：保持“发言人 N”
```

显示名解析优先级：

```text
本次人工命名
  > 本次人工确认声纹
  > 自动高置信声纹
  > 匿名说话人标签
```

人工命名和人工确认一旦发生，后台算法不得覆盖。

### 6.7 Hermes 模型选择与切换

```text
GET 模型目录
  -> 展示 configured + available + capability
  -> 用户选择 auto / pinned / no-ai
  -> meeting 保存 model_ref 与 settings_version
  -> 每个 AI job 创建不可变执行快照
  -> Hermes runner 解析到隔离 runtime target
  -> 产物保存请求模型和实际模型 provenance
```

切换模型：

1. 客户端携带 `expected_settings_version`。
2. 服务端以乐观锁保存新选择。
3. 响应 `effective_after_segment_ordinal`。
4. 已排队或正在执行的旧 job 保持原快照。
5. 新边界后的 job 使用新模型。
6. 旧产物继续保留，用户可显式重新生成。

本地切换到云端时必须再次确认文本数据边界；音频和声纹向量永远不发送给 Hermes 云端模型。

### 6.8 断网和重连

客户端使用 IndexedDB 或受限本地 outbox 保存尚未 ACK 的短期音频块：

- 每块有 `stream_epoch + sequence + capture_time`。
- 服务端 ACK 最高连续 sequence。
- 重连获取新 ticket，协商最后确认 sequence。
- 只重发未确认且仍在允许窗口内的分片。
- 服务端按幂等键去重。
- 超出本地缓存窗口形成音频缺口时，时间轴明确标记，不能隐藏。
- 页面关闭前提示正在录音；异常关闭后服务端按 lease 超时结束或标记 interrupted。

### 6.9 会后最终处理

```text
stop + ASR flush
  -> 校验音频 chunk manifest
  -> 封装可回放音频文件
  -> 分块最终 ASR 与时间戳对齐
  -> 全场说话人重聚类
  -> 重新评估授权声纹
  -> 生成最终 transcript revision
  -> 生成最终纪要 / 观点 / 决定 / 待办
  -> 标记 ready_for_review
```

会后 speaker track 可能发生合并或拆分，必须通过映射和 revision 表达，不能破坏已有 segment 和审计引用。

## 7. 目标系统架构

### 7.1 组件图

```text
Web Meeting Workbench
  ├─ REST：会议、模型、声纹、产物、导出
  ├─ WebSocket：音频上行 + 实时事件下行
  └─ IndexedDB：短期未 ACK 分片

SIQ API
  ├─ meeting router：认证、权限、合同校验
  ├─ stream ticket：短期、单次、单会议
  ├─ event read API：断线重放 durable event
  └─ audio range API：鉴权回放

Meeting Stream Gateway（新增独立进程）
  ├─ WebSocket session / lease / backpressure
  ├─ 音频 chunk 顺序持久化
  ├─ ASR adapter
  └─ durable transcript event 写入

Meeting Speech Service（新增）
  ├─ streaming ASR partial
  ├─ 2pass final
  ├─ VAD / punctuation / timestamps
  └─ speaker embedding / diarization adapter

Meeting Worker（新增独立进程）
  ├─ final transcription
  ├─ speaker reclustering
  ├─ voiceprint enrollment / matching
  ├─ LLM correction
  ├─ rolling / final minutes
  └─ exports / retention

Hermes Meeting Runner（新增隔离适配层）
  ├─ dynamic model catalog
  ├─ immutable model target resolution
  ├─ JSON schema validation
  └─ provenance

PostgreSQL / SQLite-compatible contracts
  ├─ meeting tables
  ├─ event outbox
  ├─ jobs / leases
  └─ encrypted voiceprint metadata

Meeting Artifact Storage
  ├─ audio chunks
  ├─ finalized audio
  ├─ exports
  └─ temporary enrollment slices with TTL
```

### 7.2 进程边界

- API 进程：只做认证、权限、轻量事务和 HTTP 映射。
- Stream Gateway：持有长连接、背压和 ASR session，不运行 Hermes。
- Speech Service：执行实时语音模型，不访问用户业务表。
- Meeting Worker：执行长时可恢复后处理。
- Hermes Meeting Runner：只接收稳定文本和结构化任务，不接收音频、声纹或浏览器连接。

### 7.3 故障隔离

| 故障 | 应有行为 |
| --- | --- |
| Hermes 不可用 | 字幕和录音继续；AI 标记延迟，可稍后重试 |
| 选定模型不可用 | pinned 模式不切换；AI 暂停；ASR 继续 |
| 声纹服务不可用 | 使用匿名发言人；不影响字幕 |
| speaker worker 慢 | 文本先显示，speaker 结果后补 |
| Redis/通知层不可用 | 从数据库 cursor 轮询或重连恢复，不丢 durable event |
| worker 重启 | 通过 lease 和幂等键恢复任务 |
| stream gateway 重启 | 浏览器重连并重发未 ACK 分片；标记不可恢复缺口 |
| 数据库短暂不可用 | 本地受限缓冲并施加背压；超过阈值安全停录并提示 |
| 音频存储不可写 | 立即进入核心故障，不能只展示字幕后丢失录音 |

### 7.4 新增代码边界

推荐新增文件：

```text
apps/api/routers/meetings.py
apps/api/services/meeting_contracts.py
apps/api/services/meeting_repository.py
apps/api/services/meeting_state_machine.py
apps/api/services/meeting_event_store.py
apps/api/services/meeting_stream_ticket.py
apps/api/services/meeting_asr_adapter.py
apps/api/services/meeting_audio_store.py
apps/api/services/meeting_speaker_service.py
apps/api/services/meeting_correction_feedback.py
apps/api/services/meeting_lexicon_service.py
apps/api/services/meeting_voiceprint_service.py
apps/api/services/meeting_model_catalog.py
apps/api/services/meeting_hermes_runner.py
apps/api/services/meeting_postprocess.py
apps/api/services/meeting_export.py
apps/api/services/meeting_retention.py
apps/api/services/meeting_worker.py
apps/api/migrations/002_create_meeting_tables.sql

apps/web/src/pages/Meetings.tsx
apps/web/src/pages/MeetingCreate.tsx
apps/web/src/pages/MeetingLive.tsx
apps/web/src/pages/MeetingDetail.tsx
apps/web/src/pages/MeetingLexicon.tsx
apps/web/src/pages/MeetingVoiceprints.tsx
apps/web/src/features/meeting-transcription/api.ts
apps/web/src/features/meeting-transcription/types.ts
apps/web/src/features/meeting-transcription/meetingReducer.ts
apps/web/src/features/meeting-transcription/useMeetingCapture.ts
apps/web/src/features/meeting-transcription/useMeetingStream.ts
apps/web/src/features/meeting-transcription/useMeetingEvents.ts
apps/web/src/features/meeting-transcription/MeetingList.tsx
apps/web/src/features/meeting-transcription/LiveMeetingWorkbench.tsx
apps/web/src/features/meeting-transcription/TranscriptTimeline.tsx
apps/web/src/features/meeting-transcription/SpeakerPanel.tsx
apps/web/src/features/meeting-transcription/LiveMinutesPanel.tsx
apps/web/src/features/meeting-transcription/MeetingAudioPlayer.tsx
apps/web/src/features/meeting-transcription/TranscriptCorrectionEditor.tsx
apps/web/src/features/meeting-transcription/LexiconWorkbench.tsx
apps/web/src/features/meeting-transcription/VoiceprintConsentDialog.tsx
apps/web/src/features/meeting-transcription/ModelSelector.tsx
apps/web/src/styles/meeting-transcription.css

agents/hermes/profiles/siq_meeting/
infra/model-services/meeting-speech/
infra/systemd-user/siq-meeting-stream.service
infra/systemd-user/siq-meeting-worker.service
```

具体文件可在实现时按仓库模式微调，但必须保持会议领域独立，不把核心逻辑塞入现有 chat、IC meeting 或全局模型控制文件。

## 8. 数据模型与存储合同

### 8.1 总体原则

- 所有会议表均为新增表，不修改已有表。
- 数据库为会议状态、stable transcript、事件 cursor、任务和授权的权威来源。
- 音频文件存储保存二进制，数据库只保存受控引用、校验和和 manifest。
- partial 字幕默认不持久化；stable segment 必须持久化。
- 文本修订采用追加 revision，不覆盖原始 ASR。
- 发言人重命名采用 track alias，不批量破坏性改写 segment。
- 所有时间使用 UTC；音频偏移使用整数毫秒。
- ID 使用 UUID 或项目统一不可预测 ID。
- JSON 字段必须有 schema version，并保持 SQLite/PostgreSQL 测试兼容。

### 8.2 建议新增表

#### `meeting_sessions`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | UUID/string PK | 会议 ID |
| `owner_user_id` | FK/index | 创建人 |
| `title` | string | 标题 |
| `language` | string | 识别语言 |
| `state` | enum/string | 采集状态 |
| `postprocess_state` | enum/string | 会后处理状态，与采集状态独立 |
| `audio_source` | string | microphone/tab/system/import |
| `voiceprint_enabled` | bool | 本场是否使用已授权声纹 |
| `ai_enabled` | bool | 是否启用 AI 整理 |
| `selection_mode` | string | none/auto/pinned |
| `requested_model_ref` | nullable string | 用户选择的不透明模型引用 |
| `fallback_policy` | string | disabled/local_only/explicit_policy |
| `settings_version` | integer | 模型与功能设置乐观锁版本 |
| `stream_epoch` | integer | 当前采集 epoch |
| `last_audio_sequence` | bigint | 最后确认音频序号 |
| `last_segment_ordinal` | bigint | 最后 stable segment 序号 |
| `started_at` | timestamp | 开始时间 |
| `stopped_at` | timestamp | 停止时间 |
| `created_at` | timestamp | 创建时间 |
| `updated_at` | timestamp | 更新时间 |

约束：

- `owner_user_id + created_at` 索引。
- `settings_version >= 1`。
- `stopped_at >= started_at`，字段均存在时成立。
- 非 AI 模式下 `selection_mode=none` 且 `requested_model_ref IS NULL`。

#### `meeting_stream_leases`

| 字段 | 说明 |
| --- | --- |
| `meeting_id` | 每场会议唯一 |
| `stream_epoch` | 重连递增 epoch |
| `connection_id` | 当前连接 ID |
| `owner_user_id` | 音频生产者 |
| `lease_until` | 租约到期时间 |
| `last_acked_sequence` | 最高连续 ACK |
| `updated_at` | 心跳时间 |

每场会议只允许一个有效采集生产者；观察者只能订阅事件，不能上传音频。

#### `meeting_audio_chunks`

| 字段 | 说明 |
| --- | --- |
| `meeting_id` | 会议 ID |
| `stream_epoch` | 采集 epoch |
| `sequence` | 单调序号 |
| `start_ms` / `duration_ms` | 时间轴 |
| `storage_key` | 受控存储引用 |
| `sha256` | 内容校验 |
| `byte_size` | 大小 |
| `codec` / `sample_rate` / `channels` | 音频格式 |
| `state` | received/verified/packed/deleted |
| `created_at` | 接收时间 |

唯一约束：`(meeting_id, stream_epoch, sequence)`。重复上传相同校验和幂等成功，不同校验和返回冲突并写安全审计。

#### `meeting_speaker_tracks`

| 字段 | 说明 |
| --- | --- |
| `id` | track ID |
| `meeting_id` | 会议 ID |
| `track_key` | ASR/diarization 匿名 key |
| `anonymous_label` | 发言人 N |
| `display_name` | 当前显示名 |
| `label_source` | anonymous/manual/voiceprint_confirmed/voiceprint_auto |
| `voice_profile_id` | 可空的声纹身份 |
| `match_confidence` | 可空，仅用于展示和审计 |
| `version` | 乐观锁 |
| `created_at` / `updated_at` | 时间 |

唯一约束：`(meeting_id, track_key)`。

#### `meeting_transcript_segments`

| 字段 | 说明 |
| --- | --- |
| `id` | segment ID |
| `meeting_id` | 会议 ID |
| `ordinal` | 会议内单调序号 |
| `utterance_id` | partial 到 final 的关联 ID |
| `provider_segment_key` | ASR 幂等键 |
| `start_ms` / `end_ms` | 音频范围 |
| `speaker_track_id` | 可空、可晚到 |
| `raw_text` | 首遍稳定原始文本 |
| `asr_final_text` | 二遍 ASR 确定文本 |
| `normalized_text` | 确定性规范化文本 |
| `asr_confidence` | 可空 |
| `asr_provider` / `asr_model` / `asr_version` | ASR provenance |
| `overlap` / `noise_level` | 质量元数据 |
| `human_locked` | 人工确认锁 |
| `created_at` / `updated_at` | 时间 |

唯一约束：

- `(meeting_id, ordinal)`。
- `(meeting_id, provider_segment_key)`，如果 provider 提供稳定 key。

`raw_text` 和 `asr_final_text` 一经 stable 提交不得通过普通更新覆盖；最终复核产生 revision 或单独 finalization version。

#### `meeting_segment_revisions`

| 字段 | 说明 |
| --- | --- |
| `id` | revision ID |
| `segment_id` | segment |
| `revision_no` | 单 segment 递增版本 |
| `revision_type` | llm_correction/manual/final_asr_review |
| `text` | 修订文本 |
| `base_revision_no` | 乐观锁基础版本 |
| `reason_codes` | 术语、同音字、标点、人工等 |
| `model_snapshot_id` | LLM 修订时关联执行快照 |
| `created_by` | system 或 user |
| `created_at` | 时间 |

唯一约束：`(segment_id, revision_no)`。人工 revision 使 segment 进入 human locked 状态。

#### `meeting_asr_correction_events`

记录用户对 ASR 结果的订正，不与一般文本润色混淆：

| 字段 | 说明 |
| --- | --- |
| `id` | correction event ID |
| `owner_user_id` | 订正所有者 |
| `meeting_id` / `segment_id` | 来源会议和句子 |
| `speaker_track_id` | 可空的本场发言人 |
| `voice_profile_id` | 可空，仅在身份已授权确认时关联 |
| `base_revision_no` / `result_revision_no` | 修改前后 revision |
| `original_text` / `corrected_text` | ASR 原文与人工订正 |
| `diff_ops_json` | 版本化最小 diff |
| `edit_intent` | asr_error/content_edit/unknown |
| `error_class` | lexical/entity/punctuation/itn/deletion/insertion/rewrite |
| `contribute_to_accuracy` | 用户是否允许进入改进流程 |
| `asr_provider` / `asr_model` / `asr_version` | 产生原文的识别版本 |
| `hotword_version` | 当时使用的词库版本 |
| `audio_start_ms` / `audio_end_ms` | 证据范围，不复制音频 |
| `status` | active/reverted/excluded/promoted |
| `created_by` / `created_at` / `updated_at` | 审计 |

要求：

- 一般润色仍创建 segment revision，但 `contribute_to_accuracy=false`。
- `original_text/corrected_text` 属于会议敏感文本，权限和保留策略与 transcript 相同。
- 事件不得直接修改生产词库，必须经过候选/发布流程。

#### `meeting_term_candidates`

从订正事件派生的识别改进候选：

| 字段 | 说明 |
| --- | --- |
| `id` | candidate ID |
| `owner_user_id` | 个人作用域 |
| `canonical_term` | 正确术语 |
| `misrecognition` | 常见误识别写法，可空 |
| `language` | 语言 |
| `candidate_type` | hotword/confusion_pair/context_term |
| `source_count` / `distinct_meeting_count` | 支持证据数 |
| `confirmed_count` / `reverted_count` | 用户确认与撤销统计 |
| `speaker_specific_candidate` | 可空的 voice profile 统计，不直接激活 |
| `confidence` | 候选置信度 |
| `status` | pending/confirmed/rejected/promoted/deprecated |
| `created_at` / `updated_at` | 时间 |

同一用户、语言、canonical term 和误识别组合需要可幂等合并，但保留来源 correction event 关联表。

#### `meeting_lexicon_entries`

个人有效术语库：

- `owner_user_id`、`language`。
- `canonical_term`、可选 `aliases/misrecognitions`。
- `entry_type=manual/hotword/confusion_pair`。
- `weight`，由服务端限制范围。
- `scope=current_meeting/user_future_meetings`。
- 可空 `speaker_voice_profile_id`；首期默认不启用 speaker-specific 自动规则。
- `status=active/paused/deprecated/deleted`。
- `source_candidate_id`、`created_by`、`created_at`、`updated_at`。

唯一性和冲突规则必须避免同一误识别写法同时映射到多个正确词时自动生效；歧义项只可作为上下文候选或人工确认。

#### `meeting_lexicon_versions`

每次词库发布生成不可变版本：

- `owner_user_id`、`version`、`language`。
- `entries_hash`、`entry_count`。
- `change_reason`、`created_by`、`created_at`。
- `supersedes_version`。

会议 session 和 ASR segment 必须记录使用的 lexicon version，便于对比修改前后的准确度和快速回滚。

#### `meeting_voice_profiles`

首期建议用户私有：

| 字段 | 说明 |
| --- | --- |
| `id` | 不可变 voice profile ID |
| `owner_user_id` | 所有人 |
| `display_name` | 用户可编辑显示名 |
| `scope` | MVP 固定 user_private |
| `status` | collecting/active/paused/revoked/deleted |
| `encoder_name` / `encoder_version` | embedding 版本 |
| `encrypted_embedding` | 应用层加密密文 |
| `key_id` | 加密密钥版本引用 |
| `sample_count` / `effective_duration_ms` | 质量统计 |
| `quality_summary` | 不含原始音频的质量摘要 |
| `created_at` / `updated_at` / `deleted_at` | 时间 |

严禁把明文 embedding 放入日志、API 响应、Milvus 共享 collection 或前端存储。

#### `meeting_voiceprint_consents`

授权记录采用追加事件：

| 字段 | 说明 |
| --- | --- |
| `id` | consent ID |
| `voice_profile_id` | 声纹身份 |
| `actor_user_id` | 操作人 |
| `subject_label` | 当时显示名，不作为唯一身份 |
| `purpose` | future_meeting_speaker_identification |
| `scope` | user_private |
| `policy_version` | 用户确认的政策版本 |
| `source_meeting_id` | 来源会议 |
| `granted_at` / `revoked_at` | 授权与撤销时间 |
| `metadata` | 不含音频/embedding 的审计信息 |

重命名本身不得自动写入 granted consent。

#### `meeting_voiceprint_matches`

保存候选与决策：

- `meeting_id`、`speaker_track_id`、`voice_profile_id`。
- `encoder_version`、`threshold_version`。
- Top-1 分数、Top-1/Top-2 差值、有效时长、质量等级。
- `decision=suggested/auto_applied/confirmed/rejected/undone`。
- `decided_by`、`created_at`、`decided_at`。

拒绝候选只影响本场匹配，不自动删除声纹。

#### `meeting_model_settings`

每次模型选择变更保存版本：

- `meeting_id`、`settings_version`。
- `selection_mode`、`requested_model_ref`、`fallback_policy`。
- `effective_after_segment_ordinal`。
- `cloud_data_boundary_confirmed_at`。
- `changed_by`、`created_at`。

#### `meeting_model_snapshots`

每个 AI job 创建时保存不可变快照：

```json
{
  "model_ref": "opaque-model-ref",
  "selection_mode": "pinned",
  "resolved_provider": "provider-id",
  "resolved_model": "actual-model",
  "provider_locality": "local",
  "hermes_target": "opaque-target",
  "meeting_profile_version": "v1",
  "prompt_version": "v1",
  "schema_version": "v1",
  "settings_version": 3,
  "effective_after_segment_ordinal": 182,
  "resolved_at": "2026-07-13T08:00:00Z"
}
```

Job 执行时不得重新读取“当前模型”替换该快照。

#### `meeting_artifacts`

| 字段 | 说明 |
| --- | --- |
| `id` | artifact ID |
| `meeting_id` | 会议 ID |
| `artifact_type` | 统一纪要使用 rolling_minutes/final_minutes；处理链另有 final_transcript_alignment/speaker_recluster/export |
| `version` | 同类递增版本 |
| `state` | generating/ready/failed/stale |
| `content_json` / `content_text` | 结构化和展示内容 |
| `input_from_ordinal` / `input_to_ordinal` | 输入范围 |
| `transcript_revision` | 输入逐字稿版本 |
| `model_snapshot_id` | 执行模型 |
| `supersedes_id` | 可空的上一版本 |
| `created_at` / `updated_at` | 时间 |

统一纪要 `content_json` 包含 overview、agenda_topics、chapters、decisions、open_questions、risks、action_items、speaker_viewpoints 和 keywords。除 overview 外，每个条目必须包含 `source_segment_ids`。

#### `meeting_jobs`

新增会议专属任务表，至少支持：

- `job_kind=correction/rolling_minutes/final_transcript/speaker_recluster/final_minutes/voiceprint_enroll/export`。
- `idempotency_key` 唯一。
- `state=queued/leased/running/retry_wait/succeeded/failed/cancelled`。
- `attempt`、`max_attempts`、`lease_owner`、`lease_until`。
- `input_watermark`、`settings_version`、`model_snapshot_id`。
- 可公开错误码和脱敏内部诊断分离。

#### `meeting_events`

作为 durable event/outbox：

- `meeting_id`。
- 单调 `cursor`。
- 全局 `event_id`。
- `event_type`、`schema_version`、`payload_json`。
- `trace_id`、`created_at`、`published_at`。

唯一约束：`(meeting_id, cursor)` 和 `event_id`。stable segment 与对应事件必须同事务提交。

### 8.3 音频存储布局

推荐根目录：

```text
${SIQ_MEETING_AUDIO_ROOT}/<owner_user_id>/<meeting_id>/
  manifest.json
  chunks/<stream_epoch>/<sequence>.pcm.enc
  audio/meeting.flac
  audio/meeting.m4a
  exports/transcript.srt
  exports/transcript.vtt
  exports/minutes.md
  temp/voiceprint/<job_id>/...
```

要求：

- 路径由服务端根据 ID 构造，客户端不能提交任意路径。
- chunk 原子落盘后再 ACK。
- manifest 保存格式、时长、chunk 范围、校验和和缺口。
- 原始会议音频按保留策略保存并支持 HTTP Range 回放。
- 临时声纹切片使用独立短 TTL，任务完成后删除。
- 音频下载和 Range 请求始终经过对象级鉴权，不通过公共静态目录暴露。
- 用户删除会议时进入可恢复删除任务，删除数据库引用、音频和导出文件，并保留不含正文的审计证明。

### 8.4 数据保留建议

默认值必须可配置并在 UI 显示：

- 会议音频：按用户/组织策略保留，默认建议 90 天而不是永久。
- 逐字稿和纪要：随会议保存，用户可删除。
- 临时未 ACK 浏览器分片：ACK 后尽快清理，最长不超过短期恢复窗口。
- 临时声纹切片：注册完成或失败后立即删除，最长 TTL 小时级。
- 声纹 embedding：授权有效期内保存；撤销/删除后停止使用并执行清理。
- 审计事件：不含正文、音频和 embedding，按安全政策保留。

具体生产保留期上线前由产品、运维和隐私负责人共同确认。

## 9. API 合同

### 9.1 命名与版本

新 API 统一使用：

```text
/api/meetings/v1
```

不得占用或代理 `/api/chat/*` 和 `/api/primary-market/meeting/*`。

所有写接口：

- 依赖现有用户认证。
- 执行对象级 owner/ACL 检查。
- 支持 `Idempotency-Key`。
- 并发更新使用 `expected_version` 或 `If-Match`。
- 跨用户访问返回 404，避免泄露对象存在性。
- 返回稳定业务错误码，不把内部模型地址和异常堆栈暴露给前端。

### 9.2 能力与模型

#### `GET /api/meetings/v1/capabilities`

返回：

- `enabled`。
- 浏览器所需音频格式。
- ASR 可用性、语言、时间戳和 speaker 能力。
- 声纹功能是否开放。
- AI 整理功能是否开放。
- 最大会议时长、chunk 限制和重连窗口。
- 后续音频源支持情况。

#### `GET /api/meetings/v1/models?purpose=meeting_postprocess`

模型项示例：

```json
{
  "model_ref": "opaque-model-ref",
  "label": "用户可读名称",
  "provider_label": "provider",
  "locality": "local",
  "configured": true,
  "available": true,
  "capabilities": ["text", "structured_json", "long_context"],
  "context_window": 200000,
  "data_boundary": "local",
  "reason_code": null,
  "checked_at": "2026-07-13T08:00:00Z"
}
```

响应不得包含 API key、Authorization、敏感 Base URL 或 fallback 凭证。

### 9.3 会议生命周期

| Method | Path | 作用 |
| --- | --- | --- |
| `POST` | `/sessions` | 创建会议 |
| `GET` | `/sessions` | 分页列出本人会议 |
| `GET` | `/sessions/{id}` | 获取会议摘要和权限内状态 |
| `PATCH` | `/sessions/{id}` | 更新标题等普通元数据 |
| `POST` | `/sessions/{id}/start` | 幂等开始会议 |
| `POST` | `/sessions/{id}/pause` | 幂等暂停并记录时间轴缺口 |
| `POST` | `/sessions/{id}/resume` | 恢复并创建新 epoch |
| `POST` | `/sessions/{id}/stop` | 幂等停止采集并 flush |
| `POST` | `/sessions/{id}/finalize` | 幂等触发会后任务 |
| `DELETE` | `/sessions/{id}` | 请求删除会议和关联数据 |

创建请求示例：

```json
{
  "title": "周例会",
  "language": "zh-CN",
  "audio_source": "microphone",
  "voiceprint_enabled": false,
  "ai_enabled": true,
  "model_selection": {
    "mode": "pinned",
    "model_ref": "opaque-model-ref",
    "fallback_policy": "disabled"
  }
}
```

### 9.4 流连接与事件

| Method | Path | 作用 |
| --- | --- | --- |
| `POST` | `/sessions/{id}/stream-ticket` | 创建一次性短期 WS ticket |
| `WS` | `/sessions/{id}/audio?ticket=...` | 上传音频并接收实时事件 |
| `GET` | `/sessions/{id}/events?after_cursor=` | 重连回放 durable event |
| `GET` | `/sessions/{id}/events/stream?after_cursor=` | 可选 SSE 观察者连接 |
| `GET` | `/sessions/{id}/transcript?after_ordinal=&at_ms=&limit=` | 按序分页或按播放时间读取有界稳定逐字稿窗口 |

### 9.5 逐字稿与说话人

| Method | Path | 作用 |
| --- | --- | --- |
| `GET` | `/sessions/{id}/speakers` | 获取 speaker track |
| `PATCH` | `/sessions/{id}/speakers/{trackId}` | 仅本次重命名 |
| `PATCH` | `/sessions/{id}/segments/{segmentId}/speaker` | 单段或同 track 全量改名 |
| `PATCH` | `/sessions/{id}/segments/{segmentId}` | 人工修订并锁定 |
| `POST` | `/sessions/{id}/segments/{segmentId}/revert` | 撤销到指定 revision |
| `POST` | `/sessions/{id}/speakers/{trackId}/merge` | 会后人工合并 track |
| `POST` | `/sessions/{id}/speakers/{trackId}/split` | 会后受控拆分错误 track |

人工重命名接口只更新 alias 解析，不隐式注册声纹。

人工修订 segment 的请求示例：

```json
{
  "text": "人工订正后的文字",
  "expected_revision": 2,
  "edit_intent": "asr_error",
  "contribute_to_accuracy": true,
  "candidate_terms": [
    {
      "canonical_term": "正确专有名词",
      "misrecognition": "原误识别词",
      "promote_now": false
    }
  ]
}
```

服务端必须自行计算 diff 和 error class，不能完全信任前端提交的候选范围。

### 9.6 订正反馈与个人术语

| Method | Path | 作用 |
| --- | --- | --- |
| `GET` | `/correction-feedback?status=&meeting_id=` | 查看本人订正历史 |
| `GET` | `/correction-feedback/{id}` | 查看订正、diff 和使用状态 |
| `POST` | `/correction-feedback/{id}/exclude` | 排除出识别改进流程 |
| `POST` | `/correction-feedback/{id}/restore` | 在仍有权限和授权时恢复候选资格 |
| `GET` | `/term-candidates?status=` | 查看个人术语候选 |
| `POST` | `/term-candidates/{id}/confirm` | 确认候选并进入下一词库版本 |
| `POST` | `/term-candidates/{id}/reject` | 拒绝候选 |
| `GET` | `/lexicon` | 获取本人 active 词库和版本 |
| `POST` | `/lexicon` | 手工新增个人术语 |
| `PATCH` | `/lexicon/{entryId}` | 修改权重、作用域或状态 |
| `DELETE` | `/lexicon/{entryId}` | 删除并发布新词库版本 |
| `GET` | `/lexicon/versions` | 查看版本历史 |
| `POST` | `/lexicon/versions/{version}/activate` | 回滚/激活一个已有词库版本 |

API 规则：

- 订正保存和 feedback 事件写入同一事务，避免 revision 已保存但反馈丢失。
- 词库发布原子生成不可变 version。
- 当前会议增量热词更新返回 `effective_after_audio_sequence` 或 `effective_after_segment_ordinal`。
- 删除/停用术语不改写历史 transcript，只影响未来识别。
- 作用域首期仅支持 current meeting 和 owner private future meetings。
- 跨用户访问统一 404。

### 9.7 声纹

| Method | Path | 作用 |
| --- | --- | --- |
| `GET` | `/voiceprints` | 获取本人声纹列表 |
| `POST` | `/voiceprints` | 创建 collecting 身份 |
| `POST` | `/sessions/{id}/speakers/{trackId}/voiceprint-enrollment` | 明确授权后注册 |
| `POST` | `/sessions/{id}/voiceprint-matches/{matchId}/decision` | confirm/reject/undo |
| `POST` | `/voiceprints/{id}/pause` | 暂停未来识别 |
| `POST` | `/voiceprints/{id}/resume` | 恢复有效授权范围内识别 |
| `POST` | `/voiceprints/{id}/re-enroll` | 重新采集 |
| `POST` | `/voiceprints/{id}/consent/revoke` | 撤销授权 |
| `DELETE` | `/voiceprints/{id}` | 删除声纹及模板 |

enrollment 请求必须包含：

- `consent_accepted=true`。
- `policy_version`。
- `identity/voice_profile_id`。
- `source_track_id`。
- 幂等键。

### 9.8 模型与产物

| Method | Path | 作用 |
| --- | --- | --- |
| `PUT` | `/sessions/{id}/model-selection` | 切换未来 AI 任务模型 |
| `GET` | `/sessions/{id}/artifacts` | 获取纪要和产物版本 |
| `GET` | `/sessions/{id}/artifacts/{artifactId}` | 获取单个产物 |
| `POST` | `/sessions/{id}/artifacts/{artifactId}/regenerate` | 用指定当前模型创建新版本 |
| `GET` | `/sessions/{id}/jobs` | 查看后处理任务 |
| `POST` | `/sessions/{id}/jobs/{jobId}/retry` | 重试允许重试的步骤 |

模型切换请求包含 `expected_settings_version`；版本冲突返回 409 和当前设置快照。

### 9.9 音频与导出

| Method | Path | 作用 |
| --- | --- | --- |
| `GET` | `/sessions/{id}/audio` | 鉴权 Range 回放 |
| `GET` | `/sessions/{id}/audio/manifest` | 缺口、时长和格式摘要 |
| `POST` | `/sessions/{id}/exports` | 创建导出任务 |
| `GET` | `/sessions/{id}/exports/{exportId}` | 获取导出状态/文件 |

Range 响应、缓存和下载文件名必须遵循现有安全工具，禁止公开真实服务器路径。

## 10. 实时 WebSocket 和事件合同

### 10.1 连接安全

1. 浏览器先通过已认证 REST 请求获取一次性 ticket。
2. ticket 绑定用户、会议、用途、Origin、过期时间和随机 nonce。
3. ticket 默认仅可使用一次，禁止重放。
4. WebSocket 校验 Origin、会议状态、owner/ACL 和活动 lease。
5. 不把长期 JWT 或 Hermes token放入 URL。
6. 限制单帧大小、采样率、发送速率、会话时长和并发连接。

### 10.2 客户端控制消息

```json
{
  "type": "stream.start",
  "schema_version": "siq.meeting.stream.v1",
  "client_stream_id": "uuid",
  "stream_epoch": 1,
  "audio": {
    "encoding": "pcm_s16le",
    "sample_rate": 16000,
    "channels": 1,
    "chunk_ms": 500
  },
  "last_server_cursor": 107
}
```

其他控制消息：

- `stream.pause`。
- `stream.resume`。
- `stream.stop`。
- `stream.heartbeat`。
- `stream.resume_request`，携带最后已 ACK sequence。

### 10.3 音频二进制帧

二进制帧使用版本化固定头，不使用 base64 JSON。头部至少包含：

- magic/version。
- `stream_epoch`。
- `sequence`。
- `capture_time_ms`。
- flags。
- PCM16LE payload。

服务端对重复 sequence 幂等去重，对序号缺口发送 `audio.gap.detected` 或重发请求。不得用无界内存等待永久缺失的分片。

### 10.4 服务端事件信封

```json
{
  "schema_version": "siq.meeting.event.v1",
  "event_id": "evt_uuid",
  "meeting_id": "meeting_uuid",
  "type": "transcript.segment.stable",
  "cursor": 108,
  "emitted_at": "2026-07-13T08:00:00Z",
  "trace_id": "trace_uuid",
  "payload": {}
}
```

- durable event 有单调 cursor，可从数据库回放。
- `transcript.partial` 为易失事件，`cursor=null`，不进入最终证据链。
- 前端按 `event_id` 去重，按 cursor 应用 durable event。
- 重连只回放 durable event，不回放历史 partial。

### 10.5 必须支持的事件

| 事件 | 说明 |
| --- | --- |
| `session.state.changed` | 会议状态变化 |
| `stream.ready` | 接收音频前握手完成 |
| `stream.heartbeat` | 连接健康 |
| `audio.ack` | 最高连续 ACK |
| `audio.gap.detected` | 音频序号缺口 |
| `flow.control` | 背压、暂停或建议 chunk 速度 |
| `transcript.partial` | 可替换草稿 |
| `transcript.segment.stable` | durable ASR final |
| `transcript.segment.corrected` | LLM/最终复核 revision |
| `transcript.segment.human_edited` | 用户保存人工 revision |
| `asr.feedback.recorded` | 识别订正反馈已记录 |
| `lexicon.candidate.created` | 产生个人术语候选 |
| `lexicon.version.activated` | 新词库版本生效及边界 |
| `speaker.track.created` | 新匿名 speaker |
| `speaker.track.merged` | 会后 track 合并映射 |
| `speaker.label.changed` | 人工或声纹显示名变化 |
| `voiceprint.match.suggested` | 中置信候选 |
| `voiceprint.match.applied` | 自动/人工确认应用 |
| `voiceprint.match.rejected` | 用户拒绝 |
| `voiceprint.match.undone` | 撤销自动命名 |
| `model.selection.changed` | 模型设置边界变化 |
| `minutes.rolling.updated` | 实时纪要新版本 |
| `minutes.final.ready` | 最终纪要完成 |
| `pipeline.degraded` | 可选组件降级 |
| `pipeline.recovered` | 可选组件恢复 |
| `error` | 作用域明确的错误 |

错误 payload 必须区分 `stream/asr/storage/speaker/voiceprint/postprocess/model` 作用域。`MODEL_UNAVAILABLE` 不得把 ASR 状态改为 offline。

## 11. 实时 ASR、时间戳与说话人技术方案

### 11.1 首期 ASR 方案

优先部署独立 FunASR 2pass 服务：

- 流式在线模型产生 0.5-1 秒级 partial。
- VAD 检测句尾。
- 非流式高精度模型在句尾产生 final，并补标点和时间戳。
- 会议结束后按 VAD/固定窗口重新执行最终复核。

不允许直接将当前 8899 `/ws` 宣称为目标完成，因为它没有句中 partial 且 speaker 只在 STOP 后返回。

### 11.2 资源策略

当前本机模型资源紧张，不能无评测再启动第二套 Fun-ASR-Nano vLLM。P0 预研优先：

1. CPU Paraformer streaming 作为实时首遍。
2. 复用现有 8899 的高精度能力做句尾或会后分块复核，但通过隔离 adapter 和有界队列调用，不修改其现有契约。
3. 若共享 GPU，ASR 优先级高于 Hermes AI 任务。
4. AI correction 和 rolling minutes 使用独立低优先级队列和严格并发限制。

### 11.3 时间戳

- stable segment 必须有 `start_ms/end_ms`。
- 支持模型提供的词级时间戳时保存 word timing；不支持时至少保存句级。
- 音频 chunk、ASR、最终封装音频使用同一会议单调时间轴。
- 暂停产生明确 gap，不压缩时间轴。
- 会后重新对齐产生 revision，不直接覆盖原始实时时间戳。

### 11.4 热词与上下文

会议可配置：

- 参会人姓名。
- 公司、项目、产品和金融术语。
- 用户自定义热词。

热词只作为 ASR bias 和 LLM 修订词表，不得被当作已确认事实。热词变更需要版本和生效 segment 边界。

### 11.5 说话人分离

说话人分离先产生匿名 track：

```text
SPK0 -> 发言人 1
SPK1 -> 发言人 2
```

实时阶段允许 speaker 结果延迟数秒，并允许后续 track merge patch。会后使用更完整上下文重聚类。

必须处理：

- 短语音无法可靠分离。
- 多人同时发言。
- 同一人远近位置或设备变化。
- 会议中途新加入人员。
- 重聚类导致 track 合并/拆分。

前端不得假设 `SPK0` 永久等于某姓名；姓名通过 alias/identity 映射解析。

### 11.6 人工订正驱动的识别改进

该能力可以提升后续识别，但本质是“版本化反馈与词库闭环”，不是每次保存后立即训练 ASR 参数。

#### 反馈采集

保存人工 revision 时，服务端记录：

- ASR 原文和人工订正文。
- 最小 diff，而不是只保存最终整句。
- 用户声明的编辑意图。
- segment、时间戳、speaker track 和可选已授权 voice profile。
- ASR provider/model/version、当时词库版本和 confidence。
- 反馈是否允许用于后续准确度提升。

#### 反馈分类

分类器首先使用确定性 diff 和规则，必要时可异步使用 LLM 辅助分类，但 LLM 结论不能直接发布词库：

```text
punctuation     标点变化，不进入热词
itn             数字/日期显示格式，进入规范化规则评估
lexical         普通错词/漏词，进入候选
entity          专有名词订正，优先热词候选
content_edit    表述润色或事实修改，不用于 ASR
ambiguous       无法确定，等待人工确认
```

#### 候选晋升

候选进入 active 词库至少满足一种条件：

- 用户明确点击“加入个人术语”。
- 同一用户在多个独立 segment 中重复确认相同映射，达到配置门槛。
- 管理页面人工确认。

以下情况不得自动晋升：

- 同一错误写法映射到多个正确词。
- 只有标点、空格或大段重写。
- 原 segment 无可靠音频范围或 ASR provenance。
- 反馈已撤销、存在冲突或用户关闭贡献设置。
- 候选包含超出长度、控制字符或不允许的敏感模式。

#### 生效方式

词库发布后生成不可变 version，并按 ASR 能力选择：

1. 追加 canonical term 到 FunASR hotwords，并使用服务端限制的权重。
2. 把高精度 confusion pair 用于受控上下文或 deterministic post-correction。
3. 把术语表加入 Hermes correction glossary。
4. 会议正在进行且 ASR 支持热更新时，从明确的 sequence/ordinal 边界生效；不支持则从下一场会议生效。

不得自动重写已完成历史逐字稿。系统可查找相似出现并提供批量复核列表，由用户逐项或范围确认。

#### Speaker-specific 学习

如果 speaker 已由用户确认且关联有效 voice profile，可以统计该发言人的特定易错词，但首期仅用于分析和建议。只有积累足量独立样本、通过单独评测并取得相应授权后，才允许启用 speaker-specific context；不得把一次修改永久绑定到某个人。

#### 离线模型训练

长期可以使用订正事件构建 ASR 评测或微调数据，但必须另立流程：

- 用户/组织明确允许用于模型改进。
- 去除无关个人信息并执行数据最小化。
- 人工审核 ground truth。
- 划分训练、验证和留出测试集。
- 新模型先离线评测和 shadow，不直接替换生产 ASR。
- 发布有模型版本、灰度、回滚和前后 CER/实体准确率对比。

本任务首期交付止于 feedback、候选、个人词库、版本和热词/上下文应用，不包含自动训练生产模型。

## 12. 跨会议声纹识别方案

### 12.1 能力边界

声纹只用于会议说话人标签，不得用于：

- 登录认证。
- 权限判断。
- 身份证实或法律身份结论。
- 未授权的全库人员搜索。
- 让 LLM 根据文本猜姓名。

### 12.2 注册

注册条件：

- 用户明确选择“保存声纹，未来自动识别”。
- 保存不可变 consent 记录。
- 来源 speaker track 有多个清晰片段和足够有效时长。
- 片段无明显重叠说话和严重噪声。
- encoder/version 固定并记录。
- embedding 加密后再持久化。

未经授权产生的临时 speaker embedding 只用于本场聚类或短期内存匹配，不得长期保存。

### 12.3 匹配与阈值

不能只依赖一个余弦分数。匹配至少综合：

- Top-1 分数。
- Top-1 与 Top-2 差值。
- 有效语音时长。
- 信噪比和重叠率。
- 采集设备/声道差异。
- encoder 和阈值版本。

结果等级：

| 等级 | 行为 |
| --- | --- |
| `auto_match` | 高置信自动命名，显示声纹来源，可撤销 |
| `suggestion` | 显示“可能是张三”，用户确认后应用 |
| `unknown` | 保持匿名，不显示误导性实名 |

具体阈值不得凭经验硬编码，必须使用独立验证集标定。未达到低误接受率门槛时，产品只能开放 suggestion 模式。

### 12.4 用户决策

- `confirm`：本场 track 映射到该身份，记录人工确认。
- `reject`：本场不再重复提示同一候选，记录负反馈。
- `undo`：撤销自动应用，恢复之前显示名。
- `manual rename`：人工命名优先，不自动建立新 voice profile。
- `re-enroll`：明确授权后增加或替换样本版本。
- `revoke`：立即停止未来匹配。
- `delete`：删除加密模板和临时副本。

撤销/删除声纹默认不改写历史会议中已经人工确认的姓名。历史匿名化应由用户单独发起并明确范围。

### 12.5 安全和隔离

- MVP 声纹库限定用户私有，避免在当前无成熟 tenant contract 时伪造组织共享。
- 用户 A 不能枚举、匹配或读取用户 B 的 voice profile。
- 管理员默认只能查看运行状态和审计元数据，不能读取 embedding。
- encryption key 不与数据库同库保存，支持 `key_id` 和轮换。
- 备份恢复后不得让已删除声纹“复活”；删除流程必须纳入恢复演练。

## 13. Hermes 动态模型选择与会议 Runner

### 13.1 两层职责

会议 AI 必须拆分为：

```text
siq_meeting workflow
  = 会议任务提示词、JSON Schema、权限、工具和安全规则

model_ref
  = 用户选择的 Hermes 可用模型运行目标
```

`siq_meeting` 不绑定 Nemotron 或任何固定模型。模型名称和 provider 仅存在于服务端模型目录和不可变执行快照中。

### 13.2 模型目录来源

模型目录必须从 Hermes 实际配置和运行状态只读生成，至少检查：

- 是否在管理员允许的 meeting model allowlist 中。
- provider 配置是否完整。
- 本地 endpoint 是否健康，或云端凭证是否已配置。
- 对应 Hermes meeting target 是否运行。
- 是否支持文本输入。
- 是否支持或可可靠约束为结构化 JSON 输出。
- 上下文窗口是否满足当前任务。
- 当前是否被限流或停用。
- 数据边界是 local 还是 cloud。

“配置存在”不等于“可用”。前端只允许选择 `available=true` 项，但可禁用展示不可用项及脱敏原因，便于诊断。

### 13.3 Run 级隔离技术预研

P0 必须验证 Hermes gateway 是否支持经过白名单校验的 run-scoped provider/model override：

#### 路径 A：Hermes 原生支持 run-scoped override

- 新增会议专用客户端方法，不改变现有 `create_run()` 默认行为。
- 请求只传 `model_ref`，服务端解析为 allowlist target。
- 现有调用方不传新字段时行为完全不变。
- 必须证明并发会议使用不同模型互不影响。

#### 路径 B：Hermes 不支持 run-scoped override

- 为每个可用模型创建独立、不可变的会议 Hermes target/gateway。
- target 复用 `siq_meeting` prompt/tool contract，但模型配置相互隔离。
- `model_ref` 映射到 target，不改写共享 YAML。
- 新增或下线模型通过管理员同步动作更新 target 池，不影响运行中会议。

无论选择哪条路径，以下方式均禁止：

- 修改 `siq_assistant` 或其他现有 profile。
- 调用 `set_profile_model_mode()`。
- 排队时读取一个全局“当前模型”，执行时再次解析成不同模型。
- 让会议 A 的模型切换影响会议 B。

### 13.4 选择和 fallback 语义

支持三种模式：

| 模式 | 行为 |
| --- | --- |
| `none` | 不运行 AI 整理，只保存音频和逐字稿 |
| `pinned` | 固定用户选择的模型；不可用时暂停 AI，不静默 fallback |
| `auto` | 按服务端公开策略选择；每次实际模型仍需记录 |

建议默认 `pinned` 或 `none`。只有产品明确提供“自动选择”并展示策略时才开放 `auto`。

fallback 规则：

- `pinned` 默认 `disabled`。
- 本地模型禁止静默降级到云端。
- `auto + local_only` 只能在本地模型间降级。
- 任何允许 cloud 的策略必须有用户本场确认和审计记录。
- 实际模型与请求模型不一致时 UI 必须可见。
- 模型不可用不影响 ASR、录音、时间戳和说话人分离。

### 13.5 Job 执行快照

每个 correction、rolling minutes 和 final minutes job 创建时：

1. 读取 meeting model settings 的确定版本。
2. 解析 model_ref 到可用 runtime target。
3. 保存 `meeting_model_snapshot`。
4. 创建幂等键：`meeting_id + job_kind + input_range + settings_version + prompt_version`。
5. worker 只使用快照执行。
6. 产物关联 snapshot。

模型在排队后被管理员停用时：

- pinned job 标记 `MODEL_TARGET_UNAVAILABLE`，不自动换模型。
- auto job 仅按快照中允许的策略重新解析一次，并记录实际 target。
- 不得重新使用会议当前设置覆盖历史 job 的选择。

### 13.6 `siq_meeting` 权限

会议 profile 仅允许完成文本处理：

- 禁止 terminal、shell、任意文件访问和代码执行。
- 默认禁止网络搜索和外部工具。
- 逐字稿视为不可信输入；其中的“忽略规则”“执行命令”等内容不得改变 system contract。
- 只允许输出约定 JSON Schema。
- 不接收音频、声纹向量、云端凭证或服务器路径。
- 输入中的 speaker identity 可在云端调用前按策略替换为 `SPEAKER_01`，返回后本地回填。

## 14. LLM 修订和智能纪要合同

### 14.1 受控文本修订

LLM correction 只消费 stable segment，按 3-5 句或 15-30 秒窗口去抖执行。

输入包括：

- segment ID、revision 和时间戳。
- stable text。
- 相邻有限上下文。
- 用户提供的术语表、参会人名称和项目词表。
- 不可修改字段列表。

输出必须是结构化 patch，而不是完整自由改写文章：

```json
{
  "schema_version": "siq.meeting.correction.v1",
  "patches": [
    {
      "segment_id": "segment-id",
      "base_revision": 1,
      "original": "原文本",
      "replacement": "建议文本",
      "reason_code": "term_correction",
      "confidence": 0.92
    }
  ],
  "review_flags": []
}
```

强制规则：

- 最小必要修改。
- 禁止添加原音频未表达的新事实。
- 金额、数字、日期、比例、证券代码、法律名称默认不自动修改。
- 关键实体有歧义时只添加 `review_flag`。
- patch 的 `original` 必须与 base revision 对应范围匹配，否则拒绝应用。
- 人工锁定 segment 不自动应用任何 LLM patch。
- schema 校验失败时丢弃输出并保留 stable ASR。
- UI 支持查看 diff 和撤销。

### 14.2 实时纪要

触发条件采用去抖而不是每句调用：

- 距上次成功更新 30-60 秒，且存在足够的新 stable 文本；或
- 议题边界、明确决定或用户手动刷新；或
- 队列压力允许的批次策略。

队列压力大时优先合并或跳过中间 rolling 版本，不丢弃 stable transcript。UI 显示最近成功更新时间和当前处理模型。

rolling minutes 标记为“临时纪要”，不得当作最终正式记录。

### 14.3 最终纪要

最终纪要只在以下水位都确定后生成：

- 最后音频 sequence 已 ACK 或明确标记缺口。
- ASR flush 完成。
- 最终 transcript revision 完成或明确降级。
- speaker 重聚类完成或明确降级。
- 人工已确认内容被锁定并纳入输入。

最终 JSON 至少包含：

```text
overview
agenda_topics[]
chapters[]
decisions[]
open_questions[]
risks[]
action_items[]
speaker_viewpoints[]
keywords[]
```

每个议题、章节、决定、问题、风险、待办、观点和关键词必须包含 `source_segment_ids`；无证据项不得出现在正式产物中。

### 14.4 产物版本

- 同类 artifact 采用递增版本。
- 新版本通过 `supersedes_id` 关联旧版本。
- 修改逐字稿后，旧 artifact 标记 `stale`，但不删除。
- 重新生成创建新 artifact，不在原记录上覆盖内容。
- 用户选择模型后重新生成时保存新的 model snapshot。
- 导出时明确选择 artifact version 和 transcript display layer。

## 15. 会话、任务和恢复状态机

### 15.1 会议采集状态

```text
draft
  -> connecting
  -> live
  -> paused -> live
  -> reconnecting -> live
  -> stopping
  -> stopped
  -> archived
```

异常状态：

- `interrupted`：连接或进程异常，但已有数据保留。
- `deleted`：进入删除生命周期。

禁止把 Hermes 或声纹失败映射为会议 `failed`；它们属于独立 pipeline 状态。

### 15.2 Transcript 状态

```text
partial (ephemeral)
  -> stable_asr
  -> normalized
  -> llm_corrected
  -> human_verified
```

- partial 可替换。
- stable_asr 追加后不可变。
- normalized 和 llm_corrected 使用 revision。
- human_verified 具有最高优先级并锁定。

### 15.3 AI 产物状态

```text
queued -> generating -> ready
                    -> failed -> retry_wait -> generating
ready -> stale -> generating(new version)
```

### 15.4 Voiceprint 状态

```text
collecting -> active -> paused -> active
                     -> revoked
                     -> deleted
```

匹配决策：

```text
candidate -> suggested -> confirmed/rejected
          -> auto_applied -> undone/confirmed
```

### 15.5 Worker 和 outbox

会议 worker 必须：

- 使用数据库 lease 原子认领任务。
- lease 超时后允许安全接管。
- 任务有幂等键和最大重试次数。
- 区分 retryable 与 terminal error。
- 结果和 job terminal 同事务或可幂等恢复。
- 进程重启后恢复 queued/retry_wait/过期 leased 任务。
- 不依赖 API 进程 daemon thread。
- 支持优先级：音频/ASR > stable persist > speaker > correction > rolling minutes > exports。

outbox publisher 失败不回滚已提交 transcript；恢复后按 `published_at IS NULL` 继续发布。

## 16. 安全、隐私与权限

### 16.1 权限建议

新增细粒度权限：

```text
meeting.read
meeting.create
meeting.update
meeting.delete
meeting.export
meeting.voiceprint.manage
meeting.admin
```

MVP 默认对象 owner 具有本人会议权限；共享会议另立 ACL 任务，不依赖模糊角色推断。

### 16.2 对象级权限

所有以下资源都必须再次校验 meeting owner/ACL：

- session。
- transcript 和 segment revision。
- speaker track。
- audio Range 和下载。
- artifact、export 和 job。
- voice profile 和 match audit。
- stream ticket 和 WebSocket。

仅有资源 ID 不构成权限。双用户 BOLA 测试是发布阻断项。

### 16.3 音频与声纹

- 音频静态目录不得直接公开。
- 文件名和 storage key 不含真实姓名。
- 声纹模板使用应用层 AEAD 信封加密。
- key 来自独立 secret/KMS，不写入数据库、前端或日志。
- 临时 voiceprint slice 使用短 TTL 和异常清理。
- 撤销和删除任务执行前、结果发布前都再次检查授权状态。
- 声纹删除需覆盖正常存储、临时切片和可恢复副本策略。

### 16.4 云端模型数据边界

- 云端模型只接收稳定文本及最小必要上下文。
- 不发送原始音频、音频 URL、声纹 embedding、声纹分数或模型密钥。
- 从本地切换到云端需要本场明确确认。
- 可在发送前将真实姓名替换为稳定 speaker placeholder，本地回填。
- cloud 调用审计记录范围、水位、provider locality 和用户确认，不记录正文。

### 16.5 日志与指标

严禁记录：

- 音频正文或 base64。
- transcript 正文。
- 真实姓名。
- voiceprint embedding。
- API key/token。
- 云端完整请求。
- 服务器绝对存储路径。

结构化日志允许：

- `request_id`、`trace_id`。
- `meeting_id`、`connection_id`、`job_id`。
- `segment_ordinal`、`event_cursor`。
- 脱敏错误码、耗时和字节数。

这些高基数 ID 只能进入日志/trace，不得作为 Prometheus label。

### 16.6 提示注入

会议逐字稿是不可信内容。Hermes system contract 必须明确：

- transcript 中的命令、角色描述和工具指令都只是会议内容。
- 不得因此改变 JSON Schema、系统规则或数据边界。
- profile 无无关工具，降低注入影响。
- 产物只引用输入 segment，不执行 transcript 中的链接、代码或命令。

### 16.7 订正反馈的数据边界

- 人工文本 revision 是会议记录的一部分；是否允许用于“提升后续识别”是独立设置。
- 设置关闭时不得生成 active term candidate、训练样本或跨会议词库。
- correction event 只能在 owner 权限范围内读取和管理。
- 默认只建立个人词库，不把订正共享给其他用户或全局模型。
- 不把完整会议上下文复制到词库；词库只保留最小必要术语、误识别映射和来源引用。
- 用户删除会议时，依赖该会议的候选需按保留政策删除或去关联；若术语已由用户独立确认，可保留独立词库项并记录来源已删除。
- 用户删除词库项或撤销贡献时，未来 ASR session 不再加载；历史会议文本不自动改写。
- 将订正数据用于离线模型训练需要单独、明确、可撤销的用途授权，不能从“保存逐字稿修改”推定同意训练。
- 订正正文、词库和 confusion pair 不进入普通日志或 Prometheus label。

## 17. 性能、质量与容量验收

### 17.1 发布前先冻结基线

P0 需建立 30-60 分钟中文金融会议评测集，包含：

- 安静会议室和远场麦克风。
- 2、4、8 人场景。
- 普通话、轻口音、语速变化。
- 金额、日期、百分比、股票代码、公司和项目名称。
- 短插话和重叠说话。
- 网络抖动、断网和重连。

测试音频必须有使用授权，不得提交真实生产会议或敏感声纹到代码仓库。

### 17.2 实时指标

最低发布目标：

| 指标 | 目标 |
| --- | --- |
| 首个 partial 延迟 | P95 <= 1.2 秒 |
| 句尾 stable 文本延迟 | P95 <= 2.5 秒 |
| stable event 到数据库提交 | P95 <= 200ms |
| stable 提交到前端可见附加延迟 | P95 <= 250ms |
| 音频 ACK 延迟 | 受控网络 P95 <= 300ms |
| 已确认 stable segment 丢失率 | 0 |
| 已确认 stable segment 重复率 | 0 |
| 时间戳与音频对齐误差 | 句级 P95 <= 500ms，目标 <= 300ms |
| 4 小时持续录音 | 无无界内存、句柄或队列增长 |

### 17.3 ASR 质量

- 记录中文 CER，不只记录整体准确率。
- 单独记录金额、日期、比例、证券代码和专有名词准确率。
- 流式 final CER 相对当前批量 FunASR 样例基线劣化不超过 2 个百分点，或达到项目实测更严格门槛。
- 热词开启和关闭分别评测，避免热词误伤普通词。
- 会后 final transcript 应不差于实时 final，并记录修订率。
- 单独统计人工订正率、词级错误类型和每小时订正次数。
- 对每个 lexicon version 做启用前后专有名词召回率和普通词误触发率对比。
- 新词库版本不能只提高目标词召回而明显损害全局 CER。
- 词库项被用户撤销或修回的比例超过门槛时自动暂停该项并进入复核。
- speaker-specific 候选必须单独评测，不能用个人样本训练又在同一批样本上验收。

订正改进建议验收指标：

| 指标 | 目标 |
| --- | --- |
| 用户明确激活术语在后续样例中的召回率 | 相对基线显著提升，具体门槛由 M0 数据确定 |
| 非目标普通词误触发率 | 不高于发布门槛，默认目标 < 0.5% |
| 词库导致整体 CER 劣化 | 不超过 0.2 个百分点 |
| 已撤销订正继续进入新词库版本 | 0 |
| `content_edit` 被自动晋升为热词 | 0 |
| 用户明确关闭贡献后新增跨会议候选 | 0 |

### 17.4 说话人与声纹

初始建议门槛：

| 指标 | 目标 |
| --- | --- |
| 2-8 人干净测试集 DER | <= 15% |
| 自动实名误接受率 | <= 0.1% |
| suggestion Top-1 精确率 | >= 95% |
| 撤销后未来新匹配 | 0 次 |
| 未授权持久声纹模板 | 0 条 |

具体自动实名门槛以独立留出集标定为准。达不到误接受率目标时只发布 suggestion，不开放 auto_match。

### 17.5 AI 性能与非阻塞

- AI job 入队 P95 <= 50ms。
- 健康模型下 rolling minutes 新鲜度 P95 <= 90 秒。
- 健康模型下 final minutes 在最后 stable 后 P95 <= 180 秒；超出显示处理状态，不伪装完成。
- Hermes 延迟/故障 30 分钟时，字幕延迟相对 AI 关闭基线劣化不超过 10%。
- 后处理队列满时合并/跳过中间 rolling 版本，stable transcript 和音频零丢失。
- 同时两场会议选择不同模型，产物模型串用次数为 0。

### 17.6 既有功能非回归

| 既有功能 | 门槛 |
| --- | --- |
| 聊天短语音 | 行为、响应和 P95 不变，性能劣化 <= 5% |
| 问答助手 Hermes | profile YAML、默认模型和会话行为不变 |
| 一级市场会议室 | 路由、API、工作流和前端 smoke 不变 |
| 其他 API | 旧 OpenAPI operation/request/response schema 无变化 |
| 前端其他页面 | 不请求 meetings API，不申请麦克风，不加载会议 bundle |
| 默认启动 | feature flag 关闭时现有服务集合和健康语义不变 |

## 18. 功能开关、配置与运行态

### 18.1 功能开关

生产首次部署全部默认关闭：

```text
SIQ_MEETINGS_ENABLED=0
SIQ_MEETING_REALTIME_ASR_ENABLED=0
SIQ_MEETING_CORRECTION_LEARNING_ENABLED=0
SIQ_MEETING_AI_ENABLED=0
SIQ_MEETING_VOICEPRINT_ENABLED=0
SIQ_MEETING_IMPORT_ENABLED=0
SIQ_MEETING_SYSTEM_AUDIO_ENABLED=0
```

关闭主开关时：

- 不显示导航。
- 不启动新增 stream/worker/speech 服务，或服务不接受业务流量。
- 不连接 ASR/Hermes/voiceprint 外部依赖。
- 不改变全局 readiness。
- 现有功能测试和运行行为与开发前一致。

### 18.2 配置建议

```text
SIQ_MEETING_ASR_WS_URL=
SIQ_MEETING_ASR_FINAL_URL=
SIQ_MEETING_AUDIO_ROOT=/home/maoyd/siq-research-engine/var/meetings
SIQ_MEETING_MAX_DURATION_SECONDS=14400
SIQ_MEETING_AUDIO_CHUNK_MS=200
SIQ_MEETING_AUDIO_MAX_FRAME_BYTES=
SIQ_MEETING_RECONNECT_BUFFER_SECONDS=60
SIQ_MEETING_LEXICON_AUTO_CANDIDATE_MIN_OCCURRENCES=
SIQ_MEETING_LEXICON_MAX_ENTRIES=
SIQ_MEETING_LEXICON_DEFAULT_WEIGHT=
SIQ_MEETING_STREAM_TICKET_TTL_SECONDS=60
SIQ_MEETING_MAX_ACTIVE_PER_USER=1
SIQ_MEETING_MAX_ACTIVE_TOTAL=4
SIQ_MEETING_AUDIO_MAX_FRAMES_PER_SECOND=20
SIQ_MEETING_AUDIO_MAX_BYTES_PER_SECOND=128000
SIQ_MEETING_AUDIO_RATE_BURST_SECONDS=2
SIQ_MEETING_AUDIO_RETENTION_DAYS=90
SIQ_MEETING_TEMP_RETENTION_HOURS=6
SIQ_MEETING_WORKER_CONCURRENCY=
SIQ_MEETING_AI_CONCURRENCY=
SIQ_MEETING_FINAL_ASR_MAX_CONCURRENCY=2
SIQ_MEETING_FINAL_ASR_WINDOW_OVERLAP_MS=2000
SIQ_MEETING_VOICEPRINT_KEY_ID=
SIQ_MEETING_VOICEPRINT_KEY_ENV=
```

阈值、并发、保留期和端口不应散落在业务代码中。

### 18.3 健康状态

会议模块健康需分层：

- `core_ready`：音频存储、stream gateway、实时 ASR 和数据库可用。
- `degraded`：声纹或 Hermes 不可用，但字幕可用。
- `unavailable`：核心音频/ASR 链路不可用。

可选组件故障不得使整个 SIQ API readiness 失败；会议能力接口应返回降级原因。

## 19. 可观测性与告警

### 19.1 指标

建议新增低基数指标：

```text
meeting_active_sessions
meeting_audio_frame_total{result}
meeting_audio_gap_total{reason}
meeting_ws_reconnect_total{reason}
meeting_asr_partial_latency_seconds
meeting_asr_stable_latency_seconds
meeting_asr_correction_total{intent,error_class,status}
meeting_term_candidate_total{status,type}
meeting_lexicon_entry_total{status,type}
meeting_lexicon_hit_total{result}
meeting_lexicon_version_total{result}
meeting_segment_persist_latency_seconds
meeting_event_publish_latency_seconds
meeting_postprocess_queue_depth{kind}
meeting_postprocess_oldest_age_seconds{kind}
meeting_ai_job_total{kind,status,locality}
meeting_ai_job_duration_seconds{kind,locality}
meeting_summary_freshness_seconds
meeting_voice_match_total{decision}
meeting_model_resolution_total{mode,locality,result}
meeting_caption_blocked_by_postprocess_total
meeting_speech_speaker_assignment_total{result}
meeting_speech_speaker_track_total{result}
meeting_speaker_recluster_durable_total{result}
meeting_speaker_recluster_decision_durable_total{result}
```

禁止使用 meeting ID、user ID、姓名、model_ref 或正文作为 metric label。

### 19.2 关键告警

- `meeting_caption_blocked_by_postprocess_total > 0`。
- 已确认 stable segment 丢失或重复。
- 固定模型会议发生未授权 fallback。
- 本地模式文本被发送到 cloud。
- 云端请求包含音频或声纹字段。
- 未授权/已撤销声纹产生新匹配。
- 跨用户访问 voice profile 或 meeting 成功。
- 音频存储持续失败。
- stream backlog、job queue 或内存无界增长。
- 会议 A/B 模型或产物串用。

### 19.3 运行看板

至少展示：

- 当前会议数、连接数和每场音频积压。
- partial/stable 延迟分位数。
- 音频缺口和重连率。
- ASR/speaker/voiceprint/Hermes 各自健康。
- AI 队列深度和最老任务年龄。
- rolling/final minutes 成功率。
- 模型本地/云端调用数量和失败率。
- 声纹建议、自动应用、确认和拒绝数量，不展示姓名。

## 20. 分阶段开发任务

### 20.1 阶段总览

| 阶段 | 目标 | 可交付结果 | 是否可独立发布 |
| --- | --- | --- | --- |
| M0 | 基线、协议和技术预研 | 冻结旧合同，选定 2pass 与 Hermes 隔离方案 | 文档/测试发布 |
| M1 | 新领域骨架 | Feature Flag、新表、Repository、状态机、权限、空页面 | 关闭状态发布 |
| M2 | 实时录音与字幕 | AudioWorklet、WS、ACK、ASR partial/final、音频落盘 | 内部仅字幕灰度 |
| M3 | 说话人、回放与订正反馈 | 匿名 speaker、重命名、会后音频、人工订正和个人术语 | 内部基础版 |
| M4 | 跨会议声纹 | 授权、加密注册、匹配建议、撤销删除 | 先 suggestion 灰度 |
| M5 | Hermes 模型与 AI | 动态模型、隔离 runner、纠错、滚动和最终纪要 | AI 分级灰度 |
| M6 | 会后完善 | 观点、待办、版本、导出、长录音导入 | 完整产品灰度 |
| M7 | 生产硬化 | 性能、长稳、安全、恢复、告警、回滚演练 | 全量候选 |

阶段不能通过“功能按钮能点”直接跳过前置指标。M0 ASR 与 Hermes 隔离预研不通过时，不得进入依赖其结论的生产开发。

### 20.2 M0：合同冻结与技术预研

#### MT-000 现有功能合同快照

目标：证明新增功能前后既有行为未变化。

工作项：

- 保存现有 OpenAPI operation、请求和响应 schema 快照。
- 保存现有数据库表、列、索引快照。
- 保存全部现有 Hermes profile YAML 哈希。
- 记录现有服务端口、默认启动服务和 health/readiness 结果。
- 跑现有聊天语音、一级市场会议室、Hermes client/model control 和前端路由测试。
- 记录聊天短语音与现有 Hermes 请求性能基线。

完成标准：

- 形成机器可比较基线产物，放入测试 fixture 或 CI artifact，不提交敏感运行配置。
- 后续每个阶段可自动判断旧 API/schema/profile 是否发生未授权变化。

#### MT-001 FunASR 2pass 预研

目标：选定满足低延迟和中文质量的独立会议 ASR 方案。

工作项：

- 部署 FunASR 官方 2pass/Paraformer streaming 候选。
- 使用固定授权音频评测 partial 延迟、stable 延迟、CER、时间戳和资源占用。
- 验证 30 分钟、4 小时持续连接。
- 验证 2、4、8 人样例和重叠发言。
- 比较 CPU streaming + 8899 finalizer 与其他隔离方案。
- 输出容量、端口、进程和资源优先级建议。

完成标准：

- 明确选型和拒绝其他方案的可复现实验结果。
- partial P95 接近或达到 1.2 秒目标，stable P95 接近或达到 2.5 秒目标。
- 无全量音频无界内存累计。
- 不修改 8899 已有接口和行为。

#### MT-002 Hermes Run 级模型隔离预研

目标：实现每场会议独立选择 Hermes 可用模型，且不写共享 profile。

工作项：

- 验证 Hermes 是否支持 run-scoped provider/model override。
- 验证 override 是否能在 allowlist 内限制并隐藏凭证。
- 同时运行会议 A/Nemotron 和会议 B/云端模型，验证互不影响。
- 如果不支持，构建两个不可变 meeting target 的最小 gateway pool 原型。
- 验证模型切换边界和执行快照。
- 执行前后比较现有 profile YAML 哈希。

完成标准：

- 选择路径 A 或 B，并落盘 ADR。
- 两场并发会议不同模型零串用。
- 现有 `create_run()` 和全局模型切换测试原样通过。
- 会议实现不调用 YAML 写入函数。

#### MT-003 声纹评测与隐私基线

目标：确定首版只开放 suggestion 还是允许 auto_match。

工作项：

- 建立有授权的开发/验证分离数据集。
- 选择 speaker embedding encoder，固定版本。
- 评测设备、噪声、有效时长和多人条件。
- 校准 Top-1、Top-2 差值和质量阈值。
- 定义 consent policy version、用户私有 scope 和删除流程。
- 完成隐私/安全评审。

完成标准：

- 误接受率不满足目标时明确关闭自动命名，只发布建议确认。
- 不使用生产会议或历史聊天音频做无授权回填。
- 给出加密、密钥轮换和删除验证方案。

### 20.3 M1：独立领域骨架

#### MT-010 Feature Flag 与配置

- 新增 `SIQ_MEETINGS_*` 配置读取和类型校验。
- 主开关默认关闭。
- 配置错误只影响会议 capability，不影响全局 API 启动，除非用户明确启用会议核心能力。
- 添加 local/production example，不写真实密钥。
- 新增功能关闭时验证无会议网络请求和进程依赖。

#### MT-011 数据表与幂等迁移

- 仅新增第 8 节会议表和索引。
- 同时验证 SQLite 测试和生产 PostgreSQL。
- migration 可重复执行。
- 旧版本应用忽略新表仍能启动。
- 增加迁移前后旧表 schema diff，必须为空。

#### MT-012 Repository、状态机与 Outbox

- 实现 meeting session 状态转移校验。
- 实现 stable segment 原子追加和 ordinal 分配。
- 实现 event cursor 和同事务 outbox。
- 实现 model settings 乐观锁。
- 实现 speaker alias 和 revision 展示优先级。
- 单元测试非法转移、重复消息、并发更新和人工锁。

#### MT-013 权限与对象隔离

- 新增 meeting 权限常量和 owner 检查。
- session、audio、segment、artifact、voiceprint 均执行对象级校验。
- 双用户 BOLA 测试覆盖全部资源。
- 跨用户统一 404，不泄露资源存在。

#### MT-014 前端路由与空骨架

- 懒加载注册 `/meetings/*`。
- 主导航新增“会议转写”，feature flag 关闭时不可见。
- 新增列表、新建、实时、详情和声纹空页面。
- 仅 `/meetings` 路径隐藏 ChatBot。
- 原 routes tests 断言保留，只追加新断言。

M1 完成标准：开关关闭可安全部署；开关打开可看到无副作用的空产品骨架；旧 OpenAPI/profile/schema 回归全部通过。

### 20.4 M2：实时录音、传输与字幕

#### MT-020 Meeting Speech Service

- 按 MT-001 结论部署独立流式 ASR。
- 实现 partial/final、VAD、时间戳和热词 adapter。
- 推理和音频缓冲有硬上限。
- 提供内部健康和延迟指标。
- 不改变 8899 现有端口和接口。

#### MT-021 Stream Ticket 与 WebSocket Gateway

- 实现一次性短时 ticket、Origin 校验和单生产者 lease。
- 实现固定格式 binary frame、sequence、ACK 和重复去重。
- 实现背压、最大帧、最大速率、会议时长和并发限制。
- stable segment 与 durable event 同事务提交。
- 实现连接心跳、优雅 stop 和 ASR flush。

#### MT-022 浏览器 AudioWorklet

- 用户点击开始后才申请麦克风权限。
- 使用 AudioWorklet 采集并转换 16kHz mono PCM16LE。
- 每块附带 epoch、sequence 和 capture time。
- 页面后台、设备切换和权限撤销有明确状态。
- 不复用 `useVoiceRecorder.ts`。

#### MT-023 断线缓存与恢复

- 未 ACK 分片短期写 IndexedDB outbox。
- 重连协商最高连续 sequence。
- 重发未确认块并由服务端幂等去重。
- 超出缓存产生时间轴 gap。
- 刷新/关闭/网络抖动集成测试通过。

#### MT-024 实时逐字稿 UI

- 实现 partial 原位更新、stable 固定段落和事件去重。
- 独立录音/ASR/speaker/Hermes 状态。
- 自动跟随和“回到实时”。
- 长列表窗口化。
- stable 字幕可访问性播报，partial 不重复播报。
- 375px 到 1920px 无遮挡和横向溢出。

#### MT-025 音频分片持久化

- chunk 原子落盘、校验、manifest 和 ACK 顺序正确。
- storage 不可写时立即安全降级/停录并提示。
- 音频分片与字幕使用同一时间轴。
- 4 小时持续存储无句柄泄漏和无界内存。

M2 完成标准：内部白名单可进行“仅录音和实时字幕”的真实会议，断网可恢复，音频和 stable 文本零丢失；Hermes 完全关闭也能正常工作。

### 20.5 M3：说话人、重命名、音频回放和订正反馈

#### MT-030 实时匿名 Speaker Track

- speaker worker 只产生匿名 track。
- speaker 结果晚到时以 patch 更新 segment 映射。
- 支持多人同时发言/unknown 状态。
- 不因 speaker 慢阻塞 stable 文本。

#### MT-031 人工重命名与优先级

- 用户重命名某 track 后，本场所有相关 segment 立即解析新名称。
- 逐字稿段落支持显式选择仅改单段或修改同 track 的本场全部发言；单段路径以映射事件受控拆分。
- 只更新 alias，不批量破坏性改写文本。
- 人工命名优先于声纹结果。
- 并发重命名使用 version/409。

#### MT-032 会后音频封装和 Range 回放

- 校验 chunk manifest 后生成 FLAC/M4A 等可回放文件。
- 实现鉴权 Range API。
- 音频播放器支持播放、前后 10 秒、倍速、音量和 seek。
- 点击时间戳跳转，当前段高亮不只依赖颜色。
- 缺口在时间轴明确显示。

#### MT-033 会后最终 ASR 与 Speaker 重聚类

- 按块执行最终识别，不将数小时音频一次读入内存。
- 产生 final transcript revision 和时间戳对齐结果。
- track merge/split 使用映射事件。
- 人工确认文本和姓名不被自动覆盖。

#### MT-034 句级人工订正

- 每个 stable segment 支持原位修改、保存、取消和撤销。
- 保存使用 expected revision，冲突返回 409 并允许用户合并。
- 用户选择 `识别错误` 或 `仅修改表述`。
- 人工 revision 立即成为展示优先级最高版本并锁定。
- 展示 ASR 原文与人工文本的最小 diff。

#### MT-035 Correction Feedback 管道

- revision 与 correction event 同事务写入。
- 服务端计算 diff 和 punctuation/itn/lexical/entity/content_edit 分类。
- 记录 ASR、speaker、时间戳和 lexicon provenance。
- 用户关闭贡献时只保存 revision，不创建 active candidate。
- 撤销 revision 同步把反馈标记 reverted。

#### MT-036 个人术语候选和版本

- 实现 term candidate 聚合、确认、拒绝和冲突检测。
- 实现手工“加入个人术语”。
- 发布不可变 lexicon version 和 entries hash。
- 支持 pause/delete/rollback，不改写历史 transcript。
- 首期仅支持 current meeting 和 owner private future meetings。

#### MT-037 将订正用于后续识别

- ASR session 启动加载 active 个人术语版本。
- ASR 支持热更新时从明确 sequence/ordinal 边界加载新 hotword。
- confusion pair 只在高精度、无歧义时进入受控后处理。
- Hermes correction glossary 使用同一版本化 canonical term 列表。
- 建立词库启用前后 CER、实体召回和误触发评测。
- 不实现单次修改后自动训练生产 ASR 模型。

M3 完成标准：匿名说话人、人工命名、音频回放和会后逐字稿形成完整证据链；用户订正可生成可审计反馈，确认术语可在后续会议提高识别，且普通润色不会污染词库。

### 20.6 M4：跨会议声纹

#### MT-040 Voiceprint Consent

- 重命名弹窗明确区分“仅本次”和“保存声纹”。
- 同意前不持久化模板。
- 保存 policy version、用途、范围和来源会议。
- 未启用声纹的会议不发送匹配请求。

#### MT-041 样本选择和加密注册

- 只选择多个清晰、非重叠片段。
- 样本不足时进入 collecting。
- embedding 应用层加密，保存 encoder/key version。
- 临时切片成功、失败、超时、重启后均清理。
- 不删除按用户策略保留的会议原始音频。

#### MT-042 跨会议匹配

- 只检索 owner 私有、active、授权有效模板。
- 综合分数、差值、时长和质量。
- 实现 unknown/suggestion/auto_match。
- 阈值来自版本化评测配置，前端不硬编码。

#### MT-043 确认、拒绝和撤销

- confirm、reject、undo 幂等。
- 本场拒绝后不重复建议同候选。
- 人工命名永远优先。
- 完整 match audit 不包含 embedding。

#### MT-044 声纹管理和删除

- 列表、暂停、恢复、重采、撤销和删除。
- 撤销后未来匹配立即停止。
- 删除覆盖密文、临时副本和备份策略。
- 恢复演练验证已删除模板不复活。

M4 完成标准：先以 suggestion 模式内部灰度；只有独立评测达到误接受率门槛后，才单独开启 auto_match flag。

### 20.7 M5：Hermes 模型、纠错和纪要

#### MT-050 动态模型目录

- 从 Hermes 配置和 runtime health 只读生成目录。
- 返回 model_ref、label、locality、capability、available 和脱敏原因。
- 前端不存在固定模型名、端口或 provider 列表。
- 目录短 TTL 缓存，支持管理员刷新。

#### MT-051 会议专用 Hermes Runner

- 按 MT-002 选择 run override 或 immutable target pool。
- `siq_meeting` 最小权限、无无关工具。
- 每个 job 保存执行快照。
- 并发会议模型隔离测试通过。
- 现有 Hermes profile YAML 哈希不变。

#### MT-052 模型选择和切换

- 支持 none/pinned/auto。
- pinned 默认无 fallback。
- 本地到云端明确确认。
- 切换返回生效 segment ordinal。
- 切换不重连麦克风/ASR。
- 旧产物不覆盖，旧 job 继续使用旧快照。

#### MT-053 Stable Text Correction

- 实现结构化 patch、JSON Schema 和 base revision 校验。
- 金额/日期/比例等关键实体默认只标复核。
- 人工锁不覆盖。
- 保留原文、diff、撤销和模型 provenance。
- 模型超时不影响字幕。

#### MT-054 Rolling Minutes

- 30-60 秒去抖，输入水位幂等。
- 队列压力下合并/跳过中间版本。
- 展示最近更新时间和实际模型。
- 明确标记“临时纪要”。

#### MT-055 Final Minutes

- 在 final transcript/speaker 水位后生成。
- 输出概览、议题、章节、决定、问题、风险、待办、观点和关键词。
- 每项带 source segment。
- schema 无效时失败，不保存伪完成产物。
- 用户可用当前模型创建新版本。

M5 完成标准：任何模型故障都不阻塞 ASR；模型选择并发安全；逐字稿和产物版本完整可追溯。

### 20.8 M6：会后完善与长录音导入

#### MT-060 会后工作台

- 完成纪要、逐字稿、观点、待办和导出标签。
- 修改逐字稿后旧纪要标记 stale。
- 提供版本对比和重新生成。
- 所有 AI 结论可跳转到证据时间戳。

#### MT-061 导出

- TXT、Markdown、SRT、VTT、JSON 首批支持。
- DOCX/PDF 可后续实现，但不得阻塞核心发布。
- 导出选择 transcript layer 和 artifact version。
- 文件鉴权、命名、内容转义和审计通过。

#### MT-062 长录音文件导入

- 独立 `/meetings/import`，不进入聊天窗口。
- 使用分片/可续传上传和大小、时长限制。
- 导入后复用 meeting session、segment、speaker、voiceprint 和 artifact 模型。
- 不复用 60 秒短语音 endpoint。
- 首期实时会议发布不依赖该任务，可在 M6 单独交付。

### 20.9 M7：生产硬化和发布

#### MT-070 安全与隐私测试

- 双用户 BOLA、ticket 重放、Origin、超大帧、速率、路径穿越和 SSRF。
- 日志、指标、错误、导出和云端请求敏感信息扫描。
- transcript prompt injection 测试。
- 撤销/删除竞态测试。

#### MT-071 性能与长稳

- 固定发布并发 `C_release`。
- `C_release + 20%` 运行延迟和队列测试。
- 4 小时 soak、worker 重启、gateway 重启、数据库短断和存储故障。
- Hermes 30 分钟不可用，验证字幕基线。

#### MT-072 可观测性和运维手册

- 指标、看板、告警和脱敏日志。
- 新服务启动、停止、状态、日志和健康命令。
- ASR/voiceprint/Hermes 分层排障。
- 音频缺口、任务堆积、模型不可用和声纹撤销操作手册。

#### MT-073 灰度与回滚演练

- 白名单、5%、25%、100% 阶段开关。
- 关闭 AI、关闭声纹、关闭整个会议模块的独立回滚。
- stop accepting、drain worker、保留数据和恢复服务演练。
- 旧版本应用忽略新表并正常启动。

M7 完成标准：第 17、21、22 节门槛全部通过，才能申请全量。

## 21. 自动化测试矩阵

### 21.1 后端单元测试

- meeting 状态机合法/非法转移。
- start、pause、resume、stop、finalize 幂等。
- stream epoch 和 lease 竞争。
- 音频 sequence、ACK、重复和缺口。
- stable segment ordinal 并发分配。
- event cursor 和 outbox 原子性。
- transcript 展示优先级。
- 人工锁不被 AI 覆盖。
- 人工订正 revision 与 feedback 同事务。
- asr_error/content_edit 分类和贡献开关。
- diff 提取、撤销和候选降权。
- term candidate 聚合、冲突、确认和拒绝。
- lexicon version 发布、回滚和生效边界。
- content edit、纯标点和撤销事件不得晋升。
- speaker alias、merge 和 split 映射。
- model settings 乐观锁和生效边界。
- job snapshot 与幂等键。
- pinned/auto/none 和 fallback 策略。
- voiceprint consent、pause、revoke、delete 状态机。
- voice match suggestion/confirm/reject/undo。
- 权限和跨用户 404。
- retention 和受控路径。

### 21.2 协议和集成测试

- WebSocket ticket 一次性和过期。
- Origin 和对象权限。
- 二进制帧协议版本拒绝。
- ASR partial/final 乱序和重复。
- ASR 慢、断开和恢复。
- speaker 结果晚到。
- voiceprint 不可用时匿名降级。
- Hermes 500、超时、限流和 schema 无效。
- 两场会议选择不同模型。
- 切换模型时旧 job 晚到。
- worker 重复投递和 lease 接管。
- Redis/事件通知中断后 cursor 回放。
- 音频存储失败和磁盘空间不足。

### 21.3 前端单元测试

- partial 被 stable 原位替换。
- 乱序、重复和重连事件 reducer。
- 人工文本优先级。
- speaker 人工命名优先级。
- 时间戳格式和 seek 映射。
- 句后铅笔按钮、原位编辑和 revision 冲突。
- “识别错误 / 仅修改表述”意图选择。
- 订正 diff、加入个人术语和撤销。
- 个人术语候选、停用和版本展示。
- 自动跟随暂停/恢复。
- 模型目录加载、禁用项和切换边界。
- 本地到云端确认。
- voiceprint 注册三选一。
- suggestion、auto_match、reject、undo。
- 独立录音/ASR/声纹/Hermes 状态。

### 21.4 Playwright E2E

使用浏览器 fake media 和固定授权 fixture：

1. 新建会议，收到 partial 和 stable。
2. 用户向上滚动，自动跟随停止，再回到实时。
3. 暂停、恢复并显示时间轴 gap。
4. ASR 重连后不重复 stable segment。
5. Hermes 故障时字幕继续。
6. 修改某句并标记识别错误，保存 diff 和人工 revision。
7. 仅修改表述时不产生词库候选。
8. 用户确认专有词后，本场后续/下一场 ASR 加载新词库版本。
9. 撤销订正后候选不再生效。
10. 动态模型加载和会议中切换。
11. 从本地切到云端的确认。
12. 重命名发言人并选择“仅本次”。
13. 重命名并授权保存声纹。
14. 下一场会议 suggestion/auto_match 和撤销。
15. 会后播放、点击时间戳、倍速和搜索。
16. 修改逐字稿后纪要 stale，重新生成新版本。
17. 导出 SRT/Markdown。
18. 375、390、768、1366、1440、1920 宽度无溢出、遮挡和重叠。
19. 键盘、焦点恢复、读屏状态和 reduced-motion。

### 21.5 性能和恢复测试

- 4 小时单会议 soak。
- `C_release` 并发会议。
- 网络延迟、抖动、丢包和 30-60 秒断网。
- 浏览器后台节流和设备断开。
- gateway/worker/API 进程重启。
- 数据库短时不可用。
- 模型服务共享 GPU 压力。
- Hermes 长时间不可用。
- 声纹 encoder 不可用。
- 导出和 finalization 大任务并发。

### 21.6 必须保留的非回归测试

至少执行：

```text
apps/api/tests/test_chat_voice_transcription.py
apps/api/tests/test_primary_market_meeting_router.py
apps/api/tests/test_hermes_client.py
apps/api/tests/test_hermes_model_control.py
apps/web/e2e/tests/chat-voice.spec.ts
apps/web/src/app/routes.test.ts
```

以及：

```bash
cd apps/web && npm run test:unit
cd apps/web && npm run check:frontend
cd apps/api && uv run python -m pytest tests
scripts/check_all.sh
```

大范围测试可按阶段执行，但每个发布候选必须完成全量回归。

### 21.7 新增 CI 门禁

- 旧 OpenAPI diff：除新增 `/api/meetings/v1` 外不得变化。
- 旧数据库 schema diff：旧表不得变化。
- Hermes profile 哈希：现有 profile 文件必须一致。
- 作用域 allowlist：功能 PR 出现无关文件时阻断并人工评审。
- Feature Flag off 回归：必须单独跑。
- 敏感信息扫描：fixture、日志和构建产物不得含生产音频、声纹、token。
- 前端 bundle：非会议路由不得预加载会议媒体代码。

## 22. 验收标准

### 22.1 功能验收

- `AC-F-01`：用户可以从独立“会议转写”入口创建和结束实时会议。
- `AC-F-02`：浏览器持续录音，音频分片可靠落盘并可会后回放。
- `AC-F-03`：实时展示 partial，句尾展示 stable，重连不重复 stable。
- `AC-F-04`：字幕有可定位音频的时间戳。
- `AC-F-05`：匿名说话人可人工重命名并应用到本场全部相关片段。
- `AC-F-06`：重命名不隐式保存声纹。
- `AC-F-07`：明确授权后可创建跨会议声纹。
- `AC-F-08`：下一场启用声纹识别后可给出可靠建议；达到阈值时可自动命名并撤销。
- `AC-F-09`：用户可选择 Hermes 可用本地或云端模型，也可关闭 AI。
- `AC-F-10`：模型切换不影响录音和 ASR，只影响边界后的 AI 任务。
- `AC-F-11`：LLM 纠错保留 ASR 原文、差异、版本和撤销。
- `AC-F-12`：滚动纪要明确标记临时，会后生成可追溯最终纪要。
- `AC-F-13`：观点、决定和待办可以跳转到原始 segment 和音频。
- `AC-F-14`：修改逐字稿后旧纪要标记 stale，重新生成创建新版本。
- `AC-F-15`：支持受控导出和删除。
- `AC-F-16`：用户可以在具体发言句后点击修改，保存可追溯人工 revision。
- `AC-F-17`：识别错误保存原文、订正文、diff、ASR 版本、时间戳和来源 speaker。
- `AC-F-18`：仅修改表述、纯标点、大段重写和撤销订正不会进入识别词库。
- `AC-F-19`：用户明确确认的个人术语可用于本场后续或未来会议，并记录生效词库版本。
- `AC-F-20`：词库项可以查看、停用、删除和回滚，历史逐字稿不会被静默改写。
- `AC-F-21`：用户关闭“使用订正提升识别”后不再产生跨会议候选。
- `AC-F-22`：订正闭环通过专有名词召回、全局 CER 和误触发率对照验收。

### 22.2 隔离和非回归验收

- `AC-I-01`：`/api/chat/transcribe` URL、请求、响应、60 秒限制和 FunASR 参数不变。
- `AC-I-02`：`/primary-market/meeting` 页面、API、Hermes IC 工作流不变。
- `AC-I-03`：现有 Hermes profiles 内容、默认模型和 fallback 不变。
- `AC-I-04`：会议从未调用全局模型 YAML 写入函数。
- `AC-I-05`：旧数据库表、列、索引和约束无变化。
- `AC-I-06`：旧 OpenAPI operations 和 schemas 无变化。
- `AC-I-07`：Feature Flag off 时导航、进程、网络请求和健康语义与开发前一致。
- `AC-I-08`：非会议页面不申请麦克风、不连接 meeting WS、不请求 meeting API。
- `AC-I-09`：Hermes/voiceprint 故障不传播到其他模块。
- `AC-I-10`：现有全量测试和发布检查通过。

### 22.3 安全和隐私验收

- `AC-S-01`：用户 A 无法访问用户 B 的会议、音频、逐字稿、产物或声纹。
- `AC-S-02`：stream ticket 一次性、短时、绑定用户/会议/Origin，重放失败。
- `AC-S-03`：浏览器看不到内部 endpoint、模型密钥和 embedding。
- `AC-S-04`：未经授权不持久化声纹模板。
- `AC-S-05`：云端只接收允许范围的稳定文本，不接收音频和声纹。
- `AC-S-06`：本地模型不静默 fallback 到云端。
- `AC-S-07`：撤销后无新声纹匹配，删除后模板不在恢复中复活。
- `AC-S-08`：日志、指标和错误无音频、正文、姓名、embedding 和 token。
- `AC-S-09`：prompt injection 不触发工具或改变输出合同。
- `AC-S-10`：所有导出和删除有权限与审计。

### 22.4 性能和可靠性验收

- `AC-P-01`：达到第 17.2 节 partial/stable 延迟门槛。
- `AC-P-02`：4 小时会议无无界内存、句柄和队列增长。
- `AC-P-03`：已确认 stable segment 零丢失、零重复。
- `AC-P-04`：Hermes 故障 30 分钟时字幕性能劣化不超过门槛。
- `AC-P-05`：网关和 worker 重启后可恢复，不重复产物。
- `AC-P-06`：音频缺口被明确记录，不伪造连续时间轴。
- `AC-P-07`：声纹只在通过独立精度门槛后开放自动命名。
- `AC-P-08`：两场会议不同模型并发运行无串用。

## 23. 灰度发布与回滚

### 23.1 发布顺序

1. 建立 MT-000 基线。
2. 执行只新增表的 expand-only migration。
3. 部署关闭状态 API、worker、stream gateway 和监控。
4. 部署隐藏状态前端。
5. 内部白名单开启“仅录音和实时字幕”。
6. 开启匿名 speaker 和会后回放。
7. 本地模型 AI 先 shadow 运行，不展示结果。
8. 灰度显示 AI 修订和 rolling minutes。
9. 开放用户选择云端模型和明确数据边界确认。
10. 对授权内部用户开放 voiceprint suggestion。
11. 达到误接受率门槛后单独灰度 auto_match。
12. 按 `5% -> 25% -> 100%` 或白名单扩大。

每一阶段至少覆盖：

- 一场 30 分钟真实内部会议。
- 一次断网恢复。
- 一次可选组件故障。
- 一次开关回滚。

### 23.2 回滚原则

- 优先关闭 AI、声纹或会议主开关，不删除数据表。
- 先停止接收新会议，再 drain 活动会议和 worker。
- 运行中 job 标记可重试、取消或完成，不能遗留永久 running。
- 旧版本应用应忽略新增表正常启动。
- 已创建会议保留查看、导出和删除通道。
- 紧急回滚不执行破坏性 schema downgrade。
- 隐私/越权事件立即关闭声纹。
- 模型串用、云端误外发、音频或 stable 数据丢失时关闭整个新增功能。

### 23.3 必须演练的回滚场景

- 关闭 Hermes AI，字幕继续。
- 关闭声纹，匿名 speaker 继续。
- 停止 meeting worker 后恢复，任务幂等续跑。
- stream gateway 滚动重启，客户端重连。
- 关闭会议入口，其他页面无变化。
- 退回不识别新表的旧版本应用。
- 声纹删除后恢复数据库/文件备份，确认模板不会重新启用。

## 24. Pull Request 与所有权边界

建议按以下 PR 拆分，禁止把所有能力放入一个大 PR：

| PR | 内容 | 禁止混入 |
| --- | --- | --- |
| PR-00 | 文档、合同快照、评测脚本 | 业务实现 |
| PR-01 | Feature Flag、migration、repository、状态机 | UI、ASR、Hermes |
| PR-02 | Stream ticket、WS、音频存储、speech adapter | AI、声纹 |
| PR-03 | 实时前端和断线恢复 | 会后 AI |
| PR-04 | Speaker、重命名、回放 | Voiceprint 自动实名 |
| PR-05 | Consent、加密声纹、suggestion | Hermes 纠错 |
| PR-06 | Model catalog、meeting runner、模型切换 | 全局模型设置变更 |
| PR-07 | Correction、rolling/final minutes、版本 | 长录音导入 |
| PR-08 | 会后工作台、导出、可选导入 | 无关页面重构 |
| PR-09 | 性能、安全、告警、运行手册 | 新产品功能扩张 |

每个 PR 必须包含：

- 变更范围。
- 明确不变项。
- 自动化测试。
- Feature Flag 行为。
- 数据迁移或无迁移说明。
- 可观察状态。
- 回滚步骤。
- 若有前端可见变化，提供桌面和移动截图。

Owner 建议：

- Web：会议页面、AudioWorklet、事件 reducer、回放与可访问性。
- API：合同、权限、Repository、状态机和 Range API。
- Speech/ML：2pass ASR、diarization、voiceprint encoder 和质量评测。
- Hermes：模型目录、隔离 runner、prompt/schema 和 provenance。
- Platform：stream gateway、worker、存储、部署和可观测性。
- QA/Security/Privacy：回归、负载、BOLA、授权、删除和灰度门禁。

同一 PR 不得同时重构现有 chat voice、IC meeting 或全局 Hermes model control。

## 25. 完成定义（Definition of Done）

本任务只有满足以下全部条件才可标记完成：

### 产品与功能

- 独立会议导航、列表、新建、实时和会后页面可用。
- 录音、实时字幕、时间戳、音频回放和会后逐字稿闭环完成。
- 匿名说话人、人工重命名和证据时间轴完成。
- 声纹注册有独立授权，跨会议 suggestion 完成。
- auto_match 只有达到误接受率门槛才开启。
- 句级人工订正、feedback、个人术语候选和版本化词库闭环完成。
- 普通润色不进入 ASR 改进；用户关闭贡献后无新跨会议候选。
- 已确认个人术语能被后续 ASR/纠错使用，并有启用前后质量对照。
- Hermes 模型由用户从动态可用目录选择，本地和云端都可支持。
- 切换模型不影响 ASR，不覆盖历史产物。
- 纠错、实时纪要和最终纪要版本化、可追溯、可撤销。

### 工程与可靠性

- 音频、stable segment、event 和 job 均幂等。
- API 进程不承担长时推理。
- 4 小时持续会议和发布并发通过。
- 断网、重连、进程重启和可选组件故障通过。
- 监控、告警和运行手册完成。
- 灰度和回滚演练完成。

### 安全与隐私

- 对象级权限和双用户 BOLA 全通过。
- 音频访问鉴权、ticket、Origin、限流和输入限制完成。
- 声纹模板加密、授权、撤销、删除和恢复验证完成。
- 云端数据边界可见且无静默 fallback。
- 日志、指标、错误和导出敏感信息检查通过。
- 隐私/安全负责人完成评审。

### Additive Only

- 现有聊天短语音行为和测试不变。
- 现有一级市场投研会议室行为和测试不变。
- 现有 Hermes profiles 和全局模型设置行为不变。
- 旧数据库 schema 不变。
- 旧 OpenAPI contract 不变。
- Feature Flag off 时现有产品运行行为不变。
- 全仓回归通过，无无关重构和格式化 churn。

## 26. 开发前必须确认的决策点

以下决策已给出推荐默认值，但必须在对应阶段开始前记录最终结果：

| 决策 | 推荐默认 | 最晚确认阶段 |
| --- | --- | --- |
| 产品导航名称 | 会议转写 | M1 |
| API 前缀 | `/api/meetings/v1` | M1 |
| 首期音频源 | 浏览器麦克风 | M1 |
| 首期最长会议 | 4 小时 | M2 |
| 音频默认保留 | 90 天，可配置 | M1/隐私评审 |
| 声纹作用域 | 用户私有 | M4 |
| 声纹默认开关 | 关闭 | M4 |
| 自动实名 | 未过门槛时关闭，仅 suggestion | M4 |
| AI 默认 | 用户选择；允许 none | M5 |
| pinned fallback | disabled | M5 |
| 本地到云端 | 本场明确确认 | M5 |
| rolling minutes 周期 | 30-60 秒去抖 | M5 |
| 订正事件记录 | 保存人工修改时始终记录 revision 与审计 | M3 |
| 跨会议订正贡献 | 首次明确同意前关闭；同意后保存个人偏好且可随时关闭 | M3/隐私评审 |
| 候选自动晋升 | 默认关闭；用户明确确认或质量门槛 | M3 |
| 个人术语作用域 | 当前会议 + 用户私有未来会议 | M3 |
| 单次订正自动训练 ASR | 禁止；仅离线审核训练 | M3 |
| 2pass 引擎 | M0 实测后决定 | M0 |
| Hermes 隔离 | run override 优先，否则 immutable target pool | M0 |
| 长录音导入 | M6，不阻塞实时 MVP | M6 |

## 27. 最终交付物

代码之外必须同时交付：

- ASR 选型与性能报告。
- 声纹阈值和误接受率评测报告。
- 订正反馈分类规则、个人词库 schema 和版本协议。
- 个人术语启用前后 CER、实体召回和误触发评测报告。
- 订正数据用于/不用于离线模型训练的用途与授权说明。
- Hermes run-scoped 模型隔离 ADR。
- API/OpenAPI 和 WebSocket 协议文档。
- 数据库 migration 与数据字典。
- 音频和声纹保留/删除政策说明。
- 管理员模型目录和健康检查说明。
- 用户声纹授权与撤销说明。
- 运行、监控、告警、备份和恢复手册。
- 安全测试、隐私检查、负载和 4 小时 soak 报告。
- 灰度与回滚演练记录。
- 非回归证据：旧 OpenAPI、旧 schema、Hermes profile 哈希和全量测试结果。

## 28. 实施结论

本功能应作为 SIQ 的一个全新、隔离的会议产品域实施。实时字幕由独立流式 ASR 主链路保证；说话人、跨会议声纹和 Hermes AI 均为可降级的异步能力。用户可以选择任意 Hermes 已配置且运行时可用的本地或云端模型，Nemotron 8007 只是一种可能选项。

实现过程中最重要的工程约束是：

```text
新会议功能可以逐步增加，现有功能不得因此发生任何行为变化。
```

任何需要修改现有聊天短语音、一级市场会议室、共享 Hermes profile、旧数据库契约或旧 API 语义的实现，都不符合本任务书，必须停止并重新设计隔离边界。

## 29. iOS 息屏后台录音（原生采集端）

本节是会议产品域的新增隔离能力，不回写或替换第 5-23 节已经定义的桌面端和 Web 端录音链路。现有浏览器 `AudioWorklet + WebSocket + IndexedDB` 实现、桌面端行为、API 默认语义和 feature flag 关闭时的运行结果必须保持不变。

### 29.1 能力边界与冻结决策

必须向产品和用户明确以下边界：

1. 纯 Web、安装到主屏幕的 PWA、浏览器 `getUserMedia`、WebRTC 和运行在 `WKWebView` 中的 JavaScript，均不能承诺 iOS 锁屏或长期进入后台后持续执行。页面计时器、WebSocket、音频回调和 JavaScript bridge 都可能被系统暂停。
2. Web/PWA 可继续作为前台录音能力，但不得把“保持页面打开”“阻止自动锁屏”或静音音频保活包装成可靠的息屏录音方案。
3. 需要承诺 iOS 息屏持续录音时，采用 Capacitor 复用现有 React 会议 UI，并增加独立 Swift 原生采集插件。`AVAudioSession`、`AVAudioEngine`、本地文件和后台上传均由原生层持有，不能依赖 WebView 存活。
4. 原生录音只能由用户在前台明确点击开始后建立；不能远程唤醒录音、不能在应用冷启动时自动录音，也不能承诺在用户强制退出应用、设备重启或系统终止进程后继续。
5. 来电、音频路由变化、权限撤销、系统媒体服务重置和磁盘不足都必须产生明确状态或时间轴缺口，禁止把中断伪装成连续录音。
6. iOS 原生采集通过独立 feature flag、独立构建目标、独立 capture adapter 和新增 API 灰度。关闭该能力后，桌面/Web 仍走原链路，既有录音、重连和回放测试结果不得变化。

推荐新增开关：

```text
SIQ_MEETING_IOS_NATIVE_CAPTURE_ENABLED=false
```

前端只能通过 Capacitor runtime capability 和后端 capability 响应选择 `ios_native` adapter，禁止仅依赖 User-Agent 猜测。非原生环境继续选择现有 `web_audio_worklet` adapter。

### 29.2 原生采集与本地优先架构

目标链路如下：

```text
Capacitor 会议 UI
  -> Swift MeetingCapturePlugin
       -> AVAudioSession + AVAudioEngine input tap
       -> 受保护的本地录音资产
       -> 原子 manifest / checkpoint
       -> 可恢复 batch outbox
       -> background URLSession 批量上传
  -> Meeting Batch Ingest API
       -> capture token 校验
       -> batch 幂等落盘与校验
       -> 时间轴补齐 / ASR catch-up
  -> 回到前台后 stream rollover
       -> 新 stream ticket + 新 stream epoch
       -> 恢复实时字幕
```

原生层职责：

- 配置 `NSMicrophoneUsageDescription`、Background Modes 的 audio 能力以及符合审核用途的录音说明，不使用播放静音音频等规避系统策略的保活手段。
- 使用 `AVAudioSession` 的录音或录放类别和 `AVAudioEngine` input tap 采集，具体 category、mode、采样率和编码必须由 MT-081 真机原型冻结。
- 监听 interruption、route change、media services reset、scene lifecycle 和 engine configuration change；事件处理不依赖 JavaScript callback 及时执行。
- 以音频 sample offset 作为权威时间轴；墙上时钟只用于展示，时区变化和系统校时不能导致 batch 重叠或倒退。
- 音频先写本地持久文件并 `fsync`/原子更新 manifest，再进入上传队列。网络上传、实时 ASR 和 WebView 状态都不是录音成功的前置条件。
- 维护可立即播放的本地资产。可以是单一可播放文件，也可以是由原生播放器消费的有序分片清单，但停止录音后不得等待云端转码、最终 ASR 或 AI 纪要才能播放。
- 使用允许已开始录音在设备锁定后继续写入、同时满足数据保护要求的 iOS file protection 策略；策略和威胁取舍必须经过安全评审和锁屏真机验证。
- 会议音频和临时 batch 默认排除 iCloud/iTunes 备份；服务器确认完整接收前不得清理唯一的本地副本。

建议插件最小合同：

```text
prepare(meeting_id, capture_id, audio_config)
start()
pause(reason)
resume()
stop()
getStatus()
getCheckpoints()
getLocalPlaybackAsset()
retryPendingUploads()
discardLocalCapture(confirmed_server_complete)
```

插件事件至少包括 `capture.started`、`capture.progress`、`capture.interrupted`、`capture.resumed`、`batch.sealed`、`batch.uploaded`、`capture.stopped`、`local.playback.ready` 和 `capture.error`。UI 重新激活时必须通过 `getStatus()` 和 `getCheckpoints()` 拉取权威快照，不能假设后台期间所有 bridge 事件都已送达。

### 29.3 Capture Token 与批次 API

iOS 原生上传使用新增的 capture token，不复用浏览器 Cookie、长期 JWT、WebSocket stream ticket 或 Hermes token。capture token 必须：

- 只在已认证用户对本人会议执行显式开始操作后签发。
- 绑定 `owner_user_id + meeting_id + capture_id + device_installation_id`、允许的编码、采样率、最大时长、最大字节数、用途、过期时间和随机 nonce。
- 只授予 `batch:write`、本 capture 的 `checkpoint:read` 和必要的 `capture:seal` 权限，不授予音频读取、会议管理或跨 capture 权限。
- 通过 Authorization header 发送，不进入 URL、文件名、manifest、分析日志和错误报告；原生端保存在 Keychain，不进入 WebView localStorage。
- 支持撤销和受控续期。token 过期且应用无法在后台刷新时继续本地录音，回到前台重新鉴权后补传，不能删除或丢弃待传音频。

建议追加以下隔离 API，最终路径和 schema 在 MT-080 冻结：

| Method | Path | 作用 |
| --- | --- | --- |
| `POST` | `/sessions/{id}/native-captures` | 创建 `capture_id`、签发 capture token、返回音频和批次限制 |
| `PUT` | `/sessions/{id}/native-captures/{captureId}/batches/{epoch}/{sequence}` | 幂等上传一个二进制音频 batch |
| `GET` | `/sessions/{id}/native-captures/{captureId}/checkpoint` | 返回服务端已持久化水位和缺失范围 |
| `POST` | `/sessions/{id}/native-captures/{captureId}/rollover` | 对账旧流并申请新的实时 stream epoch/ticket |
| `POST` | `/sessions/{id}/native-captures/{captureId}/seal` | 声明停止时的最终 batch、sample 水位和 manifest 摘要 |

每个 batch 至少携带：

- `capture_id`、`stream_epoch` 和单调 `sequence`。
- `first_sample`、`sample_count` 和原生单调时钟采集时间。
- 编码、采样率、声道数、字节数和 SHA-256。
- 本地 manifest revision 和幂等键。

服务端唯一约束使用 `(capture_id, stream_epoch, sequence)`。相同键和相同摘要重复上传返回原 ACK；相同键但摘要不同必须返回稳定冲突错误并停止自动覆盖。只有 batch 内容和元数据已持久化、校验完成且可由恢复任务重新发现后，服务端才推进 ingest ACK。批次大小、请求体、速率、并发、capture 总时长和总容量都必须有服务端硬限制。

### 29.4 Checkpoint 拆分与 Stream Rollover

不得继续用一个“已连接/重连中”状态代表录音、上传、ASR 和回放。至少拆分以下四类 checkpoint：

| Checkpoint | 权威方 | 最小水位 | 用途 |
| --- | --- | --- | --- |
| capture checkpoint | iOS 原生插件 | `recorded_through_sample`、最后 sealed batch、manifest revision | 判断设备实际录到了哪里 |
| ingest checkpoint | Meeting Batch Ingest | 最高连续持久化 batch/sample、缺失范围 | 判断服务器可靠收到了哪里 |
| realtime checkpoint | Stream Gateway / ASR | stream epoch、已消费 sample、stable ordinal、event cursor | 恢复实时字幕而不重复 stable |
| finalization checkpoint | Meeting Worker / Audio Store | sealed sample、音频完整性、local/server playback readiness、postprocess state | 决定何时可回放和何时可执行最终处理 |

这些水位可以暂时不同。UI 必须分别显示“后台录音中”“正在补传”“实时字幕恢复中”“本地回放可用”“云端音频处理中”等状态；只要 capture checkpoint 仍推进，就不能因为 WebSocket 已断开而把录音显示为失败或无限“重连”。

息屏和回前台的 rollover 流程固定为：

1. iOS 进入后台或锁屏后，Swift 插件继续写本地文件并封存 batch；WebSocket 和 WebView 可被暂停，其失败不得停止 `AVAudioEngine`。
2. 原生层通过系统允许的 background `URLSession` 尝试上传；系统未调度网络任务时保留本地 outbox，不承诺后台字幕实时性。
3. 回到前台后，UI 先读取原生 capture checkpoint，再查询服务端 ingest/realtime checkpoint，按缺失范围顺序补传。
4. 旧 WebSocket lease/epoch 只做封口，不尝试复活；客户端调用 rollover 获得严格递增的新 `stream_epoch` 和一次性 stream ticket。
5. ingest checkpoint 追上 rollover 边界后再恢复实时上行；若选择并行追赶，服务端必须按 sample offset 排序并用统一 chunk identity 去重，不能让新音频永久越过旧缺口。
6. 实时字幕恢复使用新的 epoch 和 durable event cursor；历史缺口由 batch catch-up 补齐。无法补齐时写入显式 `audio.gap.detected`，结束重连循环并允许用户继续当前录音。

前台 WebSocket 和后台 batch API 可能看见同一段音频时，必须共享稳定的 `capture_id + epoch + sequence/sample range` 身份。任一路径先持久化后，另一路径只能幂等命中，禁止形成双份音频或重复逐字稿。

### 29.5 停止后立即回放

停止录音必须拆成“本地封口”“服务器收齐”“音频可回放”和“会后 AI 完成”四个独立结果：

```text
用户点击结束
  -> Swift 停止 input tap 并封存最后 batch
  -> 原子写入最终本地 manifest
  -> 立即返回 local playback asset
  -> 后台继续补传未 ACK batch
  -> 服务端校验 seal 水位和完整性
  -> server playback ready
  -> 最终 ASR / speaker / minutes 异步继续
```

强制合同：

- `stop()` 幂等；重复点击、应用切前后台和网络重试不能生成多个 capture 或丢失最后一个 batch。
- 最后一个本地 batch 封存后，播放器立即切换到本地资产。目标为点击结束后 P95 2 秒内可开始播放已录内容，且不依赖网络。
- 本地资产通过受控 Capacitor URL 或原生播放器句柄暴露，不把任意 `file://` 路径开放给 WebView，不在前端状态和日志中暴露真实沙箱路径。
- 在线且无 backlog 时，服务器音频在 seal 校验后立即建立可 Range 回放的 manifest/容器；不得等待最终 ASR、speaker 重聚类、Hermes 或导出任务。
- 有 backlog 或离线时，详情页继续显示本地回放和明确的“待同步”状态。服务端只在收到 seal 声明的全部 batch 或记录不可恢复缺口后进入最终音频状态。
- server playback ready 后 UI 无缝从本地来源切到鉴权 Range API，保持当前播放时间；切换失败时保留本地播放，不显示永久“重连”。
- 只有服务端确认 ingest 完整、server playback ready 且保留策略允许清理后，才能删除本地音频；用户主动删除必须同时处理本地 outbox、Keychain token 和服务器删除任务。

### 29.6 安全、隐私与运行约束

- iOS 录音全程保留系统麦克风指示，应用内持续显示录音状态和停止入口；锁屏不等于新的授权，原始用户开始意图必须可审计。
- capture token 泄露半径限制在单一 capture 和上传用途，服务端支持立即撤销、过期、重放检测、容量限制和异常设备告警。
- 本地文件使用 iOS Data Protection；文件保护等级必须同时通过锁屏写入测试和安全评审。敏感文件不进入系统备份、共享目录、剪贴板或崩溃附件。
- 所有传输使用 TLS；服务端不信任客户端声明的时长、MIME、sample count、摘要或 owner，必须校验格式、内容上限、对象权限和 capture 状态。
- 日志、指标和崩溃报告禁止包含音频正文、绝对文件路径、capture token、用户 JWT 或可还原录音的 buffer。允许记录脱敏 capture ID、batch 水位、字节数、错误码和耗时。
- 磁盘空间接近阈值时先告警并停止产生无法可靠落盘的新音频，保留已封存内容；禁止为了继续录音静默删除未上传 batch。
- 系统来电或音频中断结束后，只有同一用户启动且尚未结束的 capture 才可恢复；中断区间写入 gap。用户已点击结束后任何通知都不能重新启动引擎。
- 后台上传由 iOS 调度，不承诺实时或固定延迟。后台转写延迟不能被描述为录音丢失，只要本地 capture checkpoint 正常推进且 outbox 可恢复。
- App Store 隐私清单、麦克风用途、后台音频用途和数据删除说明必须在发布前完成审核，不以技术可运行替代平台政策合规。

### 29.7 M8：iOS 原生采集、补传与立即回放任务

#### MT-080 iOS Capture ADR 与隔离骨架

- 冻结 Capacitor shell、Swift 插件目录、capture adapter 接口、feature flag 和 capability contract。
- 冻结 batch schema、capture token、checkpoint、rollover 和 seal API/OpenAPI；所有合同使用 `/api/meetings/v1` 下的新增路径。
- 建立 Web 与 iOS adapter contract test，证明非原生环境仍选择现有 `AudioWorklet`。
- 记录纯 Web/PWA 不承诺息屏持续、原生能力限制和 App Store 合规结论。
- 不修改聊天短语音、桌面/Web 默认采集、既有 stream ticket 和 `/primary-market/meeting`。

#### MT-081 Swift AVAudioSession/AVAudioEngine 采集插件

- 实现显式 prepare/start/pause/resume/stop、权限处理和后台 audio 配置。
- 实现 input tap、统一采样格式、sample offset、route/interruption/media reset 处理。
- WebView/JavaScript 被暂停时独立持续采集，bridge 恢复后提供权威状态快照。
- 强制退出、设备重启、系统终止和权限撤销返回真实终态，不作不可能的持续录音承诺。
- 在支持矩阵真机上冻结 session category、mode、buffer、编码、功耗和温升参数。

#### MT-082 本地录音资产、Outbox 与恢复

- 音频先落受保护本地文件，batch、manifest 和 capture checkpoint 原子更新。
- 实现滚动 batch、SHA-256、磁盘配额、崩溃恢复、重复启动保护和未完成 capture 扫描。
- 建立立即可播放的本地资产，并在 stop 后 P95 2 秒内返回播放器句柄。
- 文件排除备份；服务器完整确认前不清理唯一副本，清理任务支持重启后继续。
- 磁盘不足、文件损坏和 manifest 不一致产生稳定错误与显式 gap，不静默丢弃。

#### MT-083 Capture Token 与 Batch Ingest API

- 实现 capture token 签发、Keychain 保存、作用域、过期、撤销和前台续期。
- 实现 batch PUT、checkpoint GET 和 seal，服务端校验 owner、格式、摘要、时长、容量和状态。
- ACK 只在音频与可恢复元数据持久化后返回；相同键幂等、摘要冲突拒绝覆盖。
- background URLSession 支持系统接管上传、网络切换、失败重试和应用重启恢复。
- capture token 不进入 URL、日志、manifest、WebView 存储和崩溃附件。

#### MT-084 Checkpoint 对账与 Stream Rollover

- 分别实现 capture、ingest、realtime、finalization checkpoint，禁止共享单一 reconnect 状态。
- 前台恢复按服务端 missing ranges 顺序补传，处理重复、乱序、部分 ACK 和超时。
- rollover 原子封口旧 lease，签发递增 stream epoch 和一次性 ticket，stable/event 去重通过。
- 同一音频经 WebSocket 和 batch 两条路径到达时只保留一份存储和一份 transcript evidence。
- 无法补齐的范围落为显式 gap；UI 退出无限重连并允许继续新 epoch。

#### MT-085 停止、同步与立即回放状态机

- 实现本地封口、pending upload、server ingest complete、local/server playback ready 和 postprocess 独立状态。
- stop 幂等封存最终 batch，离线时本地立即回放，联网后自动继续补传。
- 服务端回放准备不等待最终 ASR、speaker、Hermes 和导出，Range API 就绪后通知 UI 切源。
- 切换本地/服务端音源保持 seek 时间；本地资产缺失或服务端延迟都有明确可恢复状态。
- 结束后不再显示“重连录音”；只显示真实的补传、音频处理或会后任务状态。

#### MT-086 iOS 安全、隐私与故障恢复

- 完成麦克风用途、后台 audio、隐私清单、录音指示、删除和 App Store 审核材料。
- 完成 Data Protection、Keychain、排除备份、token 重放/BOLA、文件路径和日志脱敏测试。
- 覆盖来电、Siri/其他音频中断、蓝牙/耳机切换、权限撤销、低磁盘、低电量和系统终止。
- 验证用户停止后不会自动复录，未上传音频不会因 token 过期、崩溃或升级被静默清除。
- 建立本地孤儿 capture、服务端半完成 capture 和撤销 token 的清理/恢复手册。

#### MT-087 真机长稳、灰度与非回归验收

- Simulator 结果不能作为息屏验收证据；覆盖所有声明支持的 iPhone 机型档位和 iOS 主版本。
- 真机执行锁屏 1、10、30、60 分钟和 4 小时 soak，核对本地 sample 数、服务端 ingest 水位、音频时长、摘要、gap 和重复。
- 覆盖前后台反复切换、Wi-Fi/蜂窝切换、30 分钟离线、来电、音频路由变化、低电量模式、磁盘紧张、应用崩溃和升级恢复。
- 验证结束后本地回放 P95 2 秒内可用；有网时 server playback 独立于最终 ASR/AI 就绪；离线恢复后可补齐并无重复。
- 单独灰度 iOS 原生 flag，记录耗电、温升、存储、上传流量、后台中断率、补传时长、gap 率和回放就绪延迟。
- 执行现有桌面/Web 录音、重连、Range 回放、聊天短语音和一级市场会议室全量非回归；任何既有行为变化都阻断发布。

M8 完成标准：在支持矩阵真机上，用户前台明确开始录音后可以锁屏，Swift 原生层持续把音频可靠写入本地；网络和 WebView 暂停不造成已录音频丢失；回到前台后通过 batch 对账和新 epoch 恢复实时链路；点击结束后无需等待云端即可立即本地回放，并在服务器收齐后切换到鉴权 Range 回放。该能力必须保持为可独立关闭的新增功能，桌面端、纯 Web/PWA 和现有会议录音行为不变。
