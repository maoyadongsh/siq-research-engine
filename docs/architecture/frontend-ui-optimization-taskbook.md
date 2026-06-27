# SIQ Research Engine 前端 UI/UX 优化任务书

编写日期：2026-06-27  
最近更新：2026-06-27  
适用范围：`apps/web` React + Vite 前端  
目标读者：接手优化的前端工程师、UI 设计工程师、产品/验收人员

## 0. 当前执行进度快照

更新时间：2026-06-27  
当前状态：阶段性完成，尚未达到完整 Definition of Done。

已完成并通过 `npm run build`、`npm run lint` 的内容：

- Phase 1 部分完成：新增 `apps/web/src/styles/tokens.css`，定义统一 radius、shadow、surface 基础类；新增 `components/page/*` 页面/Surface primitives；统一 `Button` 兼容新旧 API。
- Phase 2 部分完成：优化 `Sidebar`、`Topbar`、`GlobalSearch`、`NotificationMenu` 的视觉、active 状态、移动端浮层表现和键盘焦点样式。
- Phase 3 部分完成：优化 `MyWorkspace` 首屏信息密度、统计卡、流程入口和当前研究对象卡片视觉。
- Phase 4 部分完成：重排 `SearchDownload` 查询区，市场选择改为 segmented control；日志默认折叠；批量下载栏 sticky；已下载财报文件按当前市场过滤。
- Phase 5 部分完成：`PdfParsing` 增加市场 Tab、移动端锚点导航、日志折叠、统一进度状态；A 股解析页已下载列表固定只显示 `CN/` 清单。注意：曾尝试把 `PdfUploadPanel` 放进窄侧栏，导致已下载列表裁切；已回退为全宽稳定布局，后续不要再次把该组件直接放入窄侧栏。
- Phase 6 部分完成：`ReportViewer` 的报告选择和操作栏改为 sticky toolbar，阅读区改用统一 surface。
- Phase 7 部分完成：全局助手和页面助手消息气泡、composer 视觉进一步统一；移动端全局聊天改为接近全屏 bottom sheet；新增 `components/chat/ChatShell.tsx`、`ChatHeader.tsx`、`ChatMessageList.tsx`、`ChatComposer.tsx`，并已接入全局浮窗 `ChatBot`、独立问答页 `ChatPage`、页面侧栏 `AgentChatPanel`。
- Phase 8 部分完成：登录/注册页移动端隐藏视频，保证用户名、密码、主按钮和注册入口优先可见。
- Admin/Help 页面族部分完成：`Help` 已迁移到 `components/page` 的 `PageShell`、`PageHeader`、`PageSection`、`Surface`、`StatusBadge`；`VectorIngest` 已改为统一 Page/Surface 布局，启动命令卡片、状态卡片、空状态和操作按钮改用统一 primitives/Button；`Account`、`UserAdmin` 已完成第一轮 Page/Surface 统一，账户信息、额度卡、用户审批筛选、批量操作、空/错误状态和移动端用户卡片保留原业务逻辑但收敛到统一 primitives。
- 多市场下载/解析清单过滤已修复：搜索下载页底部列表按当前市场显示；A 股解析页只显示 `CN/`；港股/美股/日股/韩股解析页通过 `MarketParsingPage` 传入对应 market。
- Playwright 第一轮已接入：`apps/web/playwright.config.ts` 自动启动 Vite；`e2e/support/mockApi.ts` 注入 mock 登录态和工作平台数据；`e2e/tests/workspace-responsive.spec.ts` 已覆盖工作平台首页 390x844、768x1024、1366x768、1440x900、1920x1080 五个视口，检查六个流程入口列数、卡片边界和无横向溢出。

未完成或只做了第一轮的内容：

