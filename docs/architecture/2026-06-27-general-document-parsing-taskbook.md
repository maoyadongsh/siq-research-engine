# 通用文档解析后端与前端开发计划书

日期：2026-06-27

适用仓库：

```text
/home/maoyd/siq-research-engine
```

## 0. 结论

新增独立一级入口：

```text
文档解析
```

不要把通用文档解析塞进现有“财报解析”内部。现有财报解析已经是市场、公司、期间、财务表格、勾稽校验和 evidence package 的专业研究链路；通用解析应服务任意 PDF、Office、图片、HTML、网页 URL、研报、合同、法规、会议材料、说明书等文档，核心目标是把文件转成可读、可检索、可抽取、可溯源的 Markdown / JSON / 表格 / 图片 / RAG chunk / Schema JSON。

推荐产品结构：

```text
侧边栏
  财报解析
    A股 / 港股 / 美股 / 欧股 / 日股 / 韩股
  文档解析
    上传与解析
    任务管理
    结构化抽取
    结果库
```

推荐技术结构：

```text
apps/document-parser                         新增通用解析运行时服务
  -> local/runtime parser providers           本地 MinerU、LibreOffice、HTML reader、OCR 等适配
  -> cloud parser provider                    可选 MinerU 官方 API / 自建 API
  -> normalized document artifacts            统一文档产物合同

apps/api/routers/document_parser.py           新增鉴权、额度、代理和工作区记录

apps/web/src/pages/DocumentParsing.tsx        新增前端工作台
apps/web/src/components/document-parser/*     新增通用解析组件族

data/document-parser/                         新增运行态数据目录
data/wiki/documents/                          可选通用文档知识库归档
document_parser PostgreSQL schema             可选结构化任务和产物索引
siq_documents Milvus collection               可选通用文档向量索引
```

## 1. 官方 MinerU 能力参考

本计划参考以下官方资料和可观察交互。实现时以本项目私有部署、安全和证据链要求为准，不照搬 MinerU 产品外壳。

参考链接：

- MinerU 在线文档解析：<https://mineru.net/OpenSourceTools/Extractor>
- MinerU API 文档：<https://mineru.net/apiManage/docs>
- MinerU 输出文件格式文档：<https://opendatalab.github.io/MinerU/reference/output_files/>
- MinerU GitHub：<https://github.com/opendatalab/MinerU>

### 1.1 能力模型

MinerU 官方能力对本项目有四点直接启发：

1. 输入格式不应只限 PDF，应覆盖 PDF、图片、DOC/DOCX、PPT/PPTX、XLS/XLSX、HTML 和网页 URL。
2. 输出不应只限 Markdown，应同时保留 Markdown、阅读顺序 JSON、版面中间结果、图片、表格、公式、可视化调试文件和导出包。
3. 参数不应只放一个“解析方式”，应有模型版本、OCR、表格识别、公式识别、语言、页码范围、缓存策略、额外导出格式等独立控制。
4. UI 不应只有上传按钮，应包含任务管理、批量上传、批量导出、解析状态、左右分屏预览、源文档和 Markdown/JSON 互相定位。

### 1.2 官方 API 值得吸收的参数

通用解析的参数建议对齐以下概念：

```json
{
  "model_version": "pipeline|vlm|MinerU-HTML|auto",
  "is_ocr": false,
  "enable_formula": true,
  "enable_table": true,
  "language": "auto|ch|en|ja|ko|...",
  "page_ranges": "1-20,24,30-32",
  "extra_formats": ["docx", "html", "latex"],
  "no_cache": false,
  "data_id": "business-stable-id"
}
```

本项目不要直接暴露所有上游字段给用户。前端用更稳定的 SIQ 配置语义，后端 provider 负责映射到 MinerU、本地解析器或未来其他解析服务。

### 1.3 官方输出文件值得吸收的合同

MinerU 输出侧强调：

- `middle.json`：页面级、版面级、块级中间结构。
- `content_list.json`：按阅读顺序扁平化的内容块，适合后处理和 RAG。
- `model.json`：模型推理结果，适合排障。
- `layout.pdf` / `span.pdf`：可视化调试文件，适合人工质检。
- Markdown：人可读主结果。
- 表格 HTML、公式 LaTeX、图片块和 captions：适合保真呈现。

SIQ 通用解析应把这些产物归一为自己的 `document_full.json`、`blocks.json`、`source_map.json` 和 `quality_report.json`，但保留原始上游产物，方便回放、升级和问题诊断。

## 2. 当前 SIQ 可复用资产

### 2.1 后端已有资产

现有 `apps/pdf-parser` 已经具备：

- SQLite 任务队列。
- 上传、去重、任务状态、日志、取消、重试、重新拉取。
- MinerU / VLM 上游调用。
- Markdown 产物缓存。
- `content_list_enhanced.json`、`document_full.json`、`quality_report.json`。
- PDF 页码、表格索引、页面图片、表格溯源 API。
- 财务抽取：`financial_data.json`、`financial_checks.json`。
- 前端代理：`apps/api/routers/workspace.py` 中 `/api/pdf/*` 鉴权代理、额度记录、用户产物记录。

### 2.2 前端已有资产

现有 `apps/web` 已经具备：

- 页面 primitives：`PageShell`、`PageHeader`、`PageSection`、`Surface`。
- PDF 解析页布局：`PdfParsing.tsx`、`MarketParsingPage.tsx`。
- 上传面板：`PdfUploadPanel.tsx`。
- 任务列表：`PdfTaskList.tsx`。
- Markdown 预览：`PdfMarkdownPreview.tsx`。
- 质量报告：`PdfQualityPanel.tsx`。
- 源文档工作台：`PdfSourceWorkbench.tsx`、`PdfPageViewer.tsx`。
- API hook：`usePdfTasks.ts`、`pdfApi.ts`。

这些资产可以作为 UX 和工程语义参考，但不能把通用文档强行接入财报语义，尤其不能默认出现“财务抽取”“三大表”“市场 evidence package”等概念。

### 2.3 必须隔离的内容

以下链路应保持独立：

```text
财报解析
  data/pdf-parser
  apps/pdf-parser
  /api/pdf/*
  financial_data / financial_checks
  market evidence package

通用文档解析
  data/document-parser
  apps/document-parser
  /api/documents/*
  generic document artifacts
  schema extraction / RAG chunks / document package
```

如需抽公共工具，先新增旁路工具包，不要直接重构 `apps/pdf-parser` 主链路：

```text
packages/document-core/ 或 libs/document_parser_core/
  artifact_contract.py
  source_map.py
  table_utils.py
  markdown_utils.py
  file_type.py
```

第一期建议复制少量稳定逻辑到新服务，等通用链路稳定后再做公共化，避免影响 A 股财报解析。

## 3. 产品目标和非目标

### 3.1 产品目标

通用文档解析第一阶段目标：

1. 用户可以上传 PDF、图片、Word、PPT、Excel、HTML，或输入网页 URL。
2. 用户可以选择解析模式、OCR、表格、公式、语言、页码范围和导出格式。
3. 系统生成 Markdown、结构化块 JSON、表格 JSON、图片、source map、质量报告。
4. 用户可以在同一工作台查看源文档、Markdown、JSON、表格、图片和抽取结果。
5. 用户可以下载单个产物或完整 zip。
6. 用户可以基于一个 JSON Schema 或模板抽取结构化结果。
7. 所有可被问答和知识库使用的内容必须有来源定位。
8. 后续可一键进入通用知识库入库和 Milvus 向量化。

### 3.2 非目标

第一阶段不做：

- 不替换现有财报解析入口。
- 不修改 A 股 legacy PDF 入库和财务抽取逻辑。
- 不承诺任意文档都能抽出正确业务字段；Schema 抽取需要质量状态和证据覆盖率。
- 不把大模型抽取结果当作事实库直接写入财务或合规 schema。
- 不把上传文件默认公开给外部 MinerU 云服务；云 provider 必须显式配置和标记。

## 4. 信息架构与导航设计

### 4.1 导航

新增一级导航项：

```text
文档解析
```

路由建议：

```text
/documents                 通用文档解析工作台
/documents/tasks           任务管理，可作为同页 tab 或独立路由
/documents/:taskId         任务详情，可选
```

如果后续功能变多，可扩展：

```text
/documents/parse
/documents/tasks
/documents/library
/documents/templates
```

### 4.2 与财报解析的关系

财报解析页保留市场 tabs：

```text
A股 / 港股 / 美股 / 欧股 / 日股 / 韩股
```

通用文档解析不进入这些 tabs。财报解析页可以在以下场景给出轻提示：

- 用户在美股页选择 PDF 附件时：提示可用“文档解析”做普通 PDF 转 Markdown。
- 用户在搜索下载页下载非财报附件时：操作入口指向 `/documents`。

不要在财报解析页中加入“通用”市场 tab。这样会让市场维度和文件类型维度混杂。

## 5. 总体架构

### 5.1 端到端链路

```text
Web 文档解析工作台
  -> apps/api /api/documents/* 鉴权、额度、用户产物记录
  -> apps/document-parser /api/*
  -> 文件类型识别与安全扫描
  -> provider router
       local_mineru_pdf
       local_mineru_office
       local_mineru_html
       cloud_mineru_api
       simple_text_parser
       spreadsheet_parser
       html_reader
  -> 原始上游产物归档
  -> SIQ normalized artifacts
       document.md
       document_full.json
       blocks.json
       tables.json
       images/
       source_map.json
       quality_report.json
       extraction/
  -> 可选 Schema 抽取
  -> 可选 Wiki / Milvus 入库
  -> 前端预览、导出、复核、问答
```

### 5.2 服务拆分

新增通用解析服务：

