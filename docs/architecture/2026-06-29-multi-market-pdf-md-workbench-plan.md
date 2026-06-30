# 多市场财报复核对照前端复刻方案

日期：2026-06-29

适用仓库：

```text
/home/maoyd/siq-research-engine
```

## 0. 修订结论

本方案只替换财报解析复核页面中截图所示的“对照工作台”部分，即 `PdfSourceWorkbench` 里的：

```text
阅读视图 / 当前 PDF 页 / 当前表格
PDF 原页 / 页码 / 缩放 / bbox 高亮
```

使用通用文档解析页面已经验证过的“PDF 原页 + 渲染内容 + bbox overlay + 双栏同步”的前端体验来改造这块 UI。

不替换、不移动、不改造以下内容：

- 上传区。
- 解析状态与日志。
- 解析结果区。
- Markdown 结果 `PdfMarkdownPreview`。
- 产物清单。
- 质量报告。
- 财务抽取与校验。
- 入库工作流。
- 最近任务列表。
- 后端解析、抽取、校验、页码推断、bbox 生成和任何 API。

也就是说：只把截图里的复核对照区域换成通用文档解析那套更高精度、更完整的前端对照体验；其他内容完全保持原样。

### 0.1 2026-06-30 状态复核

本方案仍定位为前端复核对照体验改造方案，不承接后端解析、质量报告、财务抽取或任务状态拆分。

当前相关工程基线：

- `PdfParsing` / 多市场解析入口已共用 workbench，route registry 与导航/preload 已单源化。
- `PdfSourceWorkbench.tsx` 仍是前端剩余大组件之一，约 1842 行；后续 UI 精修前，建议先按 `F-004` 将 compare pane、reading pane、PDF page pane、review correction pane、artifact pane 拆出。
- `cd apps/web && npm run lint` 通过。
- `cd apps/web && npm run build` 通过。

因此，本方案下一步不应直接大改样式；优先做组件边界拆分，再进行视觉复刻和 bbox overlay 精修。

## 1. 覆盖范围

财报 PDF 解析的多市场入口共用 `PdfParsing` 页面和 `PdfSourceWorkbench` 复核组件，因此该前端替换可覆盖：

| 市场 | 说明 |
| --- | --- |
| CN 中国内地市场 | SSE / SZSE / BSE，A 股 PDF 财报复核 |
| HK 香港市场 | HKEX / SEHK，港股 PDF 财报复核 |
| US 美国市场 | NYSE / Nasdaq / Cboe / OTC 中的 PDF 附件或 IR PDF 复核 |
| EU 欧洲市场 | LSE / Euronext / Xetra / SIX 中的 PDF 年报路径复核 |
| KR 韩国市场 | KRX / KOSPI / KOSDAQ / KONEX PDF 路径复核 |
| JP 日本市场 | TSE / OSE / NSE / SSE / FSE PDF 路径复核 |

不覆盖非 PDF 主链路：

- SEC HTML / iXBRL。
- 欧股 ESEF ZIP / XHTML / iXBRL。
- DART HTML/XML。
- EDINET HTML/XML。
- 任何没有 PDF 页图的任务。

## 2. 当前前端事实

截图对应的是：

```text
apps/web/src/components/pdf/PdfSourceWorkbench.tsx
```

它已经具备：

- 左侧阅读视图。
- 当前 PDF 页 / 当前表格切换。
- 右侧 PDF 原页图。
- 页码切换。
- 缩放。
- 打开原页图片。
- 表格 bbox 高亮。
- 单元格 trace 高亮。
- 下方人工复核修正。
- Markdown 上下文。
- 产物文件。

所以这不是从零新增对照页，而是把现有复核对照区域的前端呈现方式升级为通用文档解析的工作台体验。

可复用的数据和接口保持不变：

```text
GET /api/pdf/source/:taskId/table/:tableIndex
GET /api/pdf/source/:taskId/page/:page?focus_table=
GET /api/pdf/pdf_page/:taskId/:page
```

现有 hook 保持不变：

```text
apps/web/src/pages/pdf/usePdfSourceTrace.ts
apps/web/src/pages/pdf/useEditableTable.ts
```

## 3. 目标

### 3.1 产品目标

1. 复核区域看起来、用起来与通用文档解析的对照工作台一致。
2. PDF 原页更清晰，bbox overlay 更稳定，缩放和页码控制更顺手。
3. 左侧阅读视图从现在的卡片式页面块，收敛成更接近 `document.md` 渲染结果的阅读面板。
4. 表格级复核入口、单元格 trace、人工修正保存全部保留。
5. 移动端仍用“阅读视图 / PDF 原页”切换，不把双栏硬挤到小屏。