- Playwright/截图验收刚完成首页第一轮；搜索下载、PDF 解析、多市场解析、报告阅读、聊天、管理页、设置页、帮助页、向量入库页仍待逐页确认 390x844、768x1024、1366x768、1440x900、1920x1080。
- 聊天系统已完成第一轮共享组件抽象，但尚未做 Playwright/多视口截图验收；后续如继续扩展，应优先复用 `components/chat/ChatShell.tsx`、`ChatHeader.tsx`、`ChatMessageList.tsx`、`ChatComposer.tsx`，避免在页面内重新复制消息列表或 composer。
- `components/ui/legacy/*` 尚未淘汰，`apple-card`、`apple-panel` 仍在多处作为兼容类使用。
- 设置页、用户详情页等管理页尚未系统性统一；帮助页、向量入库页、账户页和用户审批页已完成第一轮视觉收敛，但仍待多视口截图验收。
- PDF 解析和多市场解析页还需要进一步统一日志、任务状态、空状态、移动端结果 tabs。
- 表格移动端卡片化只覆盖了搜索下载候选表，其他管理/系统表格仍需继续处理。

继续执行原则：

- 每次只改一个页面族或一个共享组件族，避免再出现大范围布局回归。
- 不改变后端 API 契约，不改变财报检索、下载、解析、报告生成的数据精度逻辑。
- 涉及市场列表时必须同时考虑 `CN/HK/US/JP/KR`，并保持下载页和解析页清单按市场隔离。
- 每轮改动后必须运行：

```bash
cd /home/maoyd/siq-research-engine/apps/web
npm run build
npm run lint
```

## 1. 项目定位

SIQ Research Engine 是一个面向财报研究、披露文件检索、PDF 解析、智能报告生成、事实核查、持续跟踪和法务合规的专业研究工作台。前端不应做成营销站，也不应做成装饰很重的展示页；它应该像一个高端、可信、可长时间工作的金融研究操作系统。

本次优化目标是：

- 让界面更精美、大气、专业，形成稳定一致的 SIQ 视觉语言。
- 降低复杂流程的认知负担，让用户知道当前在哪一步、下一步做什么。
- 同时提升桌面端工作效率和移动端可用性。
- 在不大规模重写业务逻辑的前提下，优先整理布局、组件、交互状态、响应式与可访问性。

## 2. 已检查范围

重点检查了以下前端文件和模块：

- 全局入口与路由：`apps/web/src/App.tsx`
- 全局样式与设计 token：`apps/web/src/index.css`
- 应用壳层：`components/layout/Layout.tsx`、`Sidebar.tsx`、`Topbar.tsx`、`GlobalSearch.tsx`、`NotificationMenu.tsx`
- 核心页面：`pages/MyWorkspace.tsx`、`SearchDownload.tsx`、`PdfParsing.tsx`、`AnalysisReport.tsx`、`Login.tsx`
- 报告阅读：`components/report/ReportViewer.tsx`、`ReportFrame.tsx`、`ReportSelector.tsx`、`ReportToolbar.tsx`
- 智能体聊天：`components/chat/ChatBot.tsx`、`components/agent/PageWithAgentChat.tsx`、`AgentChatPanel.tsx`
- PDF 工作台：`components/pdf/*`
- 通用研究组件：`components/research/*`
- 新旧 UI 组件：`components/ui/*`、`components/ui/legacy/*`

参考准则：

- Vercel Web Interface Guidelines：布局稳定性、交互状态、可访问性、响应式、内容层级、控件语义等。

## 3. 现状结论

当前前端已经有较完整的业务功能和一套初步的“premium / secondary”视觉语言，主导航、顶部栏、工作台、检索下载、PDF 解析、报告阅读和智能体聊天都已具备可用基础。

主要问题不是“丑”，而是“不够统一、不够克制、不够像一个成熟产品系统”。视觉层有多套风格并存：`premium-shell`、`premium-card`、`secondary-*`、`apple-card`、`apple-panel`、legacy UI、Radix 风格组件和大量页面内联 Tailwind class 同时存在。结果是半径、阴影、边框、卡片密度、标题层级、按钮样式和响应式行为在页面之间不稳定。

