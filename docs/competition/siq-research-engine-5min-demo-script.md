# SIQ Research Engine 5 分钟 Demo 演示脚本

> 建议成片时长：4 分 50 秒至 5 分 10 秒。
> 主演示公司：上汽集团 `600104`。
> 主演示报告：`2025-annual`，报告期末 2025 年 12 月 31 日。
> 核心问题：核验商誉账面价值，并说明原值、减值准备、计算过程和原文出处。

## 演示策略

整段视频只走一条主线：

```text
官方年报
  -> 高精度解析
  -> LLM-Wiki 证据对象
  -> Hermes analysis / factchecker
  -> 确定性财务勾稽
  -> PDF 原文回跳
  -> OpenShell runtime provenance
```

一级市场 Deal OS、会议和应用中心只用于证明产品完整性，不展开第二条现场任务。二级市场的跨市场、跨语言覆盖需要作为独立亮点呈现。评委在 5 分钟内应该记住四个关键词：

1. 六个市场组，跨语言研究；
2. 证据先于回答；
3. 数字由程序复算；
4. Agent 在受控边界内工作。

## 分镜与逐字口播

### 00:00-00:25 开场：先讲问题，不先报技术栈

**画面动作**

- 从 SIQ 主工作台开始，画面中同时露出项目名称和二级市场分析入口。
- 鼠标短暂停在公司、报告、分析和证据区域，不要快速乱点。
- 右下角字幕显示：`SIQ Research Engine | Evidence-first AI Research`。

**逐字口播**

> 大模型很会写“像研报”的文章，但在真实投研中，流畅不等于可信。研究员还要知道：数字来自哪份报告、哪一页、哪张表，计算能否复现，Agent 有没有越权。SIQ Research Engine 要构建的，就是从官方材料到可审计结论的完整生产链。

### 00:25-01:00 二级市场：六个市场组与跨语言上市公司研究

**画面动作**

- 打开二级市场的市场切换器，依次展示 `CN / HK / US / EU / KR / JP`。
- 展开欧洲国家筛选，清楚显示英国、法国、德国、荷兰和瑞士。
- 快速切换一份境外上市公司的原语言披露与中文分析结果，画面中同时保留市场、公司、报告和原文引用。
- 最后扫过侧边栏的一级市场 Deal OS、文档解析和会议入口，说明它们共享基础设施但拥有独立业务 scope。
- 画面字幕：`CN · HK · US · EU(UK/FR/DE/NL/CH) · KR · JP`。

**逐字口播**

> SIQ 二级市场已支持中国内地、香港、美国、韩国、日本，以及欧洲首批五国：英国、法国、德国、荷兰和瑞士。面对多语言披露，大模型负责理解原文并形成中文研究表达；系统同时保留市场、公司、报告、币种、会计准则和来源坐标。研究员可以在一个工作台完成跨市场、跨语言的上市公司研究。

### 01:00-01:25 为什么是 DGX Spark

**画面动作**

- 打开系统状态或模型管理页。
- 依次高亮 Nemotron、MinerU、Qwen Embedding、Qwen Reranker、FunASR 的 ready 状态。
- 插入一张 DGX Spark 资源采样画面，显示 GB10、aarch64、CUDA 13 和约 128 GB 统一内存。

**逐字口播**

> 这不是一个模型加向量库。SIQ 在一台 DGX Spark 上，让 Nemotron 负责本地私有推理，MinerU 解析文档，Qwen 完成向量检索和精排，FunASR 处理语音；数据服务与 OpenShell 同机协同。GB10 和统一内存，让这些能力组成一台完整的研究设备。

### 01:25-02:05 官方年报进入证据生产线

**画面动作**

- 进入上汽集团 `600104`，选中 `2025-annual` 年报。
- 展示原始 PDF、解析任务和完成状态。
- 打开解析结果，依次显示页面、表格、Markdown 行、source map 和 quality/validation 状态。
- 画面字幕：`ResearchIdentity = market / company / filing / parse run`。

**逐字口播**

> 现在用上汽集团年报走一条真实闭环。材料进入后，SIQ 先冻结市场、公司、披露和解析运行四层身份，避免串公司、串市场、串报告期。MinerU 与规则引擎恢复页面、跨页表格和来源坐标。只有通过完整性、三表覆盖和财务关系质量门，材料才能进入正式事实层。