### 3.2 非目标

明确不做：

- 不替换 `PdfMarkdownPreview`。
- 不新增 `PdfMarkdownWorkbench`。
- 不移动“解析结果”区。
- 不改变 `PdfParsing.tsx` 页面结构，除非只是传入新组件所需的展示 props。
- 不改 `apps/pdf-parser/app.py`。
- 不改 `/api/pdf/*` 返回字段。
- 不改 `financial_data.json`、`financial_checks.json`、`quality_report.json`。
- 不改表格纠错保存逻辑。
- 不改多市场 Tab、任务筛选和市场归属逻辑。

## 4. 推荐落盘方式

### 4.1 最小改造路径

优先只改：

```text
apps/web/src/components/pdf/PdfSourceWorkbench.tsx
apps/web/src/pages/pdf/pdfStyles.ts
```

保留：

```text
apps/web/src/pages/PdfParsing.tsx
apps/web/src/pages/pdf/usePdfSourceTrace.ts
apps/web/src/pages/pdf/useEditableTable.ts
apps/web/src/lib/pdfApi.ts
```

只有当 `PdfSourceWorkbench` 需要拆组件时，再新增：

```text
apps/web/src/components/pdf/PdfReviewComparePane.tsx
```

这个新增组件只承接 `PdfSourceWorkbench` 内部的上半部分双栏对照，不接管人工复核修正和产物文件。

### 4.2 建议组件边界

建议将 `PdfSourceWorkbench` 里截图对应区域拆成：

```text
PdfSourceWorkbench
  PdfReviewComparePane
    ReviewReadingPane
    ReviewPdfPagePane
  人工复核修正
  Markdown 上下文
  产物文件
```

其中：

- `PdfSourceWorkbench` 的 props 不变。
- `PdfReviewComparePane` 只做前端展示和交互。
- `ReviewReadingPane` 使用现有 `readingHtml`。
- `ReviewPdfPagePane` 使用现有 `pageBlobUrl`、`overlays`、`pdfCurPage`、`pdfZoom`、`changePage`。
- 下方“人工复核修正 / Markdown 上下文 / 产物文件”保持原结构和行为。

## 5. 复刻通用文档解析哪些体验

从 `DocumentResultWorkbench` 复刻前端体验，而不是业务数据合同：

| 通用文档解析体验 | 财报复核落点 |
| --- | --- |
| PDF page card 标题栏 | 右侧 PDF 原页 pane 的标题栏 |
| Authenticated image blob 加载态 | 继续用 `useAuthenticatedBlobUrl`，但视觉状态对齐 |
| bbox overlay 分层 | 右侧 PDF 页图上的表格框、单元格框 |
| 稳定 bbox 百分比定位 | 保留现有 `bboxExtent`，收敛样式和层级 |
| 左右 pane 高度一致 | `pdf-workbench` 双栏统一高度 |
| 移动端 pane 切换 | 保留现有 mobile tab，改成更一致的 segmented control |
| 空态/错误态 | 页图缺失、readingHtml 为空时给出一致空态 |

不复刻：

- 通用文档解析的 `table_relations`。
- 跨页虚线。
- 通用文档解析的 artifacts 合同。
- 文档解析独立 tab 结构。

## 6. 具体 UI 调整

### 6.1 双栏布局

现状截图：

```text
左：阅读视图
右：PDF 原页
```

保持这个结构，但样式对齐通用文档解析：

- 外层不再显得像两个孤立卡片，改成一个统一复核工作台。
- 左右 pane 用同一标题栏、边框、背景和滚动高度。
- 桌面端两栏 `minmax(0, 1fr)`，右侧 PDF 原页可略宽。
- 移动端保留单列 tab 切换。

### 6.2 阅读视图

保留现有两种模式：

```text
当前 PDF 页
当前表格
```

但视觉改成通用文档解析的渲染块风格：

- 页面摘要更紧凑。
- 表格块不再像大号报告卡，改成可扫描的阅读块。
- 当前表格/焦点表格保持高亮。
- 表格横向滚动保留，使用统一 `.scroll-hint`。
- “打开该表”按钮保留，继续触发 `sourceTrace.showTableSource(index)`。

### 6.3 PDF 原页

保留现有功能：

- 页码。
- 上一页/下一页。
- 输入页码。
- 缩放：适应宽度、100%、150%、200%。
- 打开原页图片。
- bbox overlay。

视觉调整：