移动端已有很多补丁式适配，但核心复杂页面仍依赖横向滚动、较大的卡片半径、较密集的表单与多栏布局。桌面端则存在信息密度与视觉装饰之间的拉扯：工作台希望“大气”，下载和解析页面又需要更高操作效率。

## 4. 设计方向

### 4.1 视觉关键词

- 专业金融研究
- 企业级可信
- 明亮、通透、克制
- 信息密集但不拥挤
- 面向长时间使用
- 可审计、可追踪、可操作

### 4.2 推荐视觉语言

主色继续使用当前蓝色系，但减少全局渐变和过多发光阴影。建议采用：

- 主色：`#0071e3` 或等价 primary blue
- 辅助强调：绿色用于成功、琥珀用于警告、红色用于危险，避免整站单一蓝色
- 背景：浅灰蓝白，页面底色保持安静
- 卡片：白色或轻微透明白，边框清晰，阴影更浅
- 圆角：业务工作台卡片统一 12px 到 16px，模态/大面板可到 20px，避免 24px 到 28px 泛滥
- 字体：继续使用系统 sans；删除无实际收益的过度 `tracking-tight` 和负间距倾向
- 图标：继续使用 `lucide-react`，但图标按钮必须有 tooltip 或清晰 aria-label

### 4.3 产品布局原则

- 首屏就是工作台，不做营销式 hero。
- 页面标题区用于定位和关键操作，不承载过多装饰。
- 表格、列表、筛选器、工作流步骤优先服务扫描、比较和批量操作。
- 移动端不强求展示所有桌面信息，改为“摘要卡片 + 展开详情 + 固定主操作”。
- 报告阅读、PDF 解析和智能体聊天是三类高价值场景，应优先优化。

## 5. 关键问题清单

### P0：必须先统一设计基础

1. 全局样式系统过于分散  
   `index.css` 中同时维护大量具体页面样式，页面 class 与组件 class 边界不清。后续继续加页面会让 UI 进一步碎片化。

2. 卡片体系混乱  
   `premium-shell`、`premium-card`、`apple-card`、`apple-panel`、`secondary-panel`、legacy Card 都在承担类似职责，圆角从 16px 到 28px 不等。

3. 页面标题区不一致  
   多数页面使用 `secondary-hero`，但具体内容结构、右侧 step chip、company card、筛选区位置差异较大。

4. 响应式策略不成体系  
   有些页面移动端横向滚动，有些改为卡片，有些隐藏关键内容。需要统一断点和组件级策略。

### P1：核心页面体验需要重排

1. 工作平台 `MyWorkspace`  
   当前视觉最接近目标，但 hero、插画、统计卡、当前项目卡和流程卡的信息层级略拥挤。移动端横向流程卡可以更像快捷入口，而不是缩小桌面卡。

2. 搜索下载 `SearchDownload`  
   页面功能强，但智能检索、传统筛选、快捷下载、结果表、下载栏、日志、已下载文件全部堆在同一纵向流里。需要拆成更清晰的“查询区、候选区、操作区、历史区”。

3. PDF 解析 `PdfParsing`  
   工作台能力强，但上传、配置、任务、Markdown、财务表、质量检查、溯源区视觉密度不均。应形成两栏或三栏可折叠工作台。

4. 报告查看 `ReportViewer`  
   报告阅读和右侧智能体聊天是产品亮点，但当前在桌面端存在固定高度滚动区嵌套，移动端助手浮层与内容切换需要更自然。

5. 聊天助手 `ChatBot` / `AgentChatPanel`  
   全局聊天和页面智能体聊天有两套外观，头像尺寸、消息气泡、composer、历史面板、按钮组需要合并为统一聊天系统。

### P2：细节质量与可访问性

