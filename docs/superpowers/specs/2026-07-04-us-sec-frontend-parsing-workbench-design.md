# 美股 SEC 解析前端工作台设计

日期：2026-07-04
状态：方案已确认，等待文档复核
仓库：`/home/maoyd/siq-research-engine`

## 1. 目标

美股解析前端应和 A 股解析保持相同的用户心智：用户先看到已下载披露文件列表，选择目标文件，再点击“解析”，最后查看结果、溯源和数据管线状态。

美股的“解析”不是 PDF/OCR 解析。点击“解析”时，应从 `data/market-report-finder/downloads/US` 中选中的 SEC HTML/iXBRL/附件文件生成 Wiki evidence package，包括 Markdown、JSON、XBRL facts、metrics、quality report 和 source map 等文件。

本轮只聚焦 Wiki evidence package 和 PostgreSQL 入库前端。Milvus 不进入主流程，也不作为主按钮展示。

## 2. 当前上下文

现有前端有两条不同形态：

- A 股和 HK/JP/KR/EU PDF 入口主要复用 `MarketParsingPage`，围绕 PDF `task_id` 展示上传、已下载列表、解析状态、结果、溯源、任务和数据管线。
- 美股入口 `/parse-us` 使用 `UsSecIngestionPanel`，围绕 `ticker`、`package_path` 和 `case_set` 展示 US 下载目录、上传附件、证据包、勾稽、HTML/Markdown 对照以及 PostgreSQL/Milvus 操作。

这导致用户在 A 股和美股之间切换时，第一屏心智不一致。美股应保留 SEC HTML/iXBRL 后端逻辑，但前端结构应与 A 股解析工作台对齐。

## 3. 已确认产品口径

已确认的关键决策：

- 保留 `/parse-us` 作为美股主入口。
- `/parse?market=US` 仅保留为美股 PDF 附件、IR 年报、presentation、proxy 等 PDF 文件的兼容入口。
- `/parse-us` 顶部第一屏必须优先展示“已下载财报”列表，风格对齐 A 股截图中的列表。
- 列表行保留“选择”和“解析”按钮。
- 美股行内“解析”按钮语义为：从下载文件生成 md、JSON 等 evidence package 文件。
- 后端不伪装成 PDF task，不生成 PDF 页码型溯源。
- 本轮不考虑 Milvus 入库设计。

## 4. 推荐方案

采用“界面同构、后端适配”的方案。

用户看到的主流程和 A 股一致：

```text
已下载财报
  -> 选择披露文件
  -> 点击解析
  -> 生成解析产物
  -> 查看结果和溯源
  -> 导入 Wiki/PostgreSQL
```

工程上不把 SEC 主链路塞进 PDF parser：

```text
A 股解析按钮
  -> PDF parser
  -> task_id
  -> Markdown / table / financial / workflow status

美股解析按钮
  -> SEC package builder
  -> package_path / filing_id / parse_run_id
  -> sections / tables / XBRL facts / metrics / quality / PostgreSQL status
```

因此，前端应抽象共享的“已下载披露列表”展示和操作心智，但通过 market adapter 分别连接 PDF task API 与 US SEC package API。

## 5. 不采用的方案

### 5.1 继续保留当前独立 US 面板

只在 `UsSecIngestionPanel` 上做小修小补速度最快，但会继续保留 A 股与美股第一屏结构差异。用户进入美股页面后仍然先看到统计、侧栏和复杂操作，而不是最清晰的“下载文件 -> 解析”主链路。

### 5.2 把美股主链路并入 `/parse?market=US`

这样表面上统一，但会误导工程模型。美股主链路不是 `task_id + PDF page + table_index`，而是 `package_path + accession + section/html_anchor + xbrl context_ref`。强行并入 PDF task hook 会增加错误假设和后续维护成本。

## 6. 页面结构

`/parse-us` 保持市场 tab 和美股标题，但主体结构调整为与 A 股解析工作台同构。

### 6.1 顶部：已下载财报

第一屏放置“已下载财报”面板，样式对齐 A 股当前列表：