```text
apps/document-parser/
  app.py
  provider_router.py
  providers/
    base.py
    mineru_local.py
    mineru_cloud.py
    office_local.py
    html_reader.py
    text_parser.py
    spreadsheet_parser.py
  artifacts.py
  contracts.py
  quality.py
  source_map.py
  extraction.py
  task_store.py
  path_config.py
  run.sh
  requirements.txt
  tests/
```

新增 API 代理：

```text
apps/api/routers/document_parser.py
```

新增前端：

```text
apps/web/src/pages/DocumentParsing.tsx
apps/web/src/lib/documentApi.ts
apps/web/src/lib/documentTypes.ts
apps/web/src/pages/documents/useDocumentTasks.ts
apps/web/src/components/document-parser/
  DocumentUploadPanel.tsx
  DocumentParameterPanel.tsx
  DocumentTaskList.tsx
  DocumentResultWorkbench.tsx
  DocumentComparisonWorkbench.tsx
  DocumentSourceViewer.tsx
  DocumentMarkdownPane.tsx
  DocumentJsonPane.tsx
  DocumentTablePane.tsx
  DocumentFigurePane.tsx
  DocumentImageReferencePanel.tsx
  DocumentArtifactList.tsx
  DocumentExtractionPanel.tsx
  DocumentQualityPanel.tsx
  DocumentBatchActionBar.tsx
```

## 6. 数据目录和产物合同

### 6.1 运行态目录

```text
data/document-parser/
  uploads/
    <task_id>/<original_filename>
  results/
    <task_id>/
      manifest.json
      document.md
      document_full.json
      blocks.json
      blocks.ndjson
      layout_blocks.json
      reading_order.json
      comparison_map.json
      tables.json
      table_index.json
      logical_tables.json
      table_relations.json
      table_merge_corrections.json
      figures.json
      figure_index.json
      images/
        original/
        crops/
        page_previews/
      source_map.json
      quality_report.json
      extraction/
        schema.json
        result.json
        evidence_map.json
        validation_report.json
      raw/
        mineru/
          full.md
          *_content_list.json
          *_middle.json
          *_model.json
          *_layout.pdf
          *_span.pdf
        original/
      exports/
        full.zip
        document.html
        document.docx
        document.latex
  output/
  db/tasks.db
  logs/
  cache/
```

### 6.2 通用 manifest 合同

`manifest.json`：

```json
{
  "schema_version": "generic_document_parse_v1",
  "task_id": "uuid",
  "data_id": "optional-business-id",
  "filename": "example.pdf",
  "original_extension": ".pdf",
  "mime_type": "application/pdf",
  "source_type": "upload|url|downloaded_file|workspace_file",
  "source_url": "",
  "file_size": 123456,
  "file_sha256": "...",
  "document_kind": "pdf|image|word|ppt|excel|html|web|text|unknown",
  "parser_provider": "local_mineru|cloud_mineru|office_local|html_reader|text_parser",
  "parser_version": "document_parser_0.1.0",
  "upstream_parser_version": "mineru_xxx",
  "parse_config": {
    "model_version": "auto",
    "ocr": "auto",
    "enable_formula": true,
    "enable_table": true,
    "language": "auto",
    "page_ranges": "",
    "extra_formats": []
  },
  "status": "completed",
  "quality_status": "pass|warning|fail",
  "created_at": "2026-06-27T00:00:00+08:00",
  "completed_at": "2026-06-27T00:01:30+08:00",
  "artifact_hashes": {}
}
```

### 6.3 `blocks.json` 合同

`blocks.json` 是通用解析的核心结构，统一 PDF、Office、HTML、图片的内容块。

```json
{
  "schema_version": "document_blocks_v1",
  "task_id": "uuid",
  "blocks": [
    {
      "block_id": "b000001",
      "type": "title|heading|paragraph|list|table|image|chart|equation|code|header|footer|page_number|footnote|unknown",
      "sub_type": "",
      "text": "正文或标题",
      "markdown": "Markdown 片段",
      "html": "",
      "page_number": 1,
      "page_index": 0,
      "sheet_name": "",
      "slide_number": null,
      "bbox": [0, 0, 100, 100],
      "bbox_unit": "pdf_point|normalized_1000|pixel|none",
      "reading_order": 1,
      "parent_block_id": "",
      "source_ref": {
        "evidence_id": "doc:uuid:p1:b000001",
        "source_type": "pdf_block",
        "path": "raw/original/example.pdf"
      },
      "confidence": 0.95,
      "warnings": []
    }
  ]
}
```

### 6.4 `tables.json` 合同

```json
{
  "schema_version": "document_tables_v1",
  "task_id": "uuid",
  "tables": [
    {
      "table_id": "t000001",
      "block_id": "b000023",
      "title": "",
      "page_number": 5,
      "sheet_name": "",
      "html": "<table>...</table>",
      "markdown": "| A | B |",
      "cells": [
        {
          "row_index": 0,
          "column_index": 0,
          "text": "字段",
          "bbox": [0, 0, 10, 10],
          "evidence_id": "doc:uuid:p5:t000001:r0:c0"
        }
      ],
      "quality": {
        "has_header": true,
        "row_count": 10,
        "column_count": 4,
        "empty_cell_ratio": 0.03
      }
    }
  ]
}
```

### 6.5 `figures.json` 合同

图片、图表、组织架构图、流程图、截图和扫描件中的局部图像都必须成为一等产物。右侧 Markdown 中显示的图片，必须能回跳到左侧源文档页和 bbox；后续 Agent 引用图片时，也必须能给出 `image_id + page_number + bbox + caption/OCR`。

```json
{
  "schema_version": "document_figures_v1",
  "task_id": "uuid",
  "figures": [
    {
      "image_id": "img-000001",
      "block_id": "b000081",
      "type": "image|chart|diagram|photo|screenshot|scanned_region|unknown",
      "page_number": 82,
      "page_index": 81,
      "bbox": [86, 142, 512, 468],
      "bbox_unit": "pdf_point",
      "image_path": "images/crops/img-000001.png",
      "thumbnail_path": "images/crops/img-000001.thumb.webp",
      "source_page_image_path": "images/page_previews/page_0082.png",
      "caption": "公司治理架构图",
      "footnote": "",
      "nearby_heading": "5.1 公司治理架构图",
      "ocr_text": "股东会 董事会 行长室 ...",
      "alt_text": "公司治理架构图，展示股东会、董事会、行长室和各专门委员会之间的关系。",
      "markdown": "![公司治理架构图](images/crops/img-000001.png)",
      "markdown_anchor": "md-img-000001",
      "evidence_id": "doc:uuid:p82:img-000001",
      "quality": {
        "crop_available": true,
        "caption_detected": true,
        "ocr_available": true,
        "is_low_resolution": false
      }
    }
  ]
}
```

图片处理原则：

- `image_path` 保存裁剪后的图片块，用于 Markdown、预览、下载和 RAG。
- `source_page_image_path` 保存源页预览，用于左侧对照。
- `caption` 来自图片标题、图注或邻近标题；不确定时为空，不强行生成。
- `ocr_text` 来自图片内部 OCR，适合组织架构图、流程图、截图、扫描图。
- `alt_text` 可由规则或模型辅助生成，但必须标记来源；不能替代原文 evidence。
- 图片块 source map 必须指向源页 bbox，而不是只指向裁剪图文件。

### 6.6 `source_map.json` 合同

`source_map.json` 是通用解析能否被 Agent 和结构化抽取可信使用的关键。

```json
{
  "schema_version": "document_source_map_v1",
  "task_id": "uuid",
  "sources": [
    {
      "evidence_id": "doc:uuid:p5:t000001:r0:c0",
      "artifact": "tables.json",
      "block_id": "b000023",
      "table_id": "t000001",
      "image_id": "",
      "page_number": 5,
      "row_index": 0,
      "column_index": 0,
      "bbox": [0, 0, 10, 10],
      "quote": "原文片段",
      "open_source_url": "/api/documents/source/uuid/page/5?block=b000023",
      "open_artifact_url": "/api/documents/artifact/uuid/tables.json"
    }
  ]
}
```

图片 source map 示例：

```json
{
  "evidence_id": "doc:uuid:p82:img-000001",
  "artifact": "figures.json",
  "block_id": "b000081",
  "image_id": "img-000001",
  "source_type": "image_block",
  "page_number": 82,
  "bbox": [86, 142, 512, 468],
  "quote": "公司治理架构图",
  "open_source_url": "/api/documents/source/uuid/page/82?image=img-000001",
  "open_artifact_url": "/api/documents/artifact/uuid/images/crops/img-000001.png"
}
```

### 6.7 `quality_report.json` 合同

```json
{
  "schema_version": "document_quality_v1",
  "overall_status": "pass|warning|fail",
  "document_kind": "pdf",
  "page_count": 120,
  "block_count": 3400,
  "table_count": 85,
  "image_count": 42,
  "equation_count": 12,
  "ocr_used": true,
  "language_detected": ["zh", "en"],
  "coverage": {
    "pages_with_text_ratio": 0.98,
    "blocks_with_source_ratio": 1.0,
    "tables_with_cells_ratio": 0.94,
    "extraction_evidence_ratio": 0.0
  },
  "warnings": [
    {
      "code": "sparse_page_text",
      "severity": "warning",
      "message": "第 12 页文本较少，可能是图片页或 OCR 失败",
      "page_number": 12
    }
  ]
}
```

## 7. 后端 API 设计

### 7.1 `apps/document-parser` 内部 API

内部服务默认监听：

```text
http://127.0.0.1:15010
```

API：