1. 图标按钮有 aria-label，但 hover tooltip 不统一。
2. 部分控件高度和圆角不统一，按钮、select、input 有 `form-control`、Radix UI、新旧 Button 多套。
3. 表格在移动端主要依赖横向滚动，应为关键表格提供卡片视图。
4. 加载、空状态、错误状态视觉不统一，影响用户判断。
5. 需要补充 Playwright 或手工截图验收，覆盖桌面、平板、手机宽度。

## 6. 目标信息架构

### 6.1 全局壳层

桌面端：

- 左侧 Sidebar：一级模块导航，支持折叠；折叠时只显示图标和 tooltip。
- 顶部 Topbar：全局搜索、通知、账户、退出；不要挤占主内容。
- 主内容区：最大宽度继续控制在 1680px 左右，但复杂工作台页面可使用满宽内容区。
- 全局聊天：非智能体专属页面保留右下角入口，但避免遮挡固定操作栏。

移动端：

- Topbar 保持 60px 左右。
- Sidebar 用抽屉，不占常驻空间。
- 搜索改为全屏 command palette。
- 全局聊天入口与页面主操作按钮要避让。

### 6.2 页面类型

建议抽象 5 类页面模板：

1. `WorkspacePage`：工作台首页，重视总览和快捷入口。
2. `WorkflowPage`：搜索下载、PDF 解析、市场解析，重视步骤、任务状态和批量操作。
3. `ReaderPage`：报告阅读，重视沉浸阅读和右侧智能体。
4. `AdminPage`：用户管理、设置、向量入库，重视表格、筛选、状态。
5. `AuthPage`：登录注册，重视品牌、信任和低摩擦。

## 7. 可落盘任务拆解

### Phase 1：设计系统收敛

目标：先把基础 UI 语言收住，减少后续页面各改各的。

任务：

- 新建或整理设计 token 文档，建议路径：`apps/web/src/styles/tokens.css` 或继续在 `index.css` 中分区维护。
- 定义统一 radius：
  - `--radius-control: 10px`
  - `--radius-card: 14px`
  - `--radius-panel: 18px`
  - `--radius-modal: 20px`
- 定义统一 shadow：
  - `--shadow-card`
  - `--shadow-panel`
  - `--shadow-popover`
  - `--shadow-focus`
- 合并卡片类：
  - `surface-card`
  - `surface-panel`
  - `surface-row`
  - `surface-muted`
- 保留 `premium-*` 作为兼容别名，但新代码不要继续扩散。
- 建立统一控件尺寸：
  - small：32px
  - default：40px
  - large：44px
  - touch：48px
- 统一 `Button`、`Input`、`Select`、`Textarea`、`Badge`、`Tabs`、`Dialog` 的视觉。
- 梳理 `components/ui/legacy` 使用点，逐步替换为新 UI 组件。

验收：

- 新增页面不再直接使用 `apple-card`、`apple-panel`。
- 核心页面中 80% 以上卡片圆角落在 14px 到 18px。
- `npm run build` 通过。

### Phase 2：全局壳层优化

目标：让应用第一眼更像成熟桌面工作台。

涉及文件：

- `components/layout/Layout.tsx`
- `components/layout/Sidebar.tsx`
- `components/layout/Topbar.tsx`
- `components/layout/GlobalSearch.tsx`
- `components/layout/NotificationMenu.tsx`
- `components/layout/layoutData.ts`
- `src/index.css`

任务：

- Sidebar 视觉收敛：减少大阴影和强玻璃质感，改为更稳定的白色侧栏或浅灰侧栏。
- 统一导航 active 状态：使用主色左侧条或浅蓝底，不建议 active 项完全黑底，除非品牌整体黑白化。
- 折叠 Sidebar 时确保每个图标都有 tooltip，当前已部分具备，需补齐底部工具项。
- Topbar 搜索框在桌面端保持足够宽度，在 1024px 到 1280px 时不要挤压账户和通知。
- 移动端 Topbar：菜单、搜索、通知、账户入口保持 44px 触控目标。
- GlobalSearch 结果面板统一为空状态、加载状态、分组标题和键盘焦点样式。
- NotificationMenu 移动端改为 bottom sheet 或全屏面板，避免小屏右侧弹层溢出。

