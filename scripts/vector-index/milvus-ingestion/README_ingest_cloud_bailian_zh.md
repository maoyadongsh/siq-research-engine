# Cloud Bailian Milvus 入库脚本说明

本文档说明 `ingest_cloud_bailian.py` 的用途、启动方式、UI 参数、入库流程、VGraph 图谱同步、Dry run、暂停继续、质量报告和常见问题。

该脚本是一个云端版文档入库工具：解析本地材料，调用阿里百炼/DashScope 生成 embedding 和视觉 caption，写入 Milvus，并可选同步到 Vector Graph RAG 侧边集合。

脚本不依赖本地 MinerU、vLLM、OCR、reranker 或其他本地模型服务。

## 功能概览

- 支持 PDF、DOCX、Markdown、TXT、图片。
- 支持 PDF 文本抽取、PDF 页面渲染、DOCX 标题结构、Markdown 表格和图片引用。
- 文本、表格、视觉 caption 文本使用 `text-embedding-v4`。
- 图片和 PDF 页面视觉块使用 `qwen3-vl-embedding`。
- 视觉 caption 默认使用 `qwen3-vl-flash`。
- 主 Milvus collection 写入 `id`、`vector`、`project_tag`、`metadata`。
- 可选同步 VGraph：
  - `<prefix>_vgrag_passages`
  - `<prefix>_vgrag_entities`
  - `<prefix>_vgrag_relations`
- 支持运行日志、自动刷新状态、暂停/继续、Dry run、质量报告。

## 快速启动

```bash
cd /home/maoyd/.openclaw/workspace/ic_master_coordinator_workspace
/home/maoyd/miniconda3/bin/python ingest_cloud_bailian.py --ui --host 0.0.0.0 --port 7863
```

打开：

```text
http://127.0.0.1:7863/
```

## UI 使用流程

1. 上传文件，或填写本地文档目录。
2. 填写 `project_tag`，例如 `SIQ-PROJECT-2026`。
3. 选择 `Milvus Database`。
4. 选择目标 `Collection`。
5. 填写阿里百炼 API Key，或设置环境变量 `DASHSCOPE_API_KEY`。
6. 按需开启功能开关。
7. 点击 `开始入库`。

页面会每 2 秒自动刷新运行状态和运行日志。

## 页面参数说明

### Milvus Database

目标 Milvus database。点击 `刷新 Database` 会从 Milvus 读取 database 列表。切换 database 后，目标 collection 列表会自动刷新。

### 目标 Collection

主入库 collection。常见角色 collection：

- `ic_collaboration_shared`
- `ic_chairman`
- `ic_finance_auditor`
- `ic_sector_expert`
- `ic_legal_scanner`
- `ic_strategist`
- `ic_risk_controller`
- `ic_master_coordinator`
- `ic_archive_sop`

也支持自定义 collection 名称。

### 启用多模态 visual_chunk

为图片和 PDF 页面创建视觉块。扫描版 PDF、图片型材料、图表页建议开启。

### 生成视觉 caption

调用百炼视觉模型，从图片或页面中提取可检索证据，例如公司、产品、客户、供应商、财务指标、风险事项、时间和金额。

扫描 PDF 强烈建议开启。

### 识别 Markdown 表格块

识别 Markdown 表格，并额外生成 `table_chunk`。

### PDF 页面渲染为视觉块

把 PDF 页面渲染成图片，再生成 `visual_chunk`。适合扫描版 PDF 或图表页。

### PDF 视觉页数上限

限制最多渲染多少页 PDF 作为视觉块。

- `0`：不限制
- `8`：UI 默认值

大文件建议先设置较小页数试跑，确认效果和成本后再全量入库。

### 同步到 Vector Graph RAG passages

开启后，脚本会写入 VGraph 三个侧边 collection，并抽取规则型 entities/relations。

写入目标：

```text
<graph_prefix>_vgrag_passages
<graph_prefix>_vgrag_entities
<graph_prefix>_vgrag_relations
```

### Graph prefix

控制 VGraph 三个侧边 collection 的前缀。

一般建议留空。留空时脚本会使用目标 collection 名作为 prefix。

例如目标 collection 是：