| Method | Path | 用途 |
| --- | --- | --- |
| `GET` | `/api/health` | 服务、provider、版本、运行目录健康检查 |
| `POST` | `/api/tasks` | 上传文件或 URL，创建解析任务 |
| `GET` | `/api/tasks` | 列出任务 |
| `GET` | `/api/tasks/{task_id}` | 任务元数据 |
| `GET` | `/api/status/{task_id}` | 状态、进度、日志增量 |
| `POST` | `/api/cancel/{task_id}` | 取消任务 |
| `POST` | `/api/retry/{task_id}` | 使用原始文件重试 |
| `DELETE` | `/api/tasks/{task_id}` | 删除任务和产物 |
| `GET` | `/api/result/{task_id}` | Markdown、manifest、artifact summary |
| `GET` | `/api/artifact/{task_id}/{artifact}` | 打开白名单产物 |
| `GET` | `/api/artifact/{task_id}/images/{name}` | 打开图片产物 |
| `GET` | `/api/figures/{task_id}` | 列出图片、图表、截图和其 source map |
| `GET` | `/api/figures/{task_id}/{image_id}` | 获取单个图片块、裁剪图、caption、OCR 和来源 |
| `GET` | `/api/download/{task_id}` | 下载完整 zip |
| `GET` | `/api/source/{task_id}/page/{page_number}` | 页面或原文定位 |
| `GET` | `/api/source/{task_id}/block/{block_id}` | 内容块定位 |
| `GET` | `/api/source/{task_id}/table/{table_id}` | 表格定位 |
| `GET` | `/api/source/{task_id}/image/{image_id}` | 图片块定位 |
| `POST` | `/api/extract/{task_id}` | 基于模板或 JSON Schema 抽取 |
| `GET` | `/api/extract/{task_id}/{extract_id}` | 查询抽取结果 |

### 7.2 创建任务请求

上传文件：

```http
POST /api/tasks
Content-Type: multipart/form-data

files=<file>
source_type=upload
model_version=auto
ocr=auto
enable_formula=true
enable_table=true
language=auto
page_ranges=1-20
extra_formats=html,docx,latex
schema_template_id=
```

URL 解析：

```json
{
  "source_type": "url",
  "url": "https://example.com/report.pdf",
  "model_version": "auto",
  "ocr": "auto",
  "enable_formula": true,
  "enable_table": true,
  "language": "auto",
  "page_ranges": "",
  "extra_formats": ["html"],
  "no_cache": false
}
```

### 7.3 状态模型

统一状态：

```text
queued
uploaded
detecting_type
converting
submitting
pending
running
postprocessing
extracting
completed
completed_with_warnings
failed
cancelled
```

状态响应：

```json
{
  "task_id": "uuid",
  "status": "running",
  "stage": "running",
  "progress_percent": 42,
  "queue_position": 3,
  "total_pages": 120,
  "processed_pages": 51,
  "current_step": "layout_analysis",
  "logs": [],
  "log_count": 18,
  "artifacts_ready": false
}
```

### 7.4 结构化抽取 API

请求：

```json
{
  "mode": "schema",
  "template_id": "contract_terms_v1",
  "schema": {
    "type": "object",
    "properties": {
      "party_a": {"type": "string"},
      "party_b": {"type": "string"},
      "effective_date": {"type": "string"},
      "amount": {"type": "number"}
    },
    "required": ["party_a", "party_b"]
  },
  "instructions": "只从原文抽取，不确定则返回 null。",
  "require_evidence": true
}
```

响应：

```json
{
  "extract_id": "uuid",
  "status": "completed",
  "result": {
    "party_a": "甲方名称",
    "party_b": "乙方名称",
    "effective_date": "2026-01-01",
    "amount": 1000000
  },
  "evidence_map": {
    "party_a": ["doc:uuid:p1:b000004"],
    "amount": ["doc:uuid:p8:t000003:r2:c4"]
  },
  "validation_report": {
    "schema_valid": true,
    "evidence_coverage_ratio": 1.0,
    "warnings": []
  }
}
```

抽取原则：

- 所有字段默认必须有 evidence。
- 模型不得根据常识补全缺失字段。
- 未命中字段返回 `null`，并在 `validation_report.warnings` 写明原因。
- 对数值字段必须保留原始字符串、标准化数值、单位和 evidence。

## 8. API 网关和鉴权代理

新增：

```text
apps/api/routers/document_parser.py
```

挂载：

```python
app.include_router(document_parser.router, prefix="/api", dependencies=[Depends(get_current_user)])
```

对外前端 API：

```text
/api/documents/health
/api/documents/tasks
/api/documents/tasks/{task_id}
/api/documents/status/{task_id}
/api/documents/result/{task_id}
/api/documents/artifact/{task_id}/{artifact}
/api/documents/source/{task_id}/page/{page}
/api/documents/source/{task_id}/block/{block_id}
/api/documents/source/{task_id}/table/{table_id}
/api/documents/source/{task_id}/image/{image_id}
/api/documents/figures/{task_id}
/api/documents/figures/{task_id}/{image_id}
/api/documents/table-relations/{task_id}
/api/documents/table-relations/{task_id}/{relation_id}/review
/api/documents/logical-tables/{task_id}/{logical_table_id}/split
/api/documents/logical-tables/{task_id}/merge
/api/documents/extract/{task_id}
/api/documents/download/{task_id}
```

实现要求：

1. 复用 `usage_service.PARSE_EVENT` 或新增 `DOCUMENT_PARSE_EVENT`。建议新增 `DOCUMENT_PARSE_EVENT`，避免财报解析额度和通用解析额度混淆。
2. 上传前检查额度，按文件数和页数两层记录。第一期可按文件数，第二期补页数。
3. 任务创建成功后写 `UserArtifact`：

```text
artifact_type = "document_parse"
artifact_key = task_id
global_artifact_id = task_id
source = "document_upload|document_url|document_reused"
```

4. 访问任务结果、source、artifact 前必须检查用户拥有该任务，管理员可绕过。
5. `/api/documents/tasks` 默认只返回当前用户任务；如需系统队列视图，后续给管理员开关。
6. 所有 artifact 打开必须使用白名单，禁止任意路径读取。

环境变量：

```text
SIQ_DOCUMENT_PARSER_API_BASE=http://127.0.0.1:15010
SIQ_DOCUMENT_PARSER_ACCESS_TOKEN=
SIQ_DOCUMENT_PARSE_DATA_DIR=/home/maoyd/siq-research-engine/data/document-parser
SIQ_DOCUMENT_PARSE_MAX_FILE_MB=200
SIQ_DOCUMENT_PARSE_MAX_FILES_PER_UPLOAD=50
SIQ_DOCUMENT_PARSE_CLOUD_ENABLED=false
MINERU_API_TOKEN=
```

## 9. Provider 设计

### 9.1 Provider 接口

```python
class DocumentParserProvider(Protocol):
    name: str
    supported_kinds: set[str]

    def health(self) -> ProviderHealth: ...
    def submit(self, task: DocumentTask, source: SourceFile, config: ParseConfig) -> ProviderSubmitResult: ...
    def refresh(self, task: DocumentTask) -> ProviderStatus: ...
    def fetch_artifacts(self, task: DocumentTask, result_dir: Path) -> ProviderArtifacts: ...
    def cancel(self, task: DocumentTask) -> ProviderCancelResult: ...
```

Provider 不直接写最终 SIQ 合同。Provider 只负责拿到上游结果，后处理器负责归一：

```text
provider raw output
  -> normalize_to_blocks
  -> build_markdown
  -> build_tables
  -> build_source_map
  -> build_quality_report
```

### 9.2 provider 路由规则

```text
PDF / image
  auto -> local_mineru 或 cloud_mineru
  model_version=pipeline|vlm

DOC/DOCX/PPT/PPTX
  -> office converter -> MinerU office path 或 LibreOffice 转 PDF 后解析

XLS/XLSX
  -> spreadsheet_parser 优先保留 sheet/cell 结构
  -> 可选生成 Markdown 和 tables.json

HTML / URL
  -> MinerU-HTML 或 html_reader
  -> 输出正文 Markdown、DOM source map、链接和图片

TXT / MD
  -> text_parser
  -> 块切分和 source line map
```

### 9.3 云 provider 策略

云 MinerU 只在显式开启时可用：

```text
SIQ_DOCUMENT_PARSE_CLOUD_ENABLED=true
MINERU_API_TOKEN=...
```

前端必须显示 provider：

```text
本地解析
云端 MinerU
```

若文档可能包含敏感信息，默认使用本地解析。云 provider 应记录到 manifest：

```json
{
  "parser_provider": "cloud_mineru",
  "external_processing": true
}
```

## 10. 后处理与质量门禁

### 10.1 Markdown 生成

目标：

- 保持阅读顺序。
- 保留标题层级。
- 表格尽量以 Markdown 表格呈现；复杂表格保留 HTML 和 JSON。
- 图片、图表、公式保留引用和 caption。
- 每个块注入可选 source marker。

建议格式：

```markdown
<!-- DOC_BLOCK: b000123 page=5 evidence=doc:uuid:p5:b000123 -->
## 标题
```

### 10.2 表格处理

表格后处理要输出：

- `tables.json`：统一结构。
- `table_index.json`：轻量索引。
- `logical_tables.json`：跨页或跨块合并后的逻辑表格。
- `table_relations.json`：物理表格片段之间的 continuation / split / duplicate / unrelated 关系图。
- HTML 原样片段。
- Markdown 简化版本。
- 单元格 evidence。

质量检查：

- 空表。
- 单列错识别。
- 行列数量异常。
- 表头缺失。
- 跨页表格。
- 表格在 Markdown 中丢失。

### 10.3 源文档对照与跨页断表合并

MinerU 在线解析结果有一个本项目必须吸收的关键交互：左侧源 PDF 版面块高亮，右侧 Markdown / JSON 结果同步定位；当表格跨页断开时，左侧用连接线展示两个表格片段的合并关系，右侧输出为一张连续表格。本项目不应只把它做成前端视觉效果，而要把“对照”和“合并”落入标准产物合同。

#### 10.3.1 对照能力目标

对照能力必须支持：