验收：

- 视口 390x844、768x1024、1366x768、1440x900、1920x1080 均无横向溢出。
- Sidebar 打开/关闭时主内容无明显跳动。
- 键盘 Tab 可以访问菜单、搜索、通知、账户和退出。

### Phase 3：工作平台首页优化

目标：把 `MyWorkspace` 做成产品门面，但仍保持工具属性。

涉及文件：

- `pages/MyWorkspace.tsx`
- `public/illustrations/siq-system-map-hero.svg`
- `components/research/MetricCard.tsx`
- `components/research/InfoCard.tsx`

任务：

- 重新组织首屏为三层：
  - 顶部：用户问候、当前研究概况、主 CTA。
  - 中部：统计指标和当前优先研究对象。
  - 底部：最近任务、研究对象状态、公司概览。
- 插画不要占据过多纵向空间；桌面端可作为右侧或中部背景信息，移动端可隐藏或裁切为轻量 banner。
- 统计卡统一为紧凑指标，减少大字号造成的视觉噪音。
- Workflow steps 在桌面端用 6 个等宽快捷入口，移动端改为 2 列网格或水平 segmented actions。当前水平卡片滚动可保留，但需要加边缘渐隐提示或滚动提示。
- 当前优先研究对象卡增加明确下一步按钮和状态解释，避免只有数量。
- 近期任务列表移动端改为更紧凑的列表卡，标题两行截断，时间放次级位置。

验收：

- 桌面端首屏无需滚动即可看到工作台标题、核心统计和下一步行动。
- 移动端首屏能看到标题、一个主 CTA、2 到 4 个关键指标。
- 项目名称很长时不撑破布局。

### Phase 4：搜索下载页面重构

目标：把复杂检索流程变成清晰、可信、可批量操作的金融披露文件工作台。

涉及文件：

- `pages/SearchDownload.tsx`
- `src/index.css` 中 `.search-download-*`、`.smart-search-*`
- 可新增：`components/search-download/*`

建议拆分组件：

- `MarketSearchHero`
- `SmartSearchPanel`
- `ManualSearchForm`
- `QuickDownloadBar`
- `ReportCandidateTable`
- `ReportCandidateCardList`
- `SelectedDownloadBar`
- `DownloadResultList`
- `ProcessingLogPanel`
- `DownloadedReportLibrary`

任务：

- 把智能检索和手动检索做成同一张查询面板内的两个 Tab 或上下主次布局。
- 市场选择建议使用 segmented control，而不是两个重复 select。当前智能检索和手动检索各有市场 select，容易让用户困惑。
- 查询表单桌面端保持一行，但字段宽度更稳定；移动端每个字段独占一行。
- 候选报告桌面端使用表格，移动端使用卡片列表，不要只依赖横向表格滚动。
- 批量下载栏在桌面端 sticky bottom 或候选区顶部固定；移动端使用底部固定操作栏，显示已选数量和主按钮。
- 日志默认折叠，只有发生错误或用户展开时显示。
- 已下载财报文件模块可作为页面下方独立区域，移动端默认折叠搜索结果之外的历史区。
- 对 JP/KR 官方源配置缺失提示做成轻量状态条，不要在主查询区占过多空间。

验收：

- 用户可以在 3 秒内理解：选市场、输公司、选年份、查询、选择文件、下载。
- 移动端不用横向滚动也能完成搜索和下载。
- 下载中、成功、失败、缓存命中四种状态有统一视觉。

### Phase 5：PDF 解析工作台优化

目标：让解析工作流从“堆叠模块”变成“任务驱动工作台”。

涉及文件：

- `pages/PdfParsing.tsx`
- `components/pdf/*`
- `pages/pdf/pdfStyles.ts`

任务：

- 桌面端建议布局：
  - 左栏：上传/已下载文件/解析配置/任务列表
  - 主栏：Markdown 预览/财务表格/质量结果
  - 右栏或抽屉：PDF 源文档与溯源