```text
cloud_bailian_smoke_test
```

则 VGraph collection 为：

```text
cloud_bailian_smoke_test_vgrag_passages
cloud_bailian_smoke_test_vgrag_entities
cloud_bailian_smoke_test_vgrag_relations
```

只有当多个 collection 需要共用同一套图谱命名空间时，才手动填写同一个 `Graph prefix`。

### 重建目标 Collection

开启后会删除并重建主目标 collection。谨慎使用。

### 重建 Graph sidecar

开启后会删除并重建 VGraph 侧边 collection。谨慎使用。

### Dry run（不调用 API，不写 Milvus）

试跑模式。勾选后：

- 会读取文件
- 会解析文档
- 会生成 chunk
- 会写质量报告
- 不调用百炼 API
- 不生成 embedding
- 不写 Milvus
- 不写 VGraph

适合预检文件解析质量、chunk 数量、页码覆盖、表格识别、视觉块识别和 metadata 样例。

### 暂停 / 继续

暂停是协作式暂停。它会在下一次 caption、下一次 visual embedding、下一份文件或写 Milvus 前停住。

已经发出去的单次 API 请求不会被强制中断。

## Chunk 类型

### `text_chunk`

从 PDF、DOCX、Markdown、TXT 中抽取的文本块。使用 `text-embedding-v4`。

### `table_chunk`

从 Markdown 或 DOCX 中识别出的表格证据。使用 `text-embedding-v4`。

### `visual_chunk`

图片或 PDF 页面渲染图。使用 `qwen3-vl-embedding`。

### `caption_text_chunk`

从有价值的视觉 caption 二次生成的文本块。使用 `text-embedding-v4`。

这个类型能明显改善扫描 PDF 对自然语言查询的召回效果。

## 低信息视觉页过滤

视觉 caption 后，脚本会自动跳过低信息页面，例如：

- 纯白背景
- 空白页
- 无任何可识别文字
- 无有效信息
- 没有公司、产品、财务、风险、时间、金额等证据

这些页面会被标记为 skipped，不会进入 embedding，也不会写入 Milvus。

## 重复入库处理

写入主 collection 前，脚本会尝试按 `chunk_uid` 删除同一 `project_tag` 和同一 `source_path` 下已有的重复 chunk，再插入新结果。

注意：UI 上传文件每次会进入不同的上传目录，因此同一个文件反复上传时，`source_path` 可能不同，旧上传路径下的数据未必会被自动删除。

## VGraph 实体和关系抽取

开启 `同步到 Vector Graph RAG passages` 后，脚本会：

1. 把有效 chunk 写入 `<prefix>_vgrag_passages`。
2. 从 chunk 文本和 caption 中规则抽取 entities。
3. 基于同一 chunk 中的实体生成 relations。
4. 把 `entity_ids` 和 `relation_ids` 写到 passage 行中。

### 抽取 Profile

脚本会根据 `doc_type` 使用不同抽取口径：

- `teaser`：公司、产品、地区、产能指标、认证、能力、相关方、日期
- `financials`：公司、财务指标、产能指标、日期、风险、相关方
- `legal`：公司、法律条款、法规、风险、日期、相关方
- `industry_research`：公司、产品、地区、产能指标、财务指标、日期
- `meeting_note`：公司、产品、地区、财务指标、风险、相关方、日期
- `committee_opinion`：公司、产品、财务指标、风险、相关方、日期
- `sop`：公司、能力、风险、相关方
- `default`：启用全部规则组

### 常见关系谓词

- `涉及产品`
- `覆盖区域`
- `具备产能`
- `具备指标`
- `获得认证`
- `具备能力`
- `具备财务指标`
- `包含条款`
- `适用法规`
- `存在风险`
- `发生时间`
- `相关方`

## 命令行示例

### 启动 UI

```bash
/home/maoyd/miniconda3/bin/python ingest_cloud_bailian.py \
  --ui \
  --host 0.0.0.0 \
  --port 7863
```

### Dry run 试跑