- 点击源文档里的文本块、表格、图片、公式，右侧滚动到对应 Markdown / JSON block。
- 点击右侧 Markdown 段落、表格行、JSON block，左侧跳到源页并高亮 bbox。
- 支持 PDF 页图缩放、翻页、适应宽度、块标签显示和隐藏。
- 支持 Markdown / JSON tab 切换后保留当前选中 block。
- 支持跨页断表的连接线、合并标签和合并结果预览。
- 支持人工复核：确认合并、拆分误合并、编辑表头映射、保存备注。

#### 10.3.2 新增版面对照产物

新增：

```text
layout_blocks.json
reading_order.json
comparison_map.json
logical_tables.json
table_relations.json
```

`layout_blocks.json` 保存源文档版面块：

```json
{
  "schema_version": "document_layout_blocks_v1",
  "task_id": "uuid",
  "pages": [
    {
      "page_number": 8,
      "page_index": 7,
      "width": 595.28,
      "height": 841.89,
      "blocks": [
        {
          "layout_block_id": "p0008-b0012",
          "block_id": "b000123",
          "type": "text|title|table|image|equation|footer",
          "bbox": [72, 148, 510, 235],
          "bbox_unit": "pdf_point",
          "text_preview": "加快四化转型...",
          "confidence": 0.96
        }
      ]
    }
  ]
}
```

`comparison_map.json` 是左右对照的核心索引：

```json
{
  "schema_version": "document_comparison_map_v1",
  "task_id": "uuid",
  "entries": [
    {
      "entry_id": "cmp-000001",
      "block_id": "b000123",
      "layout_block_id": "p0008-b0012",
      "markdown_anchor": "md-b000123",
      "json_pointer": "/blocks/122",
      "page_number": 8,
      "bbox": [72, 148, 510, 235],
      "evidence_id": "doc:uuid:p8:b000123",
      "text_hash": "sha256:..."
    }
  ]
}
```

前端对照时只读 `comparison_map.json`，不要靠 fuzzy text search 临时匹配。fuzzy search 只能作为缺失 map 时的 fallback。

#### 10.3.3 物理表格与逻辑表格合同

通用表格必须区分：

```text
physical table fragment  物理表格片段：源文档某一页/某一区域识别到的一块表格
logical table            逻辑表格：可能由一个或多个 physical fragments 合并而成
```

`tables.json` 保存物理片段：

```json
{
  "schema_version": "document_tables_v1",
  "physical_tables": [
    {
      "table_id": "pt-000014",
      "block_id": "b000214",
      "page_number": 14,
      "bbox": [96, 42, 510, 330],
      "caption": "主要会计数据和财务指标",
      "unit_text": "人民币百万元",
      "header_rows": [["项目", "2025年12月31日", "2024年12月31日"]],
      "column_count": 4,
      "row_count": 18,
      "cells": [],
      "source_quality": {
        "is_truncated_top": false,
        "is_truncated_bottom": true,
        "near_page_bottom": true,
        "near_page_top": false
      }
    },
    {
      "table_id": "pt-000015",
      "block_id": "b000215",
      "page_number": 15,
      "bbox": [96, 36, 510, 520],
      "caption": "",
      "unit_text": "",
      "header_rows": [["项目", "2025年12月31日", "2024年12月31日"]],
      "column_count": 4,
      "row_count": 22,
      "cells": [],
      "source_quality": {
        "is_truncated_top": true,
        "is_truncated_bottom": false,
        "near_page_bottom": false,
        "near_page_top": true
      }
    }
  ]
}
```

`logical_tables.json` 保存合并结果：

```json
{
  "schema_version": "document_logical_tables_v1",
  "logical_tables": [
    {
      "logical_table_id": "lt-000007",
      "title": "主要会计数据和财务指标",
      "fragment_table_ids": ["pt-000014", "pt-000015"],
      "merge_status": "auto_merged|candidate|manual_merged|manual_split|single",
      "merge_confidence": 0.91,
      "merge_reasons": [
        "adjacent_pages",
        "same_column_signature",
        "first_fragment_near_page_bottom",
        "second_fragment_near_page_top",
        "repeated_header_removed"
      ],
      "header_rows": [["项目", "2025年12月31日", "2024年12月31日"]],
      "rows": [],
      "html": "<table>...</table>",
      "markdown": "| 项目 | 2025年12月31日 | 2024年12月31日 |",
      "source_fragments": [
        {"table_id": "pt-000014", "page_number": 14, "row_range": [0, 17]},
        {"table_id": "pt-000015", "page_number": 15, "row_range": [18, 39]}
      ],
      "evidence_ids": ["doc:uuid:p14:pt-000014", "doc:uuid:p15:pt-000015"],
      "warnings": []
    }
  ]
}
```

`table_relations.json` 保存连接线和人工复核所需关系：

```json
{
  "schema_version": "document_table_relations_v1",
  "relations": [
    {
      "relation_id": "rel-000001",
      "from_table_id": "pt-000014",
      "to_table_id": "pt-000015",
      "relation_type": "continuation|candidate_continuation|not_continuation|duplicate|split_part",
      "confidence": 0.91,
      "visual_connector": {
        "from_page": 14,
        "to_page": 15,
        "from_anchor": [510, 330],
        "to_anchor": [96, 36]
      },
      "reasons": ["same_column_signature", "adjacent_pages"],
      "review_status": "unreviewed|accepted|rejected|edited"
    }
  ]
}
```

#### 10.3.4 跨页断表候选生成

候选生成只在有限范围内做，避免 O(n²) 和误合并：

```text
同一文档
  -> 相邻页或间隔 1 页
  -> 相同章节上下文
  -> 表格 bbox 处于上一页下半部分 / 下一页上半部分
  -> column_count 或 column geometry 接近
```

高置信信号：

- 两个表格在连续页。
- 上一片段靠近页底，下一片段靠近页顶。
- 列数一致或 colspan 展开后列数一致。
- 表头文本相同或下一片段重复表头。
- 单位、期间、币种一致。
- caption 相同，或下一页 caption 缺失但上文仍在同一章节。
- 行项目连续，下一片段第一列不是新表标题。
- 版面宽度、左右边界和列边界接近。

负信号：

- 下一片段有新的独立 caption。
- 列数差异大且无法通过 colspan/rowspan 展开解释。
- 单位、期间或币种冲突。
- 中间出现新章节标题。
- 上一片段已有完整 footnote / table end marker。
- 下一片段第一行像新表标题，例如“下表列示”“主要财务指标”“董事会报告”。
- 两个表格类型不同，例如一个是财务指标表，一个是股东名单。

#### 10.3.5 合并算法

合并流程：

```text
physical tables
  -> normalize cells / expand rowspan colspan
  -> infer header rows
  -> compute table signature
  -> build continuation candidates
  -> score candidates
  -> auto merge high confidence
  -> keep medium confidence as candidate
  -> write logical_tables + table_relations
  -> build logical table source_map
```

表格 signature 建议包含：

```json
{
  "column_count": 5,
  "column_width_signature": [0.32, 0.17, 0.17, 0.17, 0.17],
  "header_text_signature": "项目|2025|2024|增减",
  "unit_signature": "人民币百万元",
  "period_signature": "2025-12-31|2024-12-31",
  "numeric_density": 0.72,
  "row_label_profile": "financial_metric_rows"
}
```

重复表头处理：

- 如果第二片段前 1-3 行与第一片段 header signature 高相似，合并时删除重复表头。
- 删除不是丢弃，必须在 `logical_tables.rows[].source_cells` 中保留原始来源。
- 如果第二片段没有表头，继承第一片段表头，并写入 `merge_reasons=["header_inherited"]`。

单元格 evidence：

```json
{
  "logical_row_index": 18,
  "logical_column_index": 2,
  "text": "13,070,523",
  "source_cells": [
    {
      "physical_table_id": "pt-000015",
      "page_number": 15,
      "row_index": 2,
      "column_index": 2,
      "bbox": [120, 82, 180, 96],
      "evidence_id": "doc:uuid:p15:pt-000015:r2:c2"
    }
  ]
}
```

关键原则：

- 永远保留物理表格片段，不用合并结果覆盖原始结果。
- 自动合并只在高置信时执行。
- 中置信候选只展示为“建议合并”，不进入默认 logical table。
- 所有逻辑表格单元格必须能回跳到物理页和 bbox。
- 财报抽取、Wiki、PostgreSQL、Milvus 默认使用 logical table，但保留 physical table fallback。

#### 10.3.6 人工复核与修正

新增修正文件：

```text
table_merge_corrections.json
```

结构：

```json
{
  "schema_version": "document_table_merge_corrections_v1",
  "task_id": "uuid",
  "relations": {
    "rel-000001": {
      "review_status": "accepted|rejected|edited",
      "note": "确认第 14 页和第 15 页为同一张表",
      "updated_at": "2026-06-27T00:00:00+08:00"
    }
  },
  "manual_logical_tables": []
}
```

后端应支持：

```text
GET  /api/table-relations/{task_id}
POST /api/table-relations/{task_id}/{relation_id}/review
POST /api/logical-tables/{task_id}/{logical_table_id}/split
POST /api/logical-tables/{task_id}/merge
```

通用服务对外路径经 API 网关映射为：

```text
/api/documents/table-relations/{task_id}
/api/documents/table-relations/{task_id}/{relation_id}/review
/api/documents/logical-tables/{task_id}/{logical_table_id}/split
/api/documents/logical-tables/{task_id}/merge
```

### 10.4 图片和公式

图片：