- 移动端建议布局：
  - 顶部步骤条
  - 上传和选择文件
  - 当前任务状态
  - Tabs：结果、财务表、质量、溯源、日志
- 上传区应有明确拖拽状态、文件大小/数量限制、已选文件列表和移除按钮。
- 配置项默认折叠为“高级配置”，减少新用户压力。
- 任务进度条视觉统一，分为上传、解析、抽取、校验、完成。
- Markdown 与表格区域需要稳定高度，避免结果加载时页面大跳动。
- 溯源区需要有空状态：未选择单元格、已选择单元格、PDF 加载失败。

验收：

- 桌面端 1440x900 下不需要频繁上下滚动即可完成一次解析。
- 移动端 390x844 下可以完成上传、查看状态、查看结果。
- 任务取消、恢复、失败重试都有清楚按钮和反馈。

### Phase 6：报告阅读与智能体协作优化

目标：把分析、事实核查、持续跟踪、法务合规页面打造成高价值阅读与问答体验。

涉及文件：

- `components/report/ReportViewer.tsx`
- `components/report/ReportFrame.tsx`
- `components/report/ReportSelector.tsx`
- `components/report/ReportToolbar.tsx`
- `components/agent/PageWithAgentChat.tsx`
- `components/agent/AgentChatPanel.tsx`

任务：

- 桌面端报告页采用“报告阅读主栏 + 智能体侧栏”稳定布局。
- 右侧智能体宽度可调保留，但拖拽手柄视觉要更明显，键盘调整提示放到 tooltip 或 aria 描述里。
- 报告选择器、版本选择、分享、下载、删除建议放到 sticky toolbar，滚动报告时仍可操作。
- 报告 iframe 外层减少卡片嵌套，增加阅读区域留白和固定高度。
- 空报告状态提供直接行动：返回解析、生成分析、选择其他公司。
- 移动端报告页不要同时显示 iframe 和聊天面板；使用底部 Tab 或浮层切换“报告 / 助手 / 信息”。
- 智能体上下文信息卡要更明确展示当前公司、报告类型、更新时间。

验收：

- 桌面端用户可以边看报告边提问，不遮挡内容。
- 移动端打开助手后可以关闭并回到同一阅读位置。
- 报告加载失败、无报告、无公司三类状态可区分。

### Phase 7：聊天系统统一

目标：统一全局助手和页面智能体助手的体验。

涉及文件：

- `components/chat/ChatBot.tsx`
- `components/agent/AgentChatPanel.tsx`
- `components/chat/MessageRenderer.tsx`
- `components/chat/SessionHistoryList.tsx`
- `components/chat/ChatAttachmentList.tsx`
- `components/chat/ClearChatConfirmDialog.tsx`

任务：

- 抽象共用组件：
  - `ChatShell`
  - `ChatHeader`
  - `ChatMessageList`
  - `ChatComposer`
  - `ChatQuickQuestions`
  - `ChatHistoryPanel`
- 全局助手和页面助手使用同一套消息气泡、composer、附件、复制按钮、历史列表。
- 消息气泡控制最大宽度：用户消息 80% 到 84%，助手消息 92% 到 96%，移动端自动收紧。
- Markdown 表格在聊天中移动端要可横向滚动，代码块有复制按钮。
- Quick questions 不要在小屏上挤成很小的 chip，建议 1 列或 2 列。
- 发送按钮、停止按钮、附件按钮在 composer 中位置固定，避免 textarea 自动增高时错位。
- 全局聊天浮窗移动端建议全屏或接近全屏 bottom sheet，而不是固定 400px 宽小窗。

验收：

- 两套聊天入口视觉一致，只在品牌头像和标题上区分角色。
- 流式回答时滚动位置稳定，用户手动向上滚动时不要强制拉回底部。
- 附件上传、删除、发送中、停止生成都有清晰状态。

### Phase 8：认证页优化