```bash
/home/maoyd/miniconda3/bin/python ingest_cloud_bailian.py \
  --no-ui \
  --dry-run \
  --input-dir /path/to/docs \
  --project-tag SIQ-PROJECT-2026 \
  --collection cloud_bailian_smoke_test \
  --db-name default \
  --enable-visual \
  --enable-pdf-page-visuals \
  --max-pdf-visual-pages 3
```

### 正式入库

```bash
export DASHSCOPE_API_KEY='sk-...'

/home/maoyd/miniconda3/bin/python ingest_cloud_bailian.py \
  --no-ui \
  --input-dir /path/to/docs \
  --project-tag SIQ-PROJECT-2026 \
  --milvus-uri http://127.0.0.1:19530 \
  --db-name default \
  --collection cloud_bailian_smoke_test \
  --enable-visual \
  --enable-captions \
  --enable-pdf-page-visuals \
  --max-pdf-visual-pages 8 \
  --enable-vgrag-passages
```

### 远程 Milvus / Zilliz Cloud

```bash
/home/maoyd/miniconda3/bin/python ingest_cloud_bailian.py \
  --no-ui \
  --input-dir ./docs \
  --project-tag SIQ-PROJECT-2026 \
  --milvus-uri https://example.api.zillizcloud.com \
  --milvus-token "$ZILLIZ_TOKEN" \
  --db-name default \
  --collection ic_collaboration_shared
```

## 质量报告

入库或 dry-run 后，质量报告会写入：

```text
ingest_quality_reports/
```

报告内容包括：

- 来源文件
- collection
- project_tag
- chunk 总数
- valid/skipped 数量
- chunk 类型统计
- modality 统计
- 是否包含 visual/table chunks
- citation 样例
- chunk 文本预览
- skipped 原因

建议每次正式入库后查看最新质量报告。

## 检索注意事项

查询 embedding 模型要和入库 chunk 类型匹配：

- 文本型 collection：优先用 `text-embedding-v4` 生成 query vector。
- 视觉型 collection：优先用 `qwen3-vl-embedding` 生成 query vector。
- 混合型 collection：建议 hybrid 检索或两路召回后 rerank。

扫描 PDF 建议保留 `caption_text_chunk`，这样自然语言查询可以命中文本 embedding。

## 常见问题

### `InvalidApiKey`

阿里百炼 API Key 无效。只粘贴 `sk-...` 这一段，不要粘贴说明文字。

### `latin-1 codec can't encode characters`

API Key 输入框里混入了中文或非 ASCII 字符。脚本会清理常见前缀，例如 `Bearer`、`DASHSCOPE_API_KEY=`，但 key 本体必须是 ASCII。

### Database 下拉只有一个

点击 `刷新 Database`。脚本会通过 `MilvusClient.list_databases()` 获取 database 列表。

### Database 切换后 Collection 没更新

当前脚本已绑定联动刷新。如果仍然没更新，点击 `刷新 Collection`。

### VGraph entities/relations 是 0

旧版本只写 passages。使用当前版本重新入库，并开启 `同步到 Vector Graph RAG passages`。

### 空白页影响召回

当前版本会过滤低信息视觉页。旧数据需要重新入库才能生效。

### 财务类问题召回偏弱

财务页通常依赖表格或图表。建议开启视觉 caption，并后续结合财务字段抽取、关键词召回或 rerank。

## 推荐设置

### 文本型 PDF

- 开启表格识别。
- 视觉解析可选。
- `PDF 视觉页数上限` 设置较小，除非图表很重要。

### 扫描版 / 图片型 PDF

- 开启 `启用多模态 visual_chunk`。
- 开启 `生成视觉 caption`。
- 开启 `PDF 页面渲染为视觉块`。
- 先设置较小页数试跑。
- 确认质量后再全量入库。
- 如需图谱检索，开启 `同步到 Vector Graph RAG passages`。

### 重复测试

- 先用 `Dry run`。
- 使用测试 collection，例如 `cloud_bailian_smoke_test`。
- 查看 `ingest_quality_reports/`。
- 确认无误后再入正式 collection。

## 安全注意事项

- 不要把 API Key 写进代码或提交到仓库。
- 命令行建议使用环境变量 `DASHSCOPE_API_KEY`。
- UI 密码框只是在浏览器中隐藏显示，脚本运行时仍会使用该 key 调用 API。