- 从 MinerU `content_list` / `middle.json` / 版面块中识别 image、chart、diagram、photo、screenshot、scanned_region。
- 保存裁剪图、缩略图和源页预览：`images/crops/`、`images/original/`、`images/page_previews/`。
- 保留 caption、footnote、nearby heading、page、bbox、block_id、evidence_id。
- 生成 `figures.json` 和 `figure_index.json`，并把图片块写入 `blocks.json`、`comparison_map.json`、`source_map.json`。
- Markdown 中引用本地 artifact URL，并给每张图片生成稳定 anchor，例如 `<a id="md-img-000001"></a>`。
- 图片标题、图注、图片自身 OCR 文本都要进入 `image_caption` chunk，但 chunk metadata 必须包含 `image_id` 和 `evidence_id`。
- 对组织架构图、流程图、截图、扫描件执行图片内部 OCR；OCR 结果作为辅助检索文本，不覆盖 caption 和原图 evidence。
- 对低清裁剪、缺失 bbox、caption 缺失、OCR 失败、裁剪文件缺失输出质量 warning。
- 前端点击 Markdown 图片、图注或图片 tab 卡片时，左侧必须跳到源页并高亮 bbox；点击左侧图片 bbox 时，右侧必须定位到图片预览和 Markdown 图片节点。

图片入库原则：

- Wiki 包保存裁剪图、缩略图、源页预览和 `figures.json`。
- PostgreSQL 保存图片索引和 source map，不保存大图二进制。
- Milvus 只写入 `image_caption` chunk，文本由标题、图注、nearby heading、OCR 摘要组成；不得把图片 base64 直接入向量库。
- Agent 引用图片时必须展示图片标题或 OCR 摘要，并提供 `open_source_url` 和 `open_artifact_url`。

公式：

- 保留 LaTeX。
- 区分 inline / block equation。
- `quality_report` 中记录公式数量和疑似失败数量。

### 10.5 source map

source map 必须覆盖：

- Markdown 块。
- JSON block。
- 表格单元格。
- 逻辑合并表格单元格。
- 图片、图表、截图和扫描局部图像块。
- 图片内部 OCR 片段。
- Markdown 图片节点和图注节点。
- Schema 抽取字段。
- RAG chunks。

字段命中 source map 后，前端应能打开：

```text
源页 / 源块 / 源表格 / 源图片 bbox / 裁剪图 artifact / 原始 artifact
```

source map 字段建议补齐：

```json
{
  "evidence_id": "doc:uuid:p82:img-000001",
  "source_type": "image_block|image_ocr|markdown_image|table_cell|logical_table_cell|text_block",
  "artifact": "figures.json",
  "block_id": "b000081",
  "table_id": "",
  "logical_table_id": "",
  "image_id": "img-000001",
  "markdown_anchor": "md-img-000001",
  "page_number": 82,
  "bbox": [86, 142, 512, 468],
  "quote": "公司治理架构图",
  "open_source_url": "/api/documents/source/uuid/page/82?image=img-000001",
  "open_artifact_url": "/api/documents/artifact/uuid/images/crops/img-000001.png"
}
```

### 10.6 质量报告

质量报告至少包含：

- 文件类型、页数、块数、表格数、图片数、公式数。
- OCR 是否启用。
- 文本覆盖率。
- source map 覆盖率。
- 表格结构完整性。
- 图片裁剪成功率。
- 图片 source map 覆盖率。
- 图片 caption 覆盖率。
- 图片内部 OCR 覆盖率。
- 跨页断表候选数、自动合并数、待复核数、被人工拆分数。
- 解析器 warnings。
- 抽取 evidence 覆盖率。
- 是否适合进入知识库。

图片相关指标建议：

```json
{
  "image_quality": {
    "image_count": 42,
    "figure_count": 12,
    "chart_count": 5,
    "diagram_count": 3,
    "images_with_crop_ratio": 1.0,
    "images_with_source_ratio": 1.0,
    "images_with_caption_ratio": 0.76,
    "images_with_ocr_ratio": 0.52,
    "low_resolution_count": 2,
    "missing_bbox_count": 0
  }
}
```

## 11. 多目标知识库导入

### 11.1 导入目标结论

通用文档解析完成后不应只支持导入 Wiki。Wiki 是权威归档层，但它解决的是“人可读、可重建、可审计”的问题；如果后续要支持 Agent 问答、语义召回、结构化筛选、字段级审计和批量任务管理，还需要至少支持 Milvus 和 PostgreSQL 两类导入。

推荐分层：

| 层级 | 目标库 | 是否必做 | 用途 | 是否事实源 |
| --- | --- | --- | --- | --- |
| 原始产物层 | `data/document-parser/results` | 必做 | 运行态任务产物、下载、预览、重跑 | 是 |
| Wiki 归档层 | `data/wiki/documents` | 必做 | 人可读文档包、可重建 DB/Milvus、长期归档 | 是 |
| 结构化索引层 | PostgreSQL `document_parser` | P1/P2 必做 | 任务、文档、块、表格、字段抽取、source map 查询 | 是，保存索引和证据定位 |
| 语义索引层 | Milvus `siq_documents` | P1/P2 必做 | Agent/RAG 召回、相似文档、跨文档问答 | 否，只做召回 |
| 全文检索层 | PostgreSQL FTS 或 OpenSearch | P3 可选 | 精确关键词、高亮、布尔查询、过滤排序 | 否，索引层 |
| 对象存储层 | MinIO/S3/Azure Blob | P3 可选 | 大文件、图片、导出包的集中存储 | 是，保存大对象副本 |

第一阶段建议不要引入太多新基础设施。本项目当前已有 Wiki、PostgreSQL 和 Milvus 方向，因此通用解析导入优先级应为：

```text
P0: results 运行态产物
P1: data/wiki/documents 通用 Wiki 包
P2: PostgreSQL document_parser + Milvus siq_documents
P3: 全文检索 / 对象存储扩展
```

### 11.2 导入工作流

解析完成后提供统一导入动作：

```text
document parse artifacts
  -> build document package
  -> wiki import
  -> db import
  -> semantic import
  -> optional full-text / object storage sync
```

工作流状态不应只有“已入库/未入库”，而应分目标展示：

```json
{
  "task_id": "uuid",
  "targets": {
    "wiki": {"status": "ready|running|completed|failed", "path": "data/wiki/documents/default/doc-key"},
    "postgres": {"status": "ready|running|completed|failed", "schema": "document_parser", "document_id": "..."},
    "milvus": {"status": "ready|running|completed|failed", "collection": "siq_documents", "chunk_count": 128},
    "full_text": {"status": "disabled|ready|running|completed|failed"},
    "object_storage": {"status": "disabled|ready|running|completed|failed"}
  }
}
```

前端结果页“入库”区域建议显示三个主按钮：

```text
导入 Wiki
导入结构化库
导入语义库
```

也提供一个组合动作：

```text
一键入库
```

一键入库默认顺序：

```text
Wiki -> PostgreSQL -> Milvus
```

原因是 Milvus metadata 必须能回查 Wiki / PostgreSQL source map；如果直接先入 Milvus，后续 evidence 回跳会不稳定。

### 11.3 通用 Wiki 归档

通用文档归档目录：

```text
data/wiki/documents/<collection>/<document_key>/
  manifest.json
  README.md
  raw/
  sections/
  tables/
  logical_tables/
  images/
  figures/
  comparison/
  extraction/
  qa/
```

与财报市场 evidence package 不混用 namespace。

Wiki 包必须做到：

- 独立保存原始文件引用和 hash。
- 保存 `document.md`、`blocks.json`、`tables.json`、`logical_tables.json`、`table_relations.json`、`figures.json`、`comparison_map.json`、`source_map.json`、`quality_report.json`。
- 保存 Schema 抽取结果和字段 evidence。
- 保存图片裁剪图、缩略图、源页预览和跨页表人工复核修正。
- 可以从 Wiki 包重建 PostgreSQL 和 Milvus。
- 可以脱离运行态 `data/document-parser/results` 长期保存。

建议 `manifest.json` 追加：

```json
{
  "schema_version": "generic_document_package_v1",
  "document_id": "...",
  "task_id": "...",
  "collection": "default",
  "document_key": "...",
  "source_result_dir": "data/document-parser/results/<task_id>",
  "package_version": "1",
  "import_targets": {
    "postgres": {
      "schema": "document_parser",
      "document_id": "...",
      "last_imported_at": null
    },
    "milvus": {
      "collection": "siq_documents",
      "last_imported_at": null
    }
  }
}
```

### 11.4 PostgreSQL schema

新增可选 schema：

```text
document_parser
```

核心表：

```sql
document_parser.documents
document_parser.parse_runs
document_parser.blocks
document_parser.tables
document_parser.table_cells
document_parser.logical_tables
document_parser.table_relations
document_parser.figures
document_parser.extractions
document_parser.extraction_fields
document_parser.sources
document_parser.artifacts
```

原则：

- PostgreSQL 保存索引、结构化查询和 source map，不保存大文件主体。
- 大文件在 `data/document-parser` 和 `data/wiki/documents`。
- 入库脚本必须幂等。

新增 DDL：

```text
db/ddl/060_create_document_parser_schema.sql
```

新增导入器：

```text
db/imports/import_document_parse_package_to_postgres.py
```

PostgreSQL 导入不是为了替代 Wiki，而是为了让系统能高效回答这些问题：

- 某用户或某项目有哪些文档。
- 某个文档有哪些表格、字段抽取结果和质量 warning。
- 某个 evidence_id 对应哪一页、哪个块、哪个表格单元格。
- 某个 Schema 字段在哪些文档中出现。
- 某个文档是否已导入 Milvus、chunk 数是多少。

### 11.5 Milvus

新增 collection：

```text
siq_documents
```

metadata：

```json
{
  "source_domain": "generic_document",
  "document_id": "...",
  "task_id": "...",
  "collection": "default",
  "document_kind": "pdf",
  "block_id": "b000123",
  "evidence_id": "doc:uuid:p5:b000123",
  "page_number": 5,
  "section_title": "..."
}
```

向量 chunk 输入：