目标：登录注册页更有品牌质感，但仍快速进入系统。

涉及文件：

- `pages/Login.tsx`
- `pages/Register.tsx`
- `src/index.css` 中 `.auth-*`

任务：

- 登录卡片保持简洁，不要放过大的视频占据首屏。视频可改为可展开的产品预览或右侧/下方小预览。
- 移动端默认隐藏视频，保留“快速了解 SIQ 工作流”按钮。
- 登录页增加环境/版本信息，但不要干扰主流程。
- 表单错误状态靠近对应输入框，同时保留顶部 alert。
- 注册成功页和登录页视觉统一。

验收：

- 390x844 下不滚动即可看到用户名、密码、登录按钮和注册入口。
- 键盘弹出时输入框不被遮挡。

### Phase 9：管理与系统页面统一

目标：让用户管理、设置、向量入库、帮助页看起来像同一个产品。

涉及文件：

- `pages/UserAdmin.tsx`
- `pages/UserDetail.tsx`
- `pages/Settings.tsx`
- `pages/VectorIngest.tsx`
- `pages/Help.tsx`
- `components/sec/UsSecIngestionPanel.tsx`

任务：

- 统一 AdminPage 页面头、筛选条、表格、移动卡片、分页和状态徽标。
- 表格桌面端保留密度，移动端改为卡片。
- 危险操作按钮统一红色视觉和确认弹窗。
- 代码/命令块使用统一 dark code block，支持复制。
- 帮助页和设置页避免过多大卡片，改为可扫描信息分组。

验收：

- 管理页桌面端能高效扫描，移动端能完成关键查看和操作。
- 所有危险操作都有确认。

## 8. 组件与样式重构建议

建议新增目录：

```text
apps/web/src/components/shell/
apps/web/src/components/workspace/
apps/web/src/components/search-download/
apps/web/src/components/pdf-workbench/
apps/web/src/components/chat-core/
apps/web/src/components/page/
apps/web/src/styles/
```

建议保留并增强：

- `components/ui/button.tsx`
- `components/ui/input.tsx`
- `components/ui/select.tsx`
- `components/ui/tabs.tsx`
- `components/ui/dialog.tsx`
- `components/ui/tooltip.tsx`
- `components/ui/badge.tsx`

建议逐步淘汰：

- `components/ui/legacy/*`
- 新增页面内直接写复杂 CSS class 的做法
- `apple-card`、`apple-panel` 作为新开发依赖

建议引入的通用页面组件：

```tsx
<PageShell />
<PageHeader />
<PageToolbar />
<PageSection />
<SurfaceCard />
<SurfacePanel />
<StatusBadge />
<EmptyState />
<ErrorState />
<LoadingState />
<ResponsiveTable />
<MobileRecordCard />
<StickyActionBar />
```

## 9. 桌面端专项要求

适配宽度：

- 1366x768
- 1440x900
- 1536x864
- 1920x1080

要求：

- 主内容最大宽度合理，不在大屏上无限拉长文本行。
- 搜索下载、PDF 解析、报告阅读应充分利用横向空间。
- 表格列宽可读，长标题截断后有 tooltip 或 title。
- 右侧智能体面板不应让主内容小于 760px。
- 顶部栏和侧边栏 fixed 布局不遮挡内容。
- 弹层不超出视口，通知、搜索、下拉菜单在右侧有足够边距。

## 10. 移动端专项要求

适配宽度：

- 360x800
- 390x844
- 414x896
- 430x932

要求：

- 全站无页面级横向滚动。
- 表单控件触控目标不小于 44px。
- 主要 CTA 在小屏上宽度 100%，或放入底部 sticky action bar。
- 复杂表格必须提供移动卡片视图。
- 弹层使用全屏、bottom sheet 或居中 modal，不能从右侧溢出。
- 聊天、通知、搜索、侧边栏这些全局浮层之间不能互相遮挡。
- `safe-area-inset-*` 已有基础使用，新增固定底部操作区必须继续考虑安全区域。

