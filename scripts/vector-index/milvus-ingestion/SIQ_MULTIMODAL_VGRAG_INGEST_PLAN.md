# SIQ 项目底稿多模态入库与 Vector Graph RAG 建设计划书

版本：v1.1  
日期：2026-05-01  
范围：SIQ 全部项目的 `ic_collaboration_shared` 项目底稿库优先，不改造各 Agent 私有背景库

## 1. 项目目标

本项目目标是把 SIQ 投委会项目底稿库从“文本向量库”升级为“可追溯、多模态、支持关系推理的项目证据库”。

最终效果：

- 项目 PDF、MD、DOCX 等底稿可以高质量入库。
- 文本、表格、图片、图表都能形成可检索证据。
- 每条证据都能追溯到原始文件、页码、章节、chunk id。
- Milvus 同时支持普通语义检索和 Vector Graph RAG 多跳关系检索。
- Agent 输出从“泛化回答”升级为“证据绑定型专业判断”。

## 2. 设计原则

1. `ic_collaboration_shared` 是所有项目事实底稿的主证据库，以 `project_tag` 做项目隔离。
2. `vgrag_*` collections 是关系推理 sidecar，不替代主库；默认共享一组 sidecar collections，并以 `project_tag` 做隔离。
3. PDF 作为原始证据保留，MinerU 输出的 MD/images/content_list 作为入库中间格式。
4. 所有 chunk 必须有统一 metadata，不允许无来源、无正文、无页码线索的脏数据进入主库。
5. Graph RAG 只提供关系链证据，不直接产生投委会结论。
6. 方案必须兼容多个项目并行入库、重建和检索，不能绑定某一个项目。
7. 先重建项目底稿库，私有背景库暂不纳入 Graph RAG。

## 3. 目标架构

```text
原始项目底稿
  PDF / DOCX / MD / 图片
        |
        v
MinerU 高质量解析层
  Markdown / images / content_list / middle_json
        |
        v
SIQ 入库管线 ingest_final.py
        |
        |-- text_chunk   -> ic_collaboration_shared
        |-- table_chunk  -> ic_collaboration_shared
        |-- visual_chunk -> ic_collaboration_shared
        |
        v
Vector Graph RAG Sidecar
        |
        |-- passages  -> siq_project_vgrag_passages
        |-- entities  -> siq_project_vgrag_entities
        |-- relations -> siq_project_vgrag_relations
```

## 4. Milvus Collection 规划

### 4.1 主证据库

`ic_collaboration_shared`

用途：

- 当前项目事实底稿
- 文本证据
- 表格证据
- 图片/图表 caption 证据
- 视觉 chunk

现有 schema 保持不变：

```text
id: INT64 auto_id
vector: FLOAT_VECTOR(1024)
project_tag: VARCHAR
metadata: JSON
```

### 4.2 Vector Graph RAG Sidecar

默认 prefix：`siq_project`

Collections：

```text
siq_project_vgrag_passages
siq_project_vgrag_entities
siq_project_vgrag_relations
```

VGRAG schema：

```text
id: VARCHAR primary key
vector: FLOAT_VECTOR(1024)
text: VARCHAR
dynamic fields: enabled
metric: IP
```

用途：

- `passages`：同步项目底稿 chunk。
- `entities`：存项目实体，如公司、客户、供应商、产品、政策、地区、人员、投资方。
- `relations`：存实体之间的关系，如“公司-交付-产品”“客户-位于-地区”“政策-影响-市场”。

多项目隔离策略：

- 默认：所有项目共享 `siq_project_vgrag_*` 三张 sidecar collection，通过 `project_tag` 过滤。
- 大项目或敏感项目：可使用独立 prefix，例如 `{project_tag_slug}_vgrag_*`。
- 不允许不同项目复用临时 tag，例如 `ingest_0501_1200`。
- `project_tag` 是主库和 sidecar 之间的核心 join key。

推荐项目 tag：

```text
SIQ-{COMPANY_OR_PROJECT}-{YEAR}
SIQ-{COMPANY_OR_PROJECT}-{ROUND}-{YEAR}
```

示例：

```text
SIQ-YUSHU-IPO-2026
SIQ-HAIFENG-A-2026
SIQ-ROBOTICS-SERIESB-2026
```

## 5. Metadata 标准

主库所有 chunk 使用 `siq_chunk_v1`。

必备字段：

```json
{
  "schema_version": "siq_chunk_v1",
  "project_tag": "SIQ-{PROJECT}-{YEAR}",
  "type": "text_chunk|table_chunk|visual_chunk",
  "modality": "text|table|image|chart|page",
  "text": "可检索、可引用正文或caption",
  "source": "{source_file}.pdf",
  "source_path": "/abs/path/{source_file}.pdf",
  "page": 3,
  "section_path": "业务及产品/关键章节",
  "doc_type": "teaser|financials|legal|industry_research|meeting_note|sop",
  "evidence_level": "source_doc|regulation|research|methodology|expert_opinion",
  "chunk_uid": "sha1...",
  "citation": "{source_file}.pdf | p.3 | 业务及产品"
}
```