- 标题层级。
- 段落块。
- 表格摘要和必要表格文本。
- 图片 caption。
- Schema 抽取字段摘要。

不要把整个 `middle.json` 直接入向量库。

Milvus 导入建议作为通用文档解析的核心能力，而不是远期可选项。原因：

- 通用文档的主要价值之一是进入 Agent/RAG 问答。
- Wiki 适合归档，但不适合低延迟语义召回。
- PostgreSQL 适合结构化过滤，但不适合语义相似度。
- Milvus chunk metadata 可以把语义召回结果重新指向 Wiki、PostgreSQL 和原文 source map。

Milvus chunk 类型：

| chunk_type | 来源 | 说明 |
| --- | --- | --- |
| `section` | Markdown 标题段落 | 普通正文召回 |
| `table_summary` | `tables.json` | 表格标题、表头、关键行摘要 |
| `table_cells` | `tables.json` | 小表或重要表格原文 |
| `image_caption` | 图片 caption / OCR | 图片语义召回 |
| `extraction_field` | Schema 抽取结果 | 字段级问答召回 |

Milvus metadata 最小合同：

```json
{
  "source_domain": "generic_document",
  "collection": "default",
  "document_id": "...",
  "task_id": "...",
  "wiki_package_path": "data/wiki/documents/default/doc-key",
  "postgres_schema": "document_parser",
  "block_id": "b000123",
  "table_id": "",
  "image_id": "",
  "evidence_id": "doc:uuid:p5:b000123",
  "chunk_type": "section",
  "document_kind": "pdf",
  "page_number": 5,
  "section_title": "...",
  "open_source_url": "/api/documents/source/uuid/page/5?block=b000123"
}
```

验收要求：

- 任一 Milvus 命中必须能通过 `task_id + evidence_id` 回查 source map。
- Milvus collection 可从 Wiki 包重建。
- 删除文档或重新导入时不会残留旧 chunk。

### 11.6 全文检索和对象存储扩展

全文检索可先不引入新服务。P3 如果需要关键词检索、高亮和复杂过滤，可二选一：

```text
PostgreSQL FTS
OpenSearch
```

建议先用 PostgreSQL FTS，除非文档量、并发和高亮需求明显超过 PostgreSQL 能力。

对象存储也先不作为 P1/P2 阻塞项。本地部署阶段继续使用 `data/document-parser` 和 `data/wiki/documents`；如果后续进入多机部署或大文件规模化，再接：

```text
MinIO / S3 / Azure Blob
```

对象存储只负责大对象，不负责事实判断；manifest 和 source map 仍然是证据合同。

## 12. 前端工作台设计

### 12.1 页面布局

路由：

```text
/documents
```

桌面布局：

```text
PageHeader
  title: 文档解析
  description: 上传 PDF、Office、图片或网页，生成 Markdown、JSON、表格和可溯源抽取结果。

主工作区
  左侧 320-380px：上传、来源、解析参数、任务列表
  右侧 flex：结果工作台
    顶部：当前任务状态、导出、入库、重试
    中部：源文档 / Markdown / JSON / 表格 / 抽取 / 质量 tabs
```

移动端：

```text
顶部：任务状态 + 主要操作
Tabs：上传 / 任务 / 结果 / 抽取 / 质量
源文档预览默认折叠或单独全屏打开
```

### 12.2 上传与来源面板

组件：

```text
DocumentUploadPanel.tsx
```

能力：

- 拖拽上传。
- 多文件上传。
- URL 输入。
- 从本地下载目录选择文件，第二期实现。
- 显示支持格式。
- 文件大小和数量限制。
- 重复文件提示，可复用已有任务。

支持文件：

```text
PDF
PNG / JPG / JPEG / JP2 / WEBP / GIF / BMP
DOC / DOCX
PPT / PPTX
XLS / XLSX
HTML / HTM
TXT / MD
URL
```

### 12.3 参数面板

组件：

```text
DocumentParameterPanel.tsx
```

吸收 MinerU 交互：

- 模型版本 segmented control：`自动`、`Pipeline`、`VLM`、`HTML`。
- OCR 开关：`自动 OCR`、`强制 OCR`、`关闭 OCR`。
- 表格识别 toggle。
- 公式识别 toggle。
- 语言 select：`自动`、`中文`、`英文`、`日文`、`韩文`、`多语言`。
- 页码范围输入：支持 `1-10,15,20-22`。
- 额外导出 checkbox：HTML、DOCX、LaTeX、完整 ZIP。
- 缓存 toggle：使用缓存 / 强制重跑。

交互要求：

- HTML 文件自动建议 `MinerU-HTML`。
- Excel 文件禁用页码范围，改显示 sheet 选择。
- 图片文件禁用页码范围。
- VLM 模式下给出表格/公式参数支持差异提示。
- 页码范围本地校验，超出页数时弹窗确认或提示。

### 12.4 任务管理

组件：

```text
DocumentTaskList.tsx
DocumentBatchActionBar.tsx
```

能力：

- 列表和卡片两种视图。
- 状态筛选：全部、上传中、排队、解析中、完成、失败、已过期。
- 类型筛选：PDF、Office、HTML、图片、Excel。
- 模型筛选：auto、pipeline、vlm、html。
- 日期范围筛选。
- 搜索文件名。
- 批量选择。
- 批量下载 Markdown / JSON / ZIP。
- 批量删除。
- 失败任务重试。

### 12.5 结果工作台

组件：

```text
DocumentResultWorkbench.tsx
DocumentComparisonWorkbench.tsx
DocumentSourceViewer.tsx
DocumentMarkdownPane.tsx
DocumentJsonPane.tsx
DocumentTablePane.tsx
DocumentFigurePane.tsx
DocumentImageReferencePanel.tsx
```

Tabs：

```text
预览
Markdown
JSON
表格
图片
抽取
质量
产物
```

默认结果工作台采用 MinerU 式双栏对照：

```text
左侧：源文档页图 / HTML 原文 / Excel sheet
  页码、缩放、适应宽度、上一页、下一页
  bbox overlay：文本、表格、图片、公式使用不同边框样式
  可切换标签显示：文本 / 表格 / 图片 / 公式

右侧：Markdown / JSON / 表格 / 图片 / 抽取 / 质量 / 产物
  tab 内部滚动
  当前选中 evidence 共享
  当前 evidence 来源卡片固定在内容上方或右侧窄栏
```

共享选中态：

```ts
type SelectedEvidence = {
  taskId: string
  evidenceId: string
  blockId?: string
  tableId?: string
  logicalTableId?: string
  imageId?: string
  pageNumber?: number
  bbox?: [number, number, number, number]
}
```

交互原则：

- 点击左侧 bbox，右侧根据 `comparison_map.json` 定位到 Markdown anchor、JSON pointer、表格单元格或图片卡片。
- 点击右侧 Markdown 段落、表格单元格、JSON block、图片卡片，左侧跳到源页并高亮 bbox。
- 当前选中项必须可被键盘聚焦和 Enter 激活，不能只依赖 hover。
- 双栏滚动互不抢焦点，定位时只滚动目标面板。
- 大文档列表、JSON 和图片墙使用虚拟列表，避免 200 页年报卡顿。

预览：

- PDF：源页图或 PDF viewer。
- Office：可用浏览器预览 fallback；第一期可展示转换后的 PDF/图片或提示下载源文件。
- HTML/URL：展示提取后正文 HTML。
- Excel：展示 sheet/table 预览。
- 图片：展示图片和 OCR 结果。

Markdown：

- 支持复制、下载、搜索。
- hover、focus 或点击 block 时源文档高亮。
- 点击 source marker 打开源页。
- 图片 Markdown 节点带稳定 anchor；点击图片或图注打开 `DocumentImageReferencePanel`。

JSON：

- 展示 `blocks.json`、`document_full.json`、`source_map.json`。
- 支持折叠、搜索、复制路径。

表格：

- 表格列表。
- HTML/Markdown/JSON 切换。
- 点击单元格显示 evidence。
- 展示 logical table 和 physical fragments。
- 跨页断表用连接线、合并标签、合并置信度和原因说明展示。
- 支持人工确认合并、拒绝合并、拆分误合并、编辑表头映射。

图片：

- 左侧是缩略图列表或网格，右侧是当前图片详情。
- 图片卡片显示类型、页码、caption、nearby heading、OCR 摘要和质量 warning。
- 详情区展示裁剪图、源页 bbox 小预览、Markdown 引用、source map、RAG chunk metadata。
- 点击裁剪图上的“定位源页”按钮，左侧源文档跳到对应页并高亮 bbox。
- 点击源文档中的图片 bbox，右侧自动切到“图片”tab 并选中对应 `image_id`。
- 对低清、缺失 caption、OCR 失败的图片给出非阻塞 warning，允许用户手动编辑 caption 或标记“不入库”。
- 图片预览必须预留宽高或使用固定 aspect-ratio，避免加载后布局跳动。
- 图片卡片和图标按钮触控目标不小于 44px，图标按钮使用 aria-label 和 tooltip。

抽取：

- Schema 模板选择。
- JSON Schema 编辑器。
- 抽取结果 JSON viewer。
- 字段 evidence 列表。
- validation report。

质量：

- 总体状态。
- 覆盖率。
- warnings。
- 可入库状态。

产物：

- Markdown。
- blocks JSON。
- tables JSON。
- source map。
- quality report。
- images zip。
- full zip。
- HTML / DOCX / LaTeX。

### 12.6 前端 API

新增：

```text
apps/web/src/lib/documentApi.ts
apps/web/src/lib/documentTypes.ts
apps/web/src/pages/documents/useDocumentTasks.ts
```

核心函数：