## 11. 交互状态规范

每个核心组件必须覆盖：

- 默认状态
- hover 状态
- focus-visible 状态
- active/pressed 状态
- disabled 状态
- loading 状态
- empty 状态
- error 状态
- success 状态

具体要求：

- 所有按钮 loading 时保留原宽度或最小宽度，避免布局跳动。
- 所有异步列表要有 skeleton 或稳定高度 loading 区域。
- 错误信息应提供恢复动作，例如重试、返回、选择其他项。
- 删除、清空、覆盖类操作必须二次确认。

## 12. 可访问性要求

- 页面必须有且只有一个主 `h1`。
- 图标按钮必须有 `aria-label`，复杂图标按钮还需 tooltip。
- 表单 label 必须和 input/select 关联。
- 弹窗打开后焦点进入弹窗，关闭后回到触发按钮。
- 键盘用户可以完成搜索、选择报告、下载、打开助手、发送消息。
- 颜色不能作为唯一状态表达，状态徽标需配合文字或图标。
- 文本对比度满足常规可读性。

## 13. 验收截图清单

每一阶段提交前至少提供以下截图或 Playwright 截图：

- 登录页：390x844、1440x900
- 工作平台：首页 390x844、1440x900、1920x1080
- 搜索下载：初始态、搜索结果态、下载中、下载结果、移动端卡片态
- PDF 解析：初始态、上传中、解析完成、溯源打开、移动端 tabs
- 报告阅读：有报告、无报告、报告加载失败、助手打开、移动端助手打开
- 全局搜索：桌面弹层、移动全屏
- 通知菜单：桌面弹层、移动面板
- Sidebar：展开、折叠、移动抽屉

## 14. 自动化与验证命令

基础验证：

```bash
cd /home/maoyd/siq-research-engine/apps/web
npm run build
npm run lint
```

本地启动：

```bash
cd /home/maoyd/siq-research-engine/apps/web
npm run dev -- --host 0.0.0.0 --port 15173
```

全栈启动参考：

```bash
cd /home/maoyd/siq-research-engine
./start_all.sh
```

建议补充 Playwright 脚本，至少覆盖：

- 首页无横向滚动
- 搜索下载移动端可完成查询表单输入
- 报告页助手打开关闭
- 全局搜索移动端打开关闭
- Sidebar 移动抽屉打开关闭

## 15. 推荐执行顺序

1. Phase 1：设计系统收敛
2. Phase 2：全局壳层优化
3. Phase 7：聊天系统统一
4. Phase 6：报告阅读与智能体协作优化
5. Phase 4：搜索下载页面重构
6. Phase 5：PDF 解析工作台优化
7. Phase 3：工作平台首页优化
8. Phase 8：认证页优化
9. Phase 9：管理与系统页面统一

原因：

- 先统一基础组件和壳层，可以避免每个页面重复返工。
- 聊天和报告阅读是产品差异化体验，应尽早统一。
- 搜索下载和 PDF 解析复杂度最高，建议在基础稳定后重构。
- 首页最后精修更容易吸收新组件体系成果。

## 16. 非目标

本任务书不要求：

- 重写业务接口。
- 改变后端数据结构。
- 更换技术栈。
- 引入大型 UI 框架替代现有 Tailwind/Radix/lucide 组合。
- 做营销落地页。
- 为了视觉效果牺牲工作台密度和可操作性。

## 17. Definition of Done

完成优化后应满足：

- 全站视觉统一，核心页面不像不同批次拼接。
- 桌面端适合长时间研究工作，信息密度合理。
- 移动端能完成关键流程，不再只是缩小桌面页。
- 登录、工作台、搜索下载、PDF 解析、报告阅读、聊天助手均有高质量空态、加载态、错误态。
- 核心浮层、抽屉、弹窗不会溢出视口。
- `npm run build` 和 `npm run lint` 通过。
- 提供验收截图或 Playwright 报告。