### 02:05-02:35 LLM-Wiki：不是传统切片 RAG

**画面动作**

- 展示 LLM-Wiki 中的 company、report、fact、metric 和 document link。
- 从“商誉”主表事实点击或动画连线到“商誉账面原值”和“商誉减值准备”附注对象。
- 同屏短暂展示 Milvus，标注 `补充语义候选，不决定权威财务事实`。

**逐字口播**

> 第二个差异是 LLM-Wiki。传统 RAG 把年报切成匿名文本，再依赖相似度找答案；SIQ 建立公司、报告、事实、关系和证据对象。查询商誉时，系统先定位主表净额，再沿 document links 找到附注中的原值和减值准备。Milvus 可以发现补充材料，但不能改写权威财务事实。

### 02:35-03:20 Hermes 岗位智能体完成分析与反向核查

**画面动作**

- 在分析助手中输入固定问题：

```text
请核验上汽集团 2025 年年报的商誉账面价值。
分别给出商誉账面原值、减值准备和主表净额，
展示计算过程，并提供可回跳的原文证据。
```

- 使用提前录好的真实流式响应，保留 SSE 输出过程，但将等待段剪短到 3 至 5 秒。
- 高亮 `siq_analysis`、`siq_factchecker`、引用编号和 validation card。
- 若页面能展示工具调用，短暂露出 calculator/financial reconciliation 回执。

**逐字口播**

> 我现在要求分析助手核验商誉。这里不是让多个角色互相赞同。Hermes 的 analysis 负责组织结论，factchecker 反向检查数字、口径、引用和遗漏风险；每个岗位都有自己的职责和禁止行为。历史记忆可以保存用户偏好和纠错，却不能替代当前材料。SIQ 的原则是：记忆负责连续性，证据负责事实。

### 03:20-04:10 财务勾稽和原文回跳：全片核心证据

**画面动作**

- 将回答中的 financial trace 放大到可读尺寸。
- 按顺序高亮以下三行：

```text
商誉账面原值：1,282,085,915.36 元
减值准备：      98,963,594.89 元
主表账面净额：1,183,122,320.47 元
```

- 高亮公式：`原值 - 减值准备 = 主表净额`，以及 `difference = 0`。
- 点击主表引用，回跳 PDF 第 65 页、表格索引 84。
- 点击附注引用，回跳 PDF 第 137 页、表格索引 165 和 166。

**逐字口播**

> 这是 Demo 最关键的一步。模型不能靠心算宣布“已经核验”，SIQ 的 Decimal 工具会重算公式，并绑定每个输入的期间、单位和证据。商誉原值十二点八二亿元，减值准备零点九九亿元，得到净额十一点八三亿元，与主表一致，差异为零。点击净额回到 PDF 第六十五页；点击原值和准备，回到第一百三十七页的附注表。引用因此不只是链接，而是可复核的证据坐标。

### 04:10-04:40 OpenShell：让 Agent 的权限和运行来源可见

**画面动作**

- 展开本次回答的 runtime provenance。
- 高亮：`profile=siq_analysis`、`target=openshell`、company scope、sandbox generation、lease release、fallback 状态。
- 如画面中存在 `/health`，展示 `openshell_recovery.ready=true`。
- 字幕显示：`链路已跑通 != Formal Production GO`。

**逐字口播**

> 第三项创新是 OpenShell 安全运行面。API 决定公司 scope，Gateway 和 Sandbox 限制 Agent 可访问的文件、服务与凭据。本次回执记录了 profile、模型、sandbox generation 和是否回退。链路跑通不等于生产门已经 GO；安全、质量和发布审批是三个独立状态。

### 04:40-05:05 收束商业价值

**画面动作**

- 快速回到完整研究报告，随后闪回 Deal OS、会议和六市场画面。
- 最后定格项目名称、GitHub URL 和一句主张。
- 结束字幕：`Evidence first. Calculation reproducible. Execution controlled.`

**逐字口播**

> SIQ 不替研究员决策，而是把找材料、解析、核数、引用和审计沉淀为可复用生产线。模型可以替换，索引可以重建，但事实身份和证据链保持稳定。这就是 SIQ Research Engine：让数字有出处，让计算可复现，让 Agent 有边界。

## 必须出现在画面中的证据