```ts
checkDocumentParserHealth()
createDocumentTasks(form)
loadDocumentTasks()
fetchDocumentStatus(taskId, since)
fetchDocumentResult(taskId)
fetchDocumentQuality(taskId)
fetchDocumentArtifact(taskId, artifact)
fetchDocumentSourcePage(taskId, page)
fetchDocumentSourceBlock(taskId, blockId)
fetchDocumentSourceTable(taskId, tableId)
fetchDocumentSourceImage(taskId, imageId)
fetchDocumentFigures(taskId)
fetchDocumentFigure(taskId, imageId)
fetchDocumentComparisonMap(taskId)
fetchDocumentTableRelations(taskId)
reviewDocumentTableRelation(taskId, relationId, body)
mergeDocumentLogicalTables(taskId, body)
splitDocumentLogicalTable(taskId, logicalTableId, body)
runDocumentExtraction(taskId, body)
downloadDocumentArtifact(taskId, variant)
retryDocumentTask(taskId)
deleteDocumentTask(taskId)
```

### 12.7 路由和导航改动

修改：

```text
apps/web/src/App.tsx
apps/web/src/lib/routePreload.ts
apps/web/src/components/layout/layoutData.ts
apps/web/src/pages/Help.tsx
apps/web/src/pages/MyWorkspace.tsx
apps/web/src/components/layout/NotificationMenu.tsx
```

新增导航文案：

```text
文档解析
```

图标建议：

```text
FileSearch 或 Files
```

帮助页增加：

```text
文档解析：上传 PDF、Office、图片或网页 URL，生成 Markdown、表格、JSON、质量报告和结构化抽取结果。
```

工作台 artifact 类型增加：

```text
document_parse
```

## 13. 和 Agent / RAG 的集成

### 13.1 Agent 附件解析

当前聊天附件已有 PDF parse 相关上下文。后续应迁移或扩展为：

```text
任意附件
  -> document-parser
  -> 等待解析完成
  -> 注入 Markdown + source map 摘要
```

不要让聊天附件继续走一个只适合 PDF 的临时路径。

### 13.2 RAG 入库

新增工作流步骤：

```text
document-wiki-import
document-semantic
document-db-import
```

第一期可只做 UI 入口占位和 artifact readiness 检查，第二期补真实入库。

### 13.3 Agent 引用要求

Agent 使用通用文档 evidence 时必须保留：

```text
source_type=document_parse
task_id
document_id
block_id
page_number / sheet_name / slide_number
table_id
logical_table_id
image_id
evidence_id
open_source_url
open_artifact_url
```

回答中不能只引用向量 chunk 文本，应能跳回原始文档或对应 artifact。

## 14. 安全、权限和额度

### 14.1 文件安全

必须实现：

- 文件名清洗。
- MIME 和扩展名双重检查。
- 文件大小限制。
- 上传目录隔离。
- artifact 白名单。
- 禁止打开任意绝对路径。
- URL 解析防 SSRF：禁止内网 IP、localhost、file scheme、metadata IP。
- HTML 预览必须 sanitize。
- ZIP 解压必须防 zip-slip。

### 14.2 权限

- 普通用户只能访问自己的任务。
- 管理员可查看系统任务。
- 任务删除只删除自己的任务引用和可访问产物；系统级清理需管理员。

### 14.3 额度

新增额度类型：

```text
documentParse
```

计量：

- P0：按任务数。
- P1：按页数或文件大小折算。
- P2：按 provider 成本和额外格式导出折算。

### 14.4 敏感文件与云 provider

默认本地解析。云 provider 需要：

- 环境变量开启。
- 前端显式选择。
- manifest 记录 `external_processing=true`。
- UI 显示“文件将发送到外部解析服务”提示。

## 15. 开发阶段计划

### P0：架构骨架和合同

目标：通用解析服务能启动，能上传 PDF/TXT/MD，能输出最小 Markdown、blocks、manifest、quality。

新增文件：

```text
docs/architecture/2026-06-27-general-document-parsing-taskbook.md
apps/document-parser/
apps/document-parser/tests/
apps/api/routers/document_parser.py
apps/web/src/pages/DocumentParsing.tsx
apps/web/src/lib/documentApi.ts
apps/web/src/lib/documentTypes.ts
```

任务：

1. 建立 `apps/document-parser` Flask 或 FastAPI 服务。建议沿用 `apps/pdf-parser` 的 Flask 风格以降低首次迁移成本。
2. 建立 `data/document-parser` 路径配置。
3. 实现 SQLite task store。
4. 实现 `/api/health`、`/api/tasks`、`/api/status`、`/api/result`。
5. 实现 TXT/MD simple parser。
6. 实现 PDF local MinerU provider 的最小适配，先复用现有本地 MinerU HTTP 服务或命令行输出。
7. 实现最小 `manifest.json`、`document.md`、`blocks.json`、`quality_report.json`。
8. API 网关代理接入鉴权和用户产物记录。
9. 前端新增 `/documents` 页面、导航和最小上传/任务/Markdown 预览。

验收：

```bash
cd /home/maoyd/siq-research-engine/apps/document-parser
python3 -m pytest tests
bash -n run.sh
```

前端验收：

```bash
cd /home/maoyd/siq-research-engine/apps/web
npm run build
```

P0 Definition of Done：

- `/documents` 可打开。
- 上传一个 TXT/MD 可立即生成 Markdown 和 blocks。
- 上传一个 PDF 可创建任务、轮询状态、显示 Markdown。
- 非本人任务结果不可访问。
- 财报解析页面行为不变。

### P1：MinerU 能力完整吸收

目标：覆盖 PDF、图片、Office、HTML，补齐 OCR、表格、公式、语言、页码范围、额外格式。

后端任务：

1. 实现 file type detector。
2. 实现 local MinerU provider 的完整参数映射。
3. 支持官方语义参数：OCR、table、formula、language、page_ranges、model_version、extra_formats。
4. 支持 raw MinerU artifacts 归档。
5. 将 `content_list.json` / `middle.json` 归一为 `blocks.json`、`tables.json`、`source_map.json`。
6. 支持图片文件 OCR。
7. 支持 Office 文件路径：优先 MinerU office，fallback LibreOffice 转 PDF。
8. 支持 HTML / URL：MinerU-HTML 或 html reader。
9. 实现完整 zip 下载。
10. 生成 `layout_blocks.json`、`reading_order.json`、`comparison_map.json`，支持源文档和 Markdown / JSON / 表格 / 图片双向定位。
11. 生成 `figures.json`、`figure_index.json`，完成图片裁剪、缩略图、源页预览、caption 关联和图片内部 OCR。
12. 生成 `logical_tables.json`、`table_relations.json`，支持跨页断表自动合并和候选合并。
13. 实现表格合并复核 API：确认、拒绝、拆分、手动合并和修正落盘。
14. 在 `quality_report.json` 中加入图片质量、跨页表合并质量和 source map 覆盖率。

前端任务：

1. 参数面板完整。
2. 批量上传。
3. 任务筛选和批量下载。
4. JSON viewer。
5. 表格 viewer。
6. 图片 viewer。
7. 源文档 / Markdown 分屏。
8. 移动端 tabs。
9. 实现 `DocumentComparisonWorkbench`，左源文档 bbox 和右侧 Markdown / JSON / 表格 / 图片共享选中态。
10. 实现 `DocumentFigurePane` 和 `DocumentImageReferencePanel`，支持图片卡片、裁剪图、caption、OCR、source map 和定位源页。
11. 实现跨页断表连接线、合并标签、合并置信度和人工复核入口。
12. Markdown 图片、图注、表格单元格和 JSON block 都可点击回源文档。

验收样本：

```text
PDF 年报
扫描 PDF
Word 合同
PPT 路演材料
Excel 表格
PNG 截图
HTML 页面
网页 URL
```

P1 Definition of Done：

- 每类样本至少 2 个通过。
- 每个完成任务有 `manifest/document.md/blocks/tables/figures/source_map/quality`。
- 表格点击可回 source。
- Markdown 图片点击可回 source page bbox。
- 源文档图片 bbox 点击可定位到图片 tab 和 Markdown 图片节点。
- 至少一个含组织架构图、流程图或截图的 PDF 可以生成图片 caption / OCR / source map。
- 至少一个跨页断表 PDF 可以生成 `table_relations.json` 和 `logical_tables.json`，并在前端展示合并关系。
- 人工拒绝误合并后，重新打开任务仍保留修正结果。
- Markdown 和 JSON 可下载。
- 页面在 390x844、768x1024、1440x900 下可用。

### P2：Schema 抽取和模板

目标：用户可以基于模板或 JSON Schema 从任意文档中抽取结构化 JSON。

后端任务：

1. 新增 `extraction.py`。
2. 支持模板注册：

```text
contract_terms_v1
research_report_summary_v1
invoice_basic_v1
meeting_minutes_v1
policy_document_v1
```

3. 支持用户粘贴 JSON Schema。
4. 支持字段级 evidence map。
5. 支持 schema validation report。
6. 支持抽取缓存和重跑。

前端任务：

1. Schema 模板选择器。
2. JSON Schema 编辑器。
3. 抽取结果 JSON viewer。
4. 字段 evidence 面板。
5. 抽取质量报告。

P2 Definition of Done：

- 合同样本能抽出甲乙方、金额、期限，并有 evidence。
- 研报样本能抽出标题、机构、核心观点、风险提示，并有 evidence。
- 缺失字段返回 null，不编造。

### P3：Wiki、PostgreSQL、Milvus

目标：通用文档解析结果进入可重建的知识库链路。

后端任务：

1. 新增 `data/wiki/documents` package builder。
2. 新增 `document_parser` schema DDL。
3. 新增 importer。
4. 新增 `ingest_document_chunks.py`。
5. API workflow 接入：

```text
/api/workflow/document/{task_id}/wiki-import
/api/workflow/document/{task_id}/db-import
/api/workflow/document/{task_id}/semantic
```

前端任务：