- 页图容器、工具条、图片加载态对齐通用文档解析。
- bbox 高亮层级更清晰：表格框、单元格框、文本锚定框有不同样式。
- overlay 不遮挡过多原文，默认轻色透明。
- 缩放按钮改为紧凑 segmented controls。

### 6.4 人工复核区

保持原样。

只允许做低风险视觉收敛：

- 不改字段。
- 不改保存按钮。
- 不改默认值。
- 不改 `review_status` 枚举。
- 不改 textarea 内容来源。

### 6.5 Markdown 上下文与产物文件

保持原样。

可以跟随外层样式做轻微边距/边框统一，但不改功能。

## 7. 文件级实施计划

### Phase 1：复核对照 UI 替换

改动文件：

```text
apps/web/src/components/pdf/PdfSourceWorkbench.tsx
apps/web/src/pages/pdf/pdfStyles.ts
```

动作：

1. 保留 `PdfSourceWorkbenchProps` 不变。
2. 将上半部分 `.pdf-workbench` 重构成接近通用文档解析的双 pane 布局。
3. 保留 `readingHtml` 的 `dangerouslySetInnerHTML` 渲染入口。
4. 保留 `pageBlobUrl` 和 `overlays` 生成逻辑。
5. 保留移动端 `mobileTab`。
6. 不动下半部分人工复核和产物区。

验收：

- 截图区域外观变成通用文档解析风格。
- 表格打开、翻页、缩放、bbox 高亮、单元格 trace、保存修正均可用。
- `PdfMarkdownPreview` 没有变化。

### Phase 2：阅读视图细节增强

改动文件：

```text
apps/web/src/pages/pdf/usePdfSourceTrace.ts
apps/web/src/components/pdf/PdfSourceWorkbench.tsx
apps/web/src/pages/pdf/pdfStyles.ts
```

动作：

1. 仅在必要时微调 `renderBlock` 输出的 class，不改数据。
2. 让当前页阅读块更像 Markdown 渲染结果。
3. 改进 focus table 的视觉位置和滚动体验。

验收：

- 当前 PDF 页模式仍能打开该页任意表格。
- 当前表格模式仍可编辑并同步到修正 textarea。

### Phase 3：可选 overlay 精修

改动文件：

```text
apps/web/src/components/pdf/PdfSourceWorkbench.tsx
apps/web/src/pages/pdf/pdfStyles.ts
```

动作：

1. 对表格 bbox、单元格 bbox、文本锚定 bbox 使用更清晰的视觉层级。
2. 增加 hover/focus 状态。
3. 保证 overlay 在 100%、150%、200% 下仍对齐。

验收：

- 不同缩放比例 bbox 不漂移。
- overlay 不遮住重要文字。

## 8. 不允许改动清单

本任务不得改：

```text
apps/pdf-parser/app.py
apps/pdf-parser/financial_extractor.py
apps/pdf-parser/quality_engine.py
apps/web/src/pages/pdf/usePdfTasks.ts
apps/web/src/lib/pdfApi.ts
apps/web/src/components/pdf/PdfMarkdownPreview.tsx
apps/web/src/components/pdf/PdfQualityPanel.tsx
apps/web/src/components/pdf/PdfFinancialPanel.tsx
apps/web/src/components/pdf/PdfWorkflowPanel.tsx
```

除非只是类型导入或格式化被构建工具要求，否则不要碰。

## 9. Definition of Done

功能验收：

- A 股解析复核页截图区域替换为通用文档解析风格的对照前端。
- 港股、美股 PDF、欧股 PDF、韩股 PDF、日股 PDF 因共用组件同步获得同样体验。
- 当前 PDF 页 / 当前表格切换可用。
- 页码切换可用。
- 缩放可用。
- 打开原页图片可用。
- 表格 bbox 和单元格 trace 可用。
- 人工复核保存可用。
- 解析结果、抽取、校验、入库、任务列表没有 UI 和功能变化。

工程验收：

```bash
cd /home/maoyd/siq-research-engine/apps/web
npm run build
npm run lint
```

视觉验收：

- 1366x768、1440x900、1920x1080 下截图区域无横向溢出。
- 390x844、768x1024 下可切换阅读/PDF，按钮触控目标足够。
- PDF 页图加载前有稳定占位。
- 页图加载失败有明确错误态。
- 表格横向滚动只发生在表格容器内。

## 10. 最终状态

财报解析页面保持原有结构：

```text
上传
状态
解析结果
可视化溯源
最近任务
```

只有 `可视化溯源` 内截图所示的复核对照工作台被替换为通用文档解析的对照前端体验。

这样可以提高 A 股及多市场 PDF 财报复核的精度和可用性，同时把风险严格限制在前端展示层。