| 证据 | 最低画面要求 | 证明什么 |
| --- | --- | --- |
| 项目名称与主工作台 | 首尾各一次 | 产品不是脚本集合 |
| 二级市场与欧洲国家筛选 | CN/HK/US/EU/KR/JP，EU 展示 UK/FR/DE/NL/CH | 六个市场组与跨语言研究覆盖 |
| 境外原语言披露与中文研究输出 | 同屏保留公司、报告和原文引用 | 大模型跨语言理解不丢证据身份 |
| DGX Spark 与模型 ready 状态 | 至少 5 个本地模型/服务 | 多模型单机协同与 NVIDIA 平台适配 |
| 上汽公司、报告和解析任务身份 | 公司代码、报告 ID、task/parse run | 防止串公司、串报告 |
| source map / quality 状态 | 页码、table index、quality/validation | 解析产物可定位、有门禁 |
| LLM-Wiki 主表到附注关系 | 商誉事实与两个附注对象 | 非匿名切片 RAG |
| analysis / factchecker | 两个 profile 或相应运行记录 | 岗位合同与反向核查 |
| financial trace | 三个输入/结果、公式、`difference=0` | 程序复算，不是模型心算 |
| PDF 回跳 | 第 65 页与第 137 页 | 结论可回到原始披露 |
| runtime provenance | OpenShell target、scope、generation、fallback | Agent 执行边界可审计 |
| GitHub URL | 结尾至少停留 3 秒 | 评委可复核项目 |

## 录制与剪辑要求

1. 成片使用 1440p 或 4K，浏览器缩放建议 110% 至 125%，确保页码、公式和金额在手机端也能看清。
2. 录制前关闭系统通知、聊天弹窗和浏览器自动填充，不在画面中出现 API key、token、本机用户名、私有文件路径或客户材料。
3. 所有模型提前 warm up。不要把冷启动、长时间 token 等待或服务重启录入成片。
4. Agent 回答必须来自真实请求，但允许剪掉等待段；剪辑处用轻微淡化过渡，不伪造成无延迟实时生成。
5. 财务 trace 和 PDF 回跳不能只用字幕代替，必须录到实际 UI 或真实脱敏回执。
6. 架构图只停留 6 至 8 秒，禁止逐框朗读。五分钟的视觉中心应是证据闭环。
7. 背景音乐音量低于人声 18 至 22 dB；财务公式和结尾处适当降低音乐。
8. 全程加字幕，对 `ResearchIdentity`、`LLM-Wiki`、`difference=0`、`OpenShell` 等词使用统一高亮色。

## 录制前固定检查

- [ ] Web、API、Hermes Gateway 和演示公司数据 ready。
- [ ] 二级市场六个市场入口可见，欧洲筛选准确显示 UK/FR/DE/NL/CH。
- [ ] 至少准备一份境外原语言披露与对应中文研究输出，用于证明跨语言链路。
- [ ] Nemotron、MinerU、Embedding、Reranker、FunASR 完成 readiness 和最小请求。
- [ ] 上汽 `2025-annual` 的商誉 trace 可返回 `status=pass`、`difference=0`。
- [ ] 主表第 65 页与附注第 137 页回跳可用。
- [ ] `siq_analysis` 当前运行来源、OpenShell scope 和 provenance 可展示。
- [ ] 已准备相同请求的完整本地录屏与 JSON 回执作为兜底。
- [ ] 所有外部材料、图片和音频均可用于赛事展示。
- [ ] 结尾 GitHub URL 在未登录浏览器中可访问。

## 异常时的成片兜底

| 异常 | 处理方式 | 口播是否变化 |
| --- | --- | --- |
| StepFun 网络波动 | 使用提前录制的同一真实请求，或切本地 Nemotron 并展示来源 | 将“双主模型协同”改为“当前使用本地私有路径” |
| Agent 响应过慢 | 剪掉等待段，保留请求发出、首个事件和完整终态 | 不变 |
| OpenShell scope 临时不可用 | 展示明确 Host fallback 和 fallback reason | 不得声称本次真实进入 OpenShell |
| PDF 在线预览卡顿 | 使用本地预录的真实页码回跳片段 | 不变 |
| 某项 quality/validation 为 warning | 保留 warning 并解释系统没有伪装成功 | 强调 fail-closed 和可见降级 |

最重要的原则是：异常可以剪辑，真实状态不能改写。