1. 结果页增加“入库”区域。
2. 展示入库状态。
3. 展示 Milvus collection 和 chunk 统计。

P3 Definition of Done：

- 一个 PDF、一个 Word、一个 HTML 样本可入 Wiki。
- importer 连续跑两次不重复。
- Milvus chunk metadata 可回 source map。

### P4：Agent 和跨功能整合

目标：Agent 可以自然使用通用解析产物，聊天附件统一走 document-parser。

任务：

1. 扩展 agent attachment parse。
2. 通用文档 evidence 加入 citation renderer。
3. 工作台 `MyWorkspace` 支持 document_parse artifact。
4. 全局搜索支持文档解析任务和产物。
5. Help 页面补充文档解析说明。

P4 Definition of Done：

- 上传一份合同到聊天，Agent 等待解析并基于 source map 回答。
- 回答引用可打开源页或源块。
- 全局搜索能找到文档解析任务。

## 16. 文件级任务清单

### 后端新增

```text
apps/document-parser/app.py
apps/document-parser/provider_router.py
apps/document-parser/providers/base.py
apps/document-parser/providers/mineru_local.py
apps/document-parser/providers/mineru_cloud.py
apps/document-parser/providers/html_reader.py
apps/document-parser/providers/text_parser.py
apps/document-parser/providers/spreadsheet_parser.py
apps/document-parser/artifacts.py
apps/document-parser/contracts.py
apps/document-parser/quality.py
apps/document-parser/source_map.py
apps/document-parser/comparison.py
apps/document-parser/figures.py
apps/document-parser/table_merge.py
apps/document-parser/extraction.py
apps/document-parser/task_store.py
apps/document-parser/path_config.py
apps/document-parser/run.sh
apps/document-parser/requirements.txt
apps/document-parser/README.md
apps/document-parser/tests/test_contracts.py
apps/document-parser/tests/test_task_api.py
apps/document-parser/tests/test_source_map.py
apps/document-parser/tests/test_comparison_map.py
apps/document-parser/tests/test_figures.py
apps/document-parser/tests/test_table_merge.py
apps/document-parser/tests/test_quality.py
```

### API 新增/修改

```text
apps/api/routers/document_parser.py
apps/api/main.py
apps/api/services/usage_service.py
apps/api/tests/test_document_parser_proxy.py
```

### 前端新增/修改

```text
apps/web/src/pages/DocumentParsing.tsx
apps/web/src/pages/documents/useDocumentTasks.ts
apps/web/src/lib/documentApi.ts
apps/web/src/lib/documentTypes.ts
apps/web/src/components/document-parser/DocumentUploadPanel.tsx
apps/web/src/components/document-parser/DocumentParameterPanel.tsx
apps/web/src/components/document-parser/DocumentTaskList.tsx
apps/web/src/components/document-parser/DocumentResultWorkbench.tsx
apps/web/src/components/document-parser/DocumentComparisonWorkbench.tsx
apps/web/src/components/document-parser/DocumentSourceViewer.tsx
apps/web/src/components/document-parser/DocumentMarkdownPane.tsx
apps/web/src/components/document-parser/DocumentJsonPane.tsx
apps/web/src/components/document-parser/DocumentTablePane.tsx
apps/web/src/components/document-parser/DocumentFigurePane.tsx
apps/web/src/components/document-parser/DocumentImageReferencePanel.tsx
apps/web/src/components/document-parser/DocumentTableMergePanel.tsx
apps/web/src/components/document-parser/DocumentExtractionPanel.tsx
apps/web/src/components/document-parser/DocumentQualityPanel.tsx
apps/web/src/components/document-parser/DocumentArtifactList.tsx
apps/web/src/components/document-parser/DocumentBatchActionBar.tsx
apps/web/src/App.tsx
apps/web/src/lib/routePreload.ts
apps/web/src/components/layout/layoutData.ts
apps/web/src/pages/Help.tsx
apps/web/src/pages/MyWorkspace.tsx
apps/web/src/components/layout/NotificationMenu.tsx
```

### 数据和运维

```text
data/document-parser/.gitkeep
db/ddl/060_create_document_parser_schema.sql
db/imports/import_document_parse_package_to_postgres.py
scripts/vector-index/milvus-ingestion/ingest_document_chunks.py
docs/operations/local-development.md
infra/env/local.example
infra/supervisor/supervisord.conf
start_all.sh
```

## 17. 测试计划

### 17.1 单元测试

```bash
cd /home/maoyd/siq-research-engine/apps/document-parser
python3 -m pytest tests
```

覆盖：

- 文件类型识别。
- 页码范围解析。
- manifest 合同。
- blocks 合同。
- tables 合同。
- figures 合同。
- source map 合同。
- comparison map 合同。
- 跨页断表候选评分、自动合并和拒绝误合并。
- 图片 crop 路径、缩略图路径、source page preview 路径必须落在白名单目录。
- Markdown 图片 anchor 与 `figures.json.markdown_anchor` 一致。
- artifact 白名单。
- URL SSRF 防护。
- zip-slip 防护。
- provider 参数映射。

### 17.2 API 测试

```bash
cd /home/maoyd/siq-research-engine/apps/api
uv run python -m pytest tests/test_document_parser_proxy.py
```

覆盖：

- 未登录禁止访问。
- 非本人任务禁止访问。
- 任务创建记录 usage 和 UserArtifact。
- artifact 代理保留 content-type。
- 图片 artifact 代理正确返回 image content-type。
- `/api/documents/source/{task_id}/image/{image_id}` 返回页码、bbox 和裁剪图 URL。
- 表格合并 review API 可写入 `table_merge_corrections.json`。
- 额度超限返回 429。

### 17.3 前端测试

```bash
cd /home/maoyd/siq-research-engine/apps/web
npm run build
npm run test:e2e -- documents
```

截图验收视口：

```text
390x844
768x1024
1366x768
1440x900
1920x1080
```

重点检查：

- 文本不溢出。
- 左右分屏不互相遮挡。
- 移动端 tabs 可用。
- 批量操作栏不挡内容。
- JSON viewer 大文件时不卡死。
- 源文档和 Markdown 定位可用。
- 点击 Markdown 图片后左侧跳转并高亮源页图片 bbox。
- 点击源文档图片 bbox 后右侧切到图片 tab 并选中对应 `image_id`。
- 跨页断表连接线在桌面视口可见，移动端以关系列表替代。
- 图片卡片、表格单元格、source marker 可键盘聚焦和 Enter 激活。

### 17.4 样本回归集

新增：

```text
eval_datasets/document_parser_cases/
  pdf_cases.json
  office_cases.json
  image_cases.json
  html_cases.json
  figure_reference_cases.json
  cross_page_table_cases.json
  schema_extraction_cases.json
```

每个 case：

```json
{
  "case_id": "contract_pdf_001",
  "kind": "pdf",
  "path": "eval_datasets/document_parser_cases/files/contract_001.pdf",
  "expected_artifacts": [
    "document.md",
    "blocks.json",
    "source_map.json",
    "quality_report.json"
  ],
  "expected_quality_status": "pass|warning",
  "expected_min_blocks": 10,
  "expected_min_source_coverage": 0.95
}
```

## 18. 风险和控制

### 18.1 风险：影响现有财报解析

控制：

- 新增服务、目录、API、前端入口。
- 不改 `apps/pdf-parser` 默认行为。
- 不改 `data/pdf-parser` 结构。
- 不改 `pdf2md` schema。

### 18.2 风险：Office 转换不稳定

控制：

- Provider 层隔离。
- 结果 manifest 记录转换路径。
- 质量报告明确 `conversion_warning`。
- 第一阶段允许 Office 走 fallback，不影响 PDF/TXT 主链路。

### 18.3 风险：大文件导致前端卡顿

控制：

- Markdown 分页或虚拟滚动。
- JSON 延迟加载。
- `blocks.ndjson` 支持流式读取。
- 图片懒加载。

### 18.4 风险：Schema 抽取幻觉

控制：

- 字段必须 evidence。
- validation report 强制显示 evidence 覆盖率。
- null 优先于猜测。
- 用户可查看字段来源。

### 18.5 风险：云解析合规

控制：

- 默认关闭云 provider。
- UI 显式选择。
- manifest 记录外部处理。
- 管理员配置开关。

## 19. 推荐第一轮实施顺序

```text
1. 写合同和路径配置
2. 搭 apps/document-parser 最小服务
3. 做 TXT/MD parser 跑通任务生命周期
4. 接 API 鉴权代理和 UserArtifact
5. 接前端 /documents 最小工作台
6. 接 PDF local MinerU provider
7. 归一 content_list -> blocks/tables/source_map
8. 补 layout/comparison/figures 产物和源文档双栏对照
9. 做跨页断表 logical table 和人工复核
10. 补任务管理、参数面板和下载
11. 做 Office/HTML/image
12. 做 Schema 抽取
```

第一轮不要一上来做公共库大重构。先让通用解析完整闭环，再把重复代码抽到公共层。

## 20. 验收总标准

完成后应满足：

- 用户能在“文档解析”入口上传任意常见文档并获得 Markdown / JSON / 表格 / 图片 / 质量报告。
- Markdown 段落、表格单元格、图片、图注和 JSON block 都能回跳到源文档页与 bbox。
- 跨页断表能输出物理片段、逻辑合并表、合并关系和人工复核记录。
- 图片和图表能输出裁剪图、caption/OCR、source map、Wiki/Milvus 入库 metadata。
- 任意结构化抽取字段都能看到 evidence 或明确缺失原因。
- 通用解析不会污染财报解析的市场 tabs、财务抽取和 evidence package。
- 后端有独立运行态目录和任务队列。
- API 访问受用户权限保护。
- 前端在桌面和移动端均能完成一次解析、查看结果和下载产物。
- 后续可以平滑接入 Wiki、PostgreSQL、Milvus 和 Agent。