- 标题：`已下载财报`
- 说明：优先从搜索下载阶段保存的 SEC 披露文件开始；HTML/iXBRL/XML/ZIP 走结构化证据包解析。
- 搜索框：支持公司、ticker、CIK、form、文件名。
- 刷新按钮：重新读取 US 下载目录。
- 数量 badge：展示当前匹配数量。
- 文件行：左侧图标和文件信息，右侧 `选择`、`解析` 两个按钮。

每行建议展示字段：

- 公司名。
- ticker。
- form：10-K、10-Q、20-F、6-K 等。
- period_end。
- filing_date。
- 文件类型：HTML、XHTML、XML、XBRL、ZIP、PDF。
- 文件大小。
- 下载时间。
- 相对路径，移动端可截断。
- 解析状态。

解析状态建议：

- `未解析`：未发现对应 package。
- `证据包已生成`：已有 `manifest.json` 和核心 artifacts。
- `PostgreSQL 已入库`：能从 ingest report 或 DB status 确认可用。
- `质量警告`：quality 为 warning。
- `质量失败`：quality 为 fail。

### 6.2 中部：解析状态

A 股这里展示上传和 PDF 解析进度。美股对应展示 SEC package build job 状态。

阶段建议：

1. `源文件`：文件已选择，并能定位到 downloads/US。
2. `SEC 结构化解析`：抽取 sections、tables、XBRL raw facts。
3. `标准指标`：生成 normalized metrics、financial_data、financial_checks。
4. `质量校验`：生成 quality_report、source_map、extraction_warnings。
5. `证据包完成`：生成 manifest 和 package index 可被后续管线读取。

如果后端当前只能返回 job 的 stdout/stderr 而没有细粒度阶段，前端先用 job status 和最终 package summary 呈现阶段状态。后续再补更细的 progress event。

### 6.3 结果区

A 股结果区展示 Markdown、财务抽取和质量面板。美股结果区展示同等层级的 package 产物：

- Section Markdown 预览。
- Metrics 表：canonical name、value、unit、period、concept、context_ref。
- XBRL facts 样例或筛选表。
- Financial checks。
- Quality summary。
- Package artifact list。

结果区应以当前选中的下载文件或 package 为上下文。点击列表中的另一个文件后，结果区切换到该文件对应 package。

### 6.4 溯源区

A 股溯源是 PDF page、table index 和 bbox。美股溯源改为 SEC 原文和 XBRL 证据定位：

- 左侧：SEC 原始 HTML iframe 或安全文件预览。
- 右侧：Wiki section Markdown。
- 证据定位字段：`section_id`、`html_anchor`、`concept`、`context_ref`、`unit_ref`、`raw_fact_id`、`source_url`。
- 不展示 PDF page，也不伪造页码。

### 6.5 数据管线

美股数据管线区域视觉上对齐 A 股 `PdfWorkflowPanel`，但阶段和按钮使用 US 语义。

主阶段：

1. `解析产物包`：evidence package 是否齐备。
2. `Wiki 证据包`：`data/wiki/us_sec` package、company index、case set 是否齐备。
3. `PostgreSQL`：`siq_us.sec_us` 是否已导入。

本轮隐藏 Milvus 主按钮。可以在文案中说明“向量化阶段后续接入”，但不提供主操作。

## 7. 前端组件边界

推荐拆分成小组件，避免继续扩大 `UsSecIngestionPanel`：

- `DownloadedDisclosureList`：共享的已下载披露列表 UI，可被 A 股和 US 使用或逐步替换。
- `UsSecDownloadedListAdapter`：把 `loadDownloadedReportsApi('', 'US')` 返回值映射到列表行模型。
- `UsSecParseActions`：处理选择、解析、刷新、job 轮询。
- `UsSecPipelinePanel`：展示 US package/Wiki/PostgreSQL 阶段。
- `UsSecResultWorkbench`：展示 Markdown、metrics、quality、checks。
- `UsSecSourceWorkbench`：展示 SEC HTML 与 section/metric 溯源。

如果第一步希望控制风险，也可以先在 `/parse-us` 内实现 US 专用版本，结构上预留 adapter 接口，第二步再抽共享组件。

## 8. 前端数据模型

列表层使用统一行模型：