Graph relation 必须回指 passage：

```json
{
  "subject": "{项目公司或实体}",
  "predicate": "{关系}",
  "object": "{目标实体}",
  "passage_id": "chunk_uid",
  "project_tag": "SIQ-{PROJECT}-{YEAR}",
  "source": "{source_file}.pdf",
  "page": 3,
  "confidence": 0.86
}
```

## 6. 分阶段实施计划

### 阶段 0：入库前准备

目标：确保重建前环境、目录、命名、回滚点明确。

任务：

- 确认 Milvus、embedding、reranker、MinerU 服务健康。
- 确认 `ingest_final.py` 编译通过。
- 确认每个项目的 `project_tag` 命名。
- 为每个项目建立标准目录，项目目录名必须与 `project_tag` 一致。
- 建立项目级 `ingest_manifest.json`，记录项目名、公司名、底稿清单、Graph prefix 策略。

推荐目录模板：

```text
projects/{project_tag}/
  source/
    *.pdf
    *.docx
    *.md
  mineru/
    {document_stem}/
      result.md
      images/
      content_list.json
      middle.json
  normalized/
    *.md
  ingest_manifest.json
```

`ingest_manifest.json` 示例：

```json
{
  "project_tag": "SIQ-{PROJECT}-{YEAR}",
  "company_name": "{公司名}",
  "industry": "{行业}",
  "graph_prefix": "siq_project",
  "source_docs": [
    {
      "file": "source/{source_file}.pdf",
      "doc_type": "teaser",
      "evidence_level": "source_doc",
      "mineru_output": "mineru/{document_stem}/result.md"
    }
  ]
}
```

交付物：

- 项目目录结构
- 入库批次清单
- 重建前 collection 计数快照

验收标准：

- 服务健康检查通过。
- 小样本文件路径明确。
- project_tag 不再使用临时 `ingest_xxxx`。

### 阶段 1：MinerU 作为 PDF 高质量解析层

目标：PDF 不再依赖 PyMuPDF 粗解析，而是优先转成 MD + assets。

任务：

- 使用本机 MinerU API 解析 PDF。
- 输出 Markdown、图片、content_list、middle_json。
- 保留原 PDF。
- 建立 PDF 到 MD/assets 的映射关系。

推荐 MinerU 参数：

```text
backend: hybrid-http-client
parse_method: auto
server_url: http://127.0.0.1:8002
return_md: true
return_middle_json: true
return_content_list: true
return_images: true
lang_list: ch
```

交付物：

- `mineru/*.md`
- `mineru/images/*`
- `mineru/content_list.json`
- `mineru/middle.json`

验收标准：

- MD 中保留页码标记。
- 表格不大量丢失。
- 图片/图表文件可定位。
- 原文件、页码、解析结果可以互相追溯。

### 阶段 2：文本与表格证据入库

目标：先把高质量 MD 中的正文和表格作为可引用证据进入 `ic_collaboration_shared`。

任务：

- 用 `ingest_final.py` 入库 MinerU 生成的 MD。
- 对 Markdown 标题、页码、表格进行结构化 chunk。
- 对表格生成 `table_chunk` 或至少保留表格 HTML/Markdown 到 `text`。
- 写入统一 metadata。

交付物：

- 重建后的 `ic_collaboration_shared`
- 每条记录带 `siq_chunk_v1`
- 每条记录有 `citation`

验收标准：

- 抽样记录中 `text` 非空。
- 抽样记录中 `section_path`、`source`、`page` 尽量完整。
- 表格能通过关键词检索召回。
- 同一项目只使用稳定 `project_tag`。

### 阶段 3：图片、图表 caption 入库

目标：让图表、产品图、架构图至少能通过 caption/OCR/table text 被文本检索召回。

任务：

- 从 MinerU content_list 中识别 image/chart/table 项。
- 为图片/图表生成 caption 或使用 MinerU 输出说明。
- 将 caption 写入 metadata 和 `text`。
- chunk 类型标记为 `visual_chunk`。

`visual_chunk` 示例：

```json
{
  "type": "visual_chunk",
  "modality": "chart",
  "text": "该图展示项目公司某项业务、产品、客户、财务或风险信息。",
  "image_path": "mineru/{document_stem}/images/page_3_chart_2.jpg",
  "page": 3,
  "source": "{source_file}.pdf",
  "section_path": "业务及产品",
  "evidence_level": "source_doc"
}
```

交付物：

- 图表 caption chunk
- 图片/图表 source/page/image_path metadata

验收标准：

- 可以检索到“收入结构图”“股权结构图”“产品示意图”等视觉证据。
- Agent 能引用图表所在页。
- 没有图片路径丢失。

### 阶段 4：视觉 embedding 增强

目标：让图片/图表本身也能参与语义检索。

任务：

- 使用 `Qwen3-VL-Embedding-2B` 对图片或页面截图生成 embedding。
- 对 visual chunk 同时保留 caption text embedding 和 image embedding 策略。
- 如果同一 collection 单向量无法表达多向量，优先使用 caption/text embedding；视觉向量作为增强字段或 sidecar 后续处理。

交付物：

- visual chunk 可检索
- visual metadata 标记 `visual_embedding`

验收标准：

- 文字查询可以召回相关图表。
- 图片/图表证据不会破坏普通文本检索。

### 阶段 5：同步 passages 到 VGRAG sidecar

目标：项目底稿 chunk 同步到 Vector Graph RAG passage 层。

任务：

- 初始化 VGRAG sidecar collections。
- 入库 `ic_collaboration_shared` 时同步 passages。
- passage id 使用 `chunk_uid`。
- passage metadata 保留 project_tag、source、page、citation。
- 所有 Graph 查询默认必须带 `project_tag` 过滤。
- 共享 sidecar 模式下，禁止跨项目扩展子图，除非用户明确要求跨项目类比。

交付物：

- `siq_project_vgrag_passages`
- 与主库一致的 passage 证据

验收标准：

- passages 数量与主库有效 chunk 基本一致。
- passage id 可回查主库 metadata。
- sidecar 不污染主库 schema。

### 阶段 6：实体关系抽取与 Graph RAG 多跳

目标：让项目底稿支持多跳关系检索。

任务：

- 从 text/table/visual chunk 中抽取实体。
- 抽取 subject-predicate-object triplets。
- 写入 `vgrag_entities` 和 `vgrag_relations`。
- relation 必须回指 passage id。

优先实体类型：

```text
公司、子公司、客户、供应商、产品、地区、政策、投资方、股东、管理层、合同、风险事件、财务指标
```

优先关系类型：

```text
生产/交付/供应/投资/持股/依赖/位于/适用/影响/披露/增长/下降/存在风险
```

交付物：

- entity collection
- relation collection
- 多跳检索 demo query

验收标准：

- 能回答关系链问题：
  - “项目公司、客户、政策、收入风险之间有什么关系？”
  - “哪些供应链节点可能形成一票否决风险？”
  - “哪些表格或图表支持该风险判断？”

## 7. 入库执行顺序

建议今天执行到阶段 2 或阶段 3。

推荐顺序：

```text
1. 选任意一个项目小样本 PDF
2. MinerU 转 MD + assets
3. 用 MD 小样本入 ic_collaboration_shared
4. 抽查 metadata
5. 正式重建 ic_collaboration_shared
6. 可选同步 vgrag_passages
7. 后续再做 visual_chunk 和 entity/relation
```

不建议今天直接全量做：

- 所有私有库 Graph RAG
- 全量法律库 Graph RAG
- 自动把 R1/R2/R3 观点写入 graph

## 8. 质量评测方案

建立 30-50 条项目底稿检索评测 query。

样例：

```json
{
  "project_tag": "SIQ-{PROJECT}-{YEAR}",
  "query": "项目公司的关键业务能力体现在哪些底稿页？",
  "expected_source": "{source_file}.pdf",
  "must_include": ["客户", "产品", "交付"],
  "expected_modality": ["text", "chart"]
}
```

评测指标：

- Recall@10
- Source hit rate
- Has text rate
- Has citation rate
- Page traceability rate
- Table/chart recall rate

## 9. 风险与应对

| 风险 | 表现 | 应对 |
| --- | --- | --- |
| PDF 直接抽取质量差 | 表格错乱、图片丢失 | 优先 MinerU 转 MD |
| visual chunk 噪声大 | 检索到无意义图片 | 先 caption 入库，再开视觉 embedding |
| Graph relation 幻觉 | 关系不在底稿中 | relation 必须绑定 passage_id |
| project_tag 混乱 | 跨项目污染 | 使用稳定项目 tag |
| sidecar 跨项目污染 | 多跳扩展召回其他项目关系 | Graph 查询强制带 project_tag |
| 全量重建耗时长 | 中途失败 | 小样本验证后再全量；保留 reset manifest |
| 表格丢结构 | 财务指标召回差 | MinerU content_list + table_chunk |

## 10. 当前推荐决策

短期执行：

```text
PDF -> MinerU -> MD/images/content_list -> ingest_final.py -> ic_collaboration_shared
```

同时保留：

```text
ic_collaboration_shared -> siq_project_vgrag_passages
```

暂缓：

```text
私有知识库 Graph RAG
R1/R2/R3 观点入图谱
全量视觉 embedding 强制开启
```

## 11. 验收清单

- [ ] 小样本 MinerU 解析成功。
- [ ] MD 保留页码、标题、表格、图片引用。
- [ ] 小样本入库成功。
- [ ] 抽样 metadata 包含 `schema_version`、`project_tag`、`text`、`source`、`page`、`citation`、`chunk_uid`。
- [ ] 普通语义检索可以召回项目事实。
- [ ] 表格内容可通过关键词检索。
- [ ] 图表 caption 可检索。
- [ ] 可选：passages 已同步到 VGRAG sidecar。
- [ ] 全量重建前 reset manifest 生效。