```ts
type DownloadedDisclosureRow = {
  id: string
  market: 'CN' | 'HK' | 'US' | 'EU' | 'JP' | 'KR'
  relativePath: string
  filename: string
  companyName?: string
  ticker?: string
  form?: string
  reportType?: string
  periodEnd?: string
  filingDate?: string
  fileType?: string
  sizeBytes?: number
  downloadedAt?: string
  parseStatus: 'unparsed' | 'building' | 'package_ready' | 'postgres_ready' | 'warning' | 'failed'
  packagePath?: string
}
```

US 选择态使用 package 上下文：

```ts
type UsSecParseContext = {
  selectedDownloadPath?: string
  selectedTicker?: string
  packagePath?: string
  filingId?: string
  parseRunId?: string
  jobId?: string
  packageDetail?: UsSecPackageDetail
  caseSetStatus?: UsSecCaseSetStatus
}
```

## 9. API 映射

现有接口可以支撑第一版：

- 加载 US 下载列表：`loadDownloadedReportsApi(text, 'US')`
- 构建 US evidence package：`buildUsSecPackage({ download_relative_path, force: true })`
- 轮询后台 job：`fetchMarketReportJob(jobId)` / `waitForMarketReportJob(jobId)`
- 加载 case set 状态：`fetchUsSecCaseSet()`
- 加载 package detail：`fetchUsSecPackage(ticker)` 或通用 `fetchMarketPackageDetail('US', packagePath)`
- 导入 PostgreSQL：`runUsSecCaseSetIngest({ postgres: true, ddl: true, include_fail, tickers })` 或通用 `runMarketPackageImport('US', packagePath, ddl)`
- 读取 package 文件：`usSecPackageFileUrl(packagePath, file)` / `marketPackageFileUrl('US', packagePath, file)`

第一版优先复用已有接口，避免为了前端统一先做大规模后端重构。后续可以补一个统一 workflow status endpoint，把 US package、Wiki 和 PostgreSQL 状态一次性返回。

## 10. 错误处理

前端需要明确区分几类错误：

- 源文件不存在或文件类型暂不支持。
- 构建 package 失败。
- package 已生成但质量为 warning/fail。
- PostgreSQL 导入失败。
- job 超时。
- package 文件存在但无法预览。

错误展示原则：

- 行级错误显示在对应下载文件行。
- job 级错误显示在解析状态面板。
- package 质量问题显示在结果区和数据管线状态中。
- 不把 quality warning 当成前端异常，仍允许查看和导入，但需要显式标识。

## 11. 测试策略

前端单元测试：

- US 下载文件映射为统一列表行模型。
- `解析` 按钮调用 `buildUsSecPackage`，不调用 PDF parser。
- 不同 package/quality 状态映射到正确 badge。
- Milvus 按钮在本轮主流程中不可见。

E2E 测试：

- 访问 `/parse-us`，第一屏出现“已下载财报”列表。
- US 行展示 `选择` 和 `解析`。
- 点击 `解析` 后请求 `/api/market-reports/packages/build`，payload 包含 `market: 'US'` 和 `download_relative_path`。
- 构建成功后展示 package counts、Markdown/HTML 对照和 PostgreSQL 数据管线。
- 访问 `/parse?market=US` 仍只作为 PDF 兼容入口，且只展示 US PDF task。

## 12. 验收标准

第一版完成后，应满足：

- `/parse-us` 顶部第一屏和 A 股解析页一样先展示已下载列表。
- 美股列表行有清晰的 `选择` 和 `解析` 按钮。
- 美股点击 `解析` 生成 md、JSON、XBRL facts、metrics、quality 等 evidence package 文件。
- 美股解析不调用 PDF parser，不创建 PDF task，不伪造 PDF 页码。
- 选中 package 后能查看 SEC 原始 HTML、Wiki Markdown、metrics、quality 和 financial checks。
- 数据管线区域展示 Wiki evidence package 和 PostgreSQL 状态。
- Milvus 不在本轮主流程展示。
- A 股 `/parse` 现有行为不变。

## 13. 后续扩展

后续可在不改变第一屏心智的基础上继续增强：

- 支持批量选择多份 US 下载文件并批量生成 package。
- 增加更细粒度的 package build progress events。
- 增加统一 market workflow status endpoint。
- 接回 Milvus 向量化阶段。
- 把 A 股/HK/US 的已下载披露列表真正抽成共享组件。
