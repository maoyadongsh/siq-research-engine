# 一级市场招股书材料中心优化方案与可执行任务书

> 日期：2026-07-13
>
> 文档编号：SIQ-PMM-PROSPECTUS-2026-07-13
>
> 状态：待实施
>
> 适用范围：`apps/web`、`apps/api`、`apps/pdf-parser`、Deal OS、Evidence Service、IC Hermes runtime
>
> 关联方案：`2026-07-04-primary-market-deal-os-v2-redesign.md`、`2026-07-06-primary-market-ic-agent-effectiveness-development-plan.md`、`2026-07-12-siq-intelligent-research-platform-optimization-plan.md`、`2026-07-13-primary-market-ic-openclaw-behavior-parity-optimization-plan.md`

## 0. 执行结论

一级市场材料中心应增加“招股书”专用材料工作流。用户在选定 Deal 后上传 PDF，系统自动完成原件归档、PDF parser 提交、任务恢复、解析产物归档、质量判定、分析源注册、Evidence 构建和 IC 检索快照刷新。

本方案锁定以下设计：

1. `data/wiki/deals/<deal_id>` 继续作为一级市场项目的权威业务归档，不新增平行的全局招股书根目录。
2. 招股书继续使用现有 `DealDocument` 业务对象，通过 `document_type=prospectus` 和 `document_profile=cn_a_share_prospectus` 扩展，不创建重复的 Material 主实体。
3. 原始 PDF 存入 Deal data room；PDF parser 自身目录只承担运行职责，不能作为一级市场长期引用的唯一位置。
4. 每次解析生成不可变 `parse_run_id`，核心解析产物按 manifest 校验后归档到 Deal 的固定目录；重解析不得覆盖旧 run。
5. 招股书上传必须通过 FastAPI 认证、Deal ACL、配额、限流和 `UserArtifact` 归属边界，浏览器不得直连 PDF parser。
6. 解析成功不等于自动成为完整事实源。分析能力按文本证据、页码追溯、财务事实和语义索引分别判定。
7. 激活或替换招股书分析源后必须生成新的 `evidence_snapshot_hash`；旧 startup receipt 和未完成的 IC 正式报告不得继续被当作当前证据快照。
8. 第一阶段复用现有 CN PDF 解析能力；后续增加 A 股招股书章节识别、报告期检查和专属质量规则。

目标用户体验是：

```text
选择一级市场项目
  -> 选择“招股书”
  -> 上传 PDF
  -> 查看解析进度与质量状态
  -> 系统自动或经人工确认启用为分析源
  -> IC 智能体基于同一证据快照开展分析
```

普通用户不再手工输入 parser task ID。旧的手工绑定入口仅作为兼容和管理员修复能力保留。

## 1. 背景与现状基线

### 1.1 已有能力

当前系统已经具备以下基础：

- Web 已有 `/primary-market/materials` 一级市场材料中心。
- Deal OS 已有 `data_room/raw`、`data_room/metadata`、`parsed_documents` 和 `evidence` 目录。
- `apps/api/services/deal_documents.py` 已支持分块保存上传文件、SHA256、大小限制、元数据和 Deal audit。
- `/api/deals/{deal_id}/documents` 已具备 `report.create` 和 Deal 对象级权限检查。
- `/api/pdf/upload` 已具备认证代理、上传缓冲、单文件/批次限制、显式 timeout、配额、SHA256 去重和 `UserArtifact(parse)` 记录。
- PDF parser 已输出 Markdown、content list、document full、财务数据、财务检查、质量和 result manifest 等产物。
- Deal Evidence、startup retrieval、R1 agent runtime 和一级市场会议室已经能读取 Deal 项目上下文。

### 1.2 当前断点

当前材料中心的实际链路是：

```text
上传普通材料
  -> 写入 Deal data_room/raw
  -> 用户手工填写 task_id
  -> 绑定 document-parser 任务
  -> Deal Evidence 从 DOCUMENT_PARSER_RESULTS_ROOT 读取 document.md
```

它与目标存在以下差距：

| 编号 | 差距 | 影响 |
| --- | --- | --- |
| G1 | 材料类型没有 `prospectus` | 招股书无法被明确识别和路由 |
| G2 | 上传与解析是两个手工步骤 | 用户需要理解 parser task，容易产生孤儿材料或错误绑定 |
| G3 | 当前绑定固定指向 document-parser | 无法直接使用项目已有 PDF parser 的财务、质量和原文追溯能力 |
| G4 | Evidence builder 固定读取 `DOCUMENT_PARSER_RESULTS_ROOT` | 即使 PDF 解析成功，也不会自动进入 Deal Evidence |
| G5 | parser results 是运行目录 | parser 清理、迁移或重跑后，Deal 引用可能失效 |
| G6 | 文档状态只有 `uploaded/parse_bound` 等扁平值 | 无法区分原件状态、解析状态、分析源状态和索引状态 |
| G7 | 没有招股书版本链 | 申报稿、更新稿、注册稿和最终版容易互相覆盖或混用 |
| G8 | 没有证据快照失效机制 | 新招股书启用后，旧 receipt/报告仍可能被误当成当前结论 |
| G9 | `market=CN` 过于宽泛 | 年报与招股书的章节、报告期和质量规则不同 |
| G10 | 解析成功后没有分析能力分级 | 财务勾稽失败的文档可能被错误用于确定性财务结论 |

### 1.3 与现有优化方案的关系

本方案不改变以下既定边界：

- 不公开 `/pdfapi`，所有浏览器请求继续经过 FastAPI `/api/*`。
- 不让一级市场数据进入二级市场 `companies` 主链路。
- 不把 Milvus 召回直接当作财务数字事实来源。
- 不让 Hermes profile 自行写 Deal package；API 服务层负责产物、状态和审计。
- 不引入 Kafka、Celery 或 Temporal；优先复用 parser task store、Deal metadata 和现有 job/lease 机制。
- 不重写现有材料中心和 PDF parser，只增加有边界的专用编排和适配层。

## 2. 目标与非目标

### 2.1 产品目标

1. 用户在材料中心一次上传即可发起 A 股招股书解析。
2. 用户能看到上传、排队、解析、归档、质量、分析源和索引状态。
3. 用户能查看原始 PDF、解析 Markdown、质量结果和原文页码。
4. 同一项目可管理多版招股书，并明确当前生效版本。
5. 可用解析产物自动进入 Deal Evidence，并成为 IC 智能体的共享项目分析源。
6. 新证据启用后，系统能识别旧 receipt、报告和决策的证据快照是否过期。

### 2.2 工程目标

1. 建立稳定的 Deal 招股书目录和版本化 parse run 合同。
2. 将 `/api/pdf/upload` 中可复用的提交逻辑抽到 application service，避免路由互调。
3. 建立可恢复、幂等的解析状态同步与产物提升流程。
4. 让 Deal Evidence 支持 `pdf` 和 `document` 两种 parser artifact provider。
5. 建立一级市场分析源和 `ResearchIdentity` 的显式 domain 边界。
6. 为上传、任务归属、目录穿越、重启恢复、重复提交和跨用户访问补自动化测试。

### 2.3 非目标

首期不做：

- 不自动从公开网站下载全部 A 股招股书；本期只处理用户上传。
- 不在上传时强制识别证券代码；未上市发行人可能没有正式证券代码。
- 不一次性完成所有招股书结构化专题抽取。
- 不将招股书全文直接塞进 Agent prompt。
- 不在本期改变 R0-R4 主状态机语义。
- 不物理迁移全部历史 document-parser 材料。
- 不允许分析源激活自动覆盖已经人工确认的 R4 历史决策。

## 3. 核心设计决策

| 决策 | 结论 | 原因 |
| --- | --- | --- |
| 业务根目录 | 复用 `WIKI_ROOT/deals/<deal_id>` | 与 Deal ACL、Evidence、IC workflow 和归档保持同一边界 |
| 业务实体 | 扩展 `DealDocument` | 避免 Document/Material 两套生命周期和权限模型 |
| 用户入口 | 一级市场专用 façade API | 保持产品语义，同时复用 Deal application service |
| parser 调用 | 后端 application service 调用 parser client | 避免浏览器旁路和 FastAPI 路由自调用 |
| parser 产物 | 运行目录 + Deal 不可变归档双层 | 兼顾 parser 执行效率和 Deal 长期可追溯性 |
| 版本模型 | 新文件新 document；重解析新 parse run | 分离业务版本和技术解析版本 |
| 当前版本 | `current.json` 保存指针，不使用 symlink | 跨容器、备份和对象存储迁移更稳定 |
| 分析启用 | 质量能力分级 | 避免财务失败导致全文完全不可用，也避免错误财务数字进入正式分析 |
| 去重 | Deal 内业务去重，parser 内 owner/config 去重 | 不跨 Deal 泄露私有材料是否存在 |
| 删除 | 被引用后只能 supersede/disable | 保留报告和审计链可重放性 |
| Agent 身份 | `domain=primary_market` + Deal 命名空间 | 避免误入二级市场公司、报告或 legacy A 股 fallback |
| receipt 版本 | 绑定 `evidence_snapshot_hash` | 防止 Agent 基于旧招股书继续发言 |

## 4. 目标架构

```text
PrimaryMarketMaterials.tsx
  -> /api/primary-market/projects/{deal_id}/materials/prospectuses
  -> primary_market_materials application service
       -> Deal access / upload validation / raw archive
       -> pdf_parse_submission service
            -> PDF parser /api/upload
            -> quota + UserArtifact(parse)
       -> material metadata + parse run state

Parser task
  -> queued / processing / completed / failed
  -> primary_market_material_reconciler
       -> result manifest validation
       -> immutable artifact promotion
       -> prospectus quality decision
       -> analysis source capability registration
       -> Deal Evidence rebuild/index request
       -> evidence snapshot refresh

IC Agent Runtime
  -> startup receipt bound to evidence_snapshot_hash
  -> role-aware retrieval over active Deal sources
  -> report stores source/evidence snapshot identity
```

### 4.1 职责边界

| 模块 | 职责 | 禁止承担的职责 |
| --- | --- | --- |
| Web 材料中心 | 上传、状态展示、人工确认、版本操作 | 不直连 parser，不拼服务器路径 |
| Primary Market router | HTTP 参数、权限、响应映射 | 不直接复制文件或轮询 parser |
| `primary_market_materials` | 用例编排、状态迁移、业务版本 | 不实现 MinerU/PDF 解析算法 |
| `pdf_parse_submission` | parser 请求、去重、配额、UserArtifact | 不决定 Deal 业务版本 |
| reconciler | 恢复、状态同步、产物提升、幂等重试 | 不生成投研结论 |
| prospectus quality | 招股书质量和能力判定 | 不替代 parser 基础质量报告 |
| Deal Evidence | 证据规范化、引用、快照 | 不把向量召回变成事实 |
| IC runtime | 基于当前 receipt 生成专业分析 | 不绕过 API 写项目包 |

## 5. 权威目录合同

### 5.1 目录结构

```text
${SIQ_WIKI_ROOT}/deals/{deal_id}/
  manifest.json
  project_meta.json
  data_room/
    raw/
      {document_id}.pdf
    metadata/
      {document_id}.json
  parsed_documents/
    {document_id}/
      current.json
      runs/
        {parse_run_id}/
          archive_manifest.json
          result_manifest.json
          document.md
          content_list.json
          content_list_enhanced.json
          document_full.json
          financial_data.json
          financial_checks.json
          quality_report.json
  sources/
    analysis_sources.json
  evidence/
    evidence_items.ndjson
    evidence_index.json
    evidence_quality_report.json
    evidence_snapshot.json
```

### 5.2 目录约束

1. `{deal_id}`、`{document_id}`、`{parse_run_id}` 必须经过现有或新增 validator。
2. API 不接受调用方提交任何绝对 artifact path。
3. raw PDF 名称使用 `document_id.pdf`，原始文件名只保存在 metadata。
4. parse run 目录写入后不可原地修改；修复或重跑创建新 run。
5. `current.json` 只能在目标 run 完整校验并原子落盘后更新。
6. `archive_manifest.json` 保存每个归档文件的相对路径、大小和 SHA256。
7. 页面截图、临时渲染图和 parser cache 不进入业务归档；它们放在 `var/` 或 parser runtime，可从 raw PDF 重建。
8. `result_manifest.json` 是 parser 输出集合的权威声明；业务层只提升 allowlist 内的 canonical artifacts。

### 5.3 原子提升流程

```text
parser result dir
  -> 校验 task/document/config identity
  -> 读取 result_manifest
  -> 拷贝 allowlist artifacts 到 .staging-{parse_run_id}
  -> 逐文件 hash 校验
  -> 写 archive_manifest.json
  -> fsync + rename 为 runs/{parse_run_id}
  -> 原子更新 current.json
  -> 更新 DealDocument metadata
```

任一步失败时不得更新 `current.json`，旧 run 继续有效。

## 6. 领域合同

### 6.1 DealDocument v2 扩展

保留现有 `siq_deal_document_v1` 读取兼容，新增字段时升级为 `siq_deal_document_v2`：

```json
{
  "schema_version": "siq_deal_document_v2",
  "document_id": "DOC-0123456789ABCDEF",
  "deal_id": "DEAL-EXAMPLE-001",
  "document_type": "prospectus",
  "document_profile": "cn_a_share_prospectus",
  "market": "CN",
  "exchange": "SSE",
  "board": "star",
  "filing_stage": "registration_draft",
  "document_date": "2026-07-01",
  "original_filename": "发行人招股说明书.pdf",
  "storage_path": "data_room/raw/DOC-0123456789ABCDEF.pdf",
  "sha256": "...",
  "size_bytes": 12345678,
  "document_status": "active",
  "parse_status": "queued",
  "analysis_source_status": "pending",
  "current_parse_run_id": "PRUN-...",
  "supersedes_document_id": null,
  "created_by": {"id": 7, "username": "analyst"},
  "created_at": "...",
  "updated_at": "..."
}
```

兼容读取规则：

- v1 `status=uploaded` 映射为 `document_status=active`、`parse_status=not_started`。
- v1 `status=parse_bound` 继续按 `parser_kind=document` 读取。
- 旧字段 `parse_task_id`、`parsed_artifact_path` 保留只读兼容，新的 PDF run 使用 `parse_runs/current_parse_run_id`。

### 6.2 ParseRun 合同

每次解析建立独立合同：

```json
{
  "schema_version": "siq_primary_market_parse_run_v1",
  "parse_run_id": "PRUN-20260713-...",
  "deal_id": "DEAL-EXAMPLE-001",
  "document_id": "DOC-0123456789ABCDEF",
  "parser_kind": "pdf",
  "parser_task_id": "...",
  "market": "CN",
  "document_profile": "cn_a_share_prospectus",
  "raw_sha256": "...",
  "parse_config_hash": "...",
  "parser_version": "...",
  "status": "queued",
  "artifact_root": null,
  "quality_status": "pending",
  "capabilities": {},
  "submitted_by": {"id": 7, "username": "analyst"},
  "created_at": "...",
  "updated_at": "..."
}
```

建议将每个 run 的可变运行状态保存在 document metadata 的 `parse_runs[]` 摘要中，最终不可变详情写入 run 目录的 `archive_manifest.json`。如果数组增长超过实际容量目标，再迁入 PostgreSQL；首期不为此新增独立数据库表。

### 6.3 AnalysisSource 合同

```json
{
  "schema_version": "siq_primary_market_analysis_source_v1",
  "source_id": "PM:DEAL-EXAMPLE-001:DOC-0123456789ABCDEF:PRUN-...",
  "domain": "primary_market",
  "source_type": "primary_market_prospectus",
  "deal_id": "DEAL-EXAMPLE-001",
  "market": "CN",
  "company_id": "PRIMARY:DEAL-EXAMPLE-001",
  "filing_id": "PROSPECTUS:DOC-0123456789ABCDEF",
  "document_id": "DOC-0123456789ABCDEF",
  "parse_run_id": "PRUN-...",
  "artifact_manifest_path": "parsed_documents/.../archive_manifest.json",
  "status": "ready_with_restrictions",
  "capabilities": {
    "text_evidence": "ready",
    "source_page_trace": "ready",
    "financial_facts": "blocked",
    "semantic_index": "pending"
  },
  "activated_by": null,
  "activated_at": null
}
```

状态枚举：

```text
pending
ready
ready_with_restrictions
review_required
blocked
disabled
superseded
```

### 6.4 ResearchIdentity

一级市场招股书传入 IC runtime 时必须带：

```json
{
  "domain": "primary_market",
  "market": "CN",
  "company_id": "PRIMARY:DEAL-EXAMPLE-001",
  "filing_id": "PROSPECTUS:DOC-0123456789ABCDEF",
  "parse_run_id": "PRUN-..."
}
```

约束：

- `domain=primary_market` 时不得进入二级市场 `companies` Wiki 或 legacy A 股 PostgreSQL fallback。
- Agent 输出中的 Evidence 必须匹配同一 `deal_id/document_id/parse_run_id`。
- 如果一个回答同时使用多个一级市场材料，answer audit 保存 `source_ids[]` 和统一 `evidence_snapshot_hash`。

### 6.5 UserArtifact 关联

提交 parser 后继续写现有 `UserArtifact(artifact_type=parse)`，用于 parser task 访问控制。同时建议增加：

```text
artifact_type = primary_market_material
artifact_key = {document_id}
global_artifact_id = {deal_id}:{document_id}
path = /primary-market/materials?dealId=...&documentId=...
source = prospectus_upload
```

Deal ACL 是业务访问权威；`UserArtifact` 只承担用户工作区关联和 parser task ownership，不替代 Deal ACL。

## 7. 状态机

### 7.1 文档状态

```text
active -> superseded
       -> deleted   # 仅未被引用且符合删除条件
```

### 7.2 解析状态

```text
not_started -> submitting -> queued -> parsing -> archiving -> succeeded
                    |          |          |            |
                    +----------+----------+-------------> failed
                               +------------------------> cancelled
                               +------------------------> interrupted
```

`submitting` 失败后保留 raw PDF，并允许重新提交。进程恢复后无法确认的旧 `submitting/parsing/archiving` 必须进入 reconciliation，不能永久悬挂。

### 7.3 分析源状态

```text
pending -> ready
        -> ready_with_restrictions
        -> review_required -> ready
                           -> blocked
        -> blocked

ready / ready_with_restrictions -> disabled / superseded
```

### 7.4 索引状态

```text
not_requested -> queued -> indexing -> indexed
                              |          |
                              +----------> failed
```

Document、parse、source、indexing 状态不得复用一个字符串字段，否则前端和恢复逻辑无法判断失败发生在哪一层。

## 8. 上传与解析详细流程

### 8.1 请求进入

1. 校验 `report.create`。
2. 调用现有 `require_deal_access(deal_id, "write", current_user)`。
3. 校验单文件、扩展名、MIME、`%PDF` 文件头和非空内容。
4. 统一执行 Nginx、API、upload proxy、parser 四层大小限制。
5. 将上传流写入受控临时文件，同时计算 SHA256。
6. 在 Deal 内按 SHA256 检查完全重复材料。

### 8.2 原件归档

1. 生成 `document_id`。
2. 原子写入 `data_room/raw/{document_id}.pdf`。
3. 写入 `data_room/metadata/{document_id}.json`，初始 `parse_status=submitting`。
4. 写 `deal_prospectus_uploaded` audit event。

原件归档成功后即视为用户上传成功。后续 parser 提交失败不得回滚原件。

### 8.3 Parser 提交

从 `workspace.authenticated_pdf_upload()` 中抽取通用 application service：

```python
submit_pdf_parse(
    *,
    upload,
    market="CN",
    document_profile="cn_a_share_prospectus",
    owner,
    quota_context,
    source_context,
) -> PdfParseSubmissionResult
```

`source_context` 至少包含：

```json
{
  "domain": "primary_market",
  "deal_id": "...",
  "document_id": "...",
  "source_type": "primary_market_prospectus"
}
```

路由层和材料服务都调用该 service，不互相调用 HTTP endpoint。service 保持现有：

- `SpooledTemporaryFile` 和分块 hash。
- 单文件/批次限制。
- explicit `httpx.Timeout`。
- parser owner/tenant/market headers。
- 配额预占、使用记录和失败释放。
- parser duplicate response 处理。
- `UserArtifact(parse)` 记录。

### 8.4 提交结果

成功后：

1. 生成 `parse_run_id`。
2. 保存 `parser_task_id`、config hash、raw hash、parser kind/version。
3. 更新 `parse_status=queued|parsing`。
4. 写 `deal_prospectus_parse_submitted` audit event。
5. 返回 HTTP 202。

失败后：

1. 更新 `parse_status=failed`。
2. 保存机器可读 `failure_code` 和适合用户展示的 `failure_message`。
3. 释放未消费配额。
4. 写 `deal_prospectus_parse_submit_failed`。
5. 保留 raw PDF，允许 reparse。

## 9. 任务同步、恢复与产物归档

### 9.1 Reconciler

新增轻量 reconciliation service，触发方式：

- 用户读取材料详情或 parse status 时执行单任务同步。
- API 启动后扫描有限数量的非终态 primary market parse runs。
- 可选后台周期任务按配置扫描，默认不开高频轮询。

不得要求用户保持页面打开才能完成归档。

### 9.2 状态映射

Parser 状态统一映射到业务状态：

| Parser 状态 | ParseRun 状态 |
| --- | --- |
| uploaded/queued/pending | queued |
| submitting/submitted/processing | parsing |
| completed/succeeded | archiving，然后 succeeded |
| failed/error | failed |
| cancelled | cancelled |
| parser task missing after recovery | interrupted |

未知状态不能当作成功。

### 9.3 归档 allowlist

首期核心 allowlist：

```text
result_manifest.json
document.md
content_list.json
content_list_enhanced.json
document_full.json
financial_data.json
financial_checks.json
quality_report.json
table_index.json
```

实际文件名以 parser result manifest 合同为准。缺少非关键可选文件只产生 warning；缺少 manifest 或 canonical Markdown 阻断归档成功。

### 9.4 幂等性

归档函数必须满足：

- 同一 `parse_run_id` 重复执行不产生不同结果。
- 已存在且 manifest/hash 一致时返回 existing success。
- 已存在但 hash 不一致时 fail closed，并写冲突审计，不覆盖。
- `current.json` 只在成功提升后更新。
- 多 worker 同步同一 run 时使用现有文件锁/lease 或 PostgreSQL durable lease，最终只有一个 owner 提升产物。

## 10. A 股招股书 profile

### 10.1 Profile 标识

```text
market = CN
document_type = prospectus
document_profile = cn_a_share_prospectus
```

建议 parser submit config 将 `document_profile` 纳入 `parse_config_hash`，避免相同 PDF 按年报和招股书 profile 解析时错误复用。

### 10.2 招股书业务元数据

用户可填、系统可回填：

- 发行人名称。
- 交易所：SSE、SZSE、BSE。
- 板块：main、star、chinext、beijing。
- 文件阶段：application draft、meeting draft、registration draft、updated、final。
- 文件日期。
- 来源说明。
- 是否替代某个旧 document。

除 Deal 和 PDF 外，其余字段首期可选，不能因为用户不知道申报阶段而阻止上传。

### 10.3 章节覆盖

招股书适配器需要识别以下语义章节，不能只依赖固定中文序号：

1. 发行概况和重大事项提示。
2. 风险因素。
3. 发行人基本情况、历史沿革和股权结构。
4. 业务与技术。
5. 行业、竞争格局和市场地位。
6. 公司治理、独立性、同业竞争和关联交易。
7. 财务会计信息与管理层分析。
8. 募集资金运用。
9. 投资者保护、重要合同、诉讼和其他重大事项。

首期章节识别仅用于 coverage 和检索标签，不要求一次性抽取所有专题结构化表。

### 10.4 质量能力判定

建议按能力而不是单一 overall status 判断：

| 能力 | Ready 条件 | Block 条件 |
| --- | --- | --- |
| `text_evidence` | canonical Markdown 非空、主体文本覆盖满足最低要求 | Markdown 缺失、几乎为空或明显解析失败 |
| `source_page_trace` | page/block/bbox 可定位比例达标 | 无法回到原 PDF 页面 |
| `financial_facts` | 财务数据有期间/币种/单位且 checks 无阻断项 | 关键财务勾稽失败、期间错配或身份不明 |
| `semantic_index` | Evidence 已生成并完成 Milvus ingest | 未构建或索引失败 |

源状态规则：

- 文本和页码能力均 ready，财务能力 ready：`ready`。
- 文本可用但财务能力 blocked：`ready_with_restrictions`。
- 只有 warning 且需要人工判断：`review_required`。
- canonical Markdown 或身份/manifest 校验失败：`blocked`。

`ready_with_restrictions` 可以服务战略、行业、法务文本分析，但财务智能体不得把其中的结构化数字作为已验证事实。

## 11. Evidence 与分析源接入

### 11.1 Parser artifact provider

当前 `deal_evidence.py` 固定读取 `DOCUMENT_PARSER_RESULTS_ROOT`。改造为最小 provider 分支：

```text
parser_kind=document
  -> 兼容读取 DOCUMENT_PARSER_RESULTS_ROOT/{task_id}/document.md

parser_kind=pdf
  -> 读取 Deal parsed_documents/{document_id}/runs/{parse_run_id}
  -> 优先 content_list_enhanced/document_full
  -> Markdown 作为文本 fallback
```

不需要引入通用插件框架；一个稳定的 provider protocol 和两个明确实现即可。

### 11.2 Evidence Item 扩展

PDF 招股书 Evidence 至少保存：

```json
{
  "source_id": "PM:...",
  "source_type": "primary_market_prospectus",
  "deal_id": "...",
  "document_id": "...",
  "parse_run_id": "...",
  "evidence_id": "EVID-...",
  "dimension": "business|finance|legal|risk|sector|strategy",
  "quote": "...",
  "page": 123,
  "block_id": "...",
  "bbox": [0, 0, 0, 0],
  "locator": "prospectus.pdf:p123:block-...",
  "artifact_path": "parsed_documents/...",
  "source_sha256": "..."
}
```

### 11.3 Evidence 快照

每次 active source set 或 Evidence 内容变化时生成：

```text
evidence_snapshot_hash = SHA256(
  sorted(active source_id + archive_manifest_hash)
  + evidence_index_hash
  + evidence_contract_version
)
```

写入 `evidence/evidence_snapshot.json`：

```json
{
  "schema_version": "siq_deal_evidence_snapshot_v1",
  "deal_id": "...",
  "snapshot_hash": "...",
  "active_sources": [],
  "evidence_index_sha256": "...",
  "created_at": "..."
}
```

### 11.4 下游失效规则

- startup receipt 必须保存 `evidence_snapshot_hash`。
- R1/R2/R3/R4 正式产物必须保存使用的 snapshot hash。
- 当前 snapshot 变化后，历史产物不删除，但 readiness 标记为 `stale`。
- 未人工确认的 workflow 阶段应阻断继续推进，要求重新检索或显式接受旧快照。
- 已人工确认的 R4 保留历史有效性，但项目进入 `decision_review_required`，不能静默覆盖原决策。

## 12. IC 智能体使用规则

招股书属于 Deal 共享事实源，所有 IC profile 可检索，但按职责路由查询：

| Profile | 优先内容 |
| --- | --- |
| `siq_ic_strategist` | 募投方向、战略定位、政策风险、资本路径、退出窗口 |
| `siq_ic_sector_expert` | 业务与技术、市场规模、竞争格局、产业链、客户和供应商 |
| `siq_ic_finance_auditor` | 报告期财务数据、收入质量、三表勾稽、现金流、估值基础 |
| `siq_ic_legal_scanner` | 历史沿革、股权、关联交易、资质、诉讼、知识产权和合规 |
| `siq_ic_risk_controller` | 风险因素、集中度、供应链、ESG、舆情和压力情景 |
| `siq_ic_chairman` | 专家结论、Evidence、分歧、条件与评分，不直接替代专家重做全文分析 |

约束：

- startup retrieval 只召回 active source。
- receipt 必须包含 `source_ids`、`evidence_snapshot_hash` 和 capability restrictions。
- 财务 profile 在 `financial_facts != ready` 时只能输出 `assumed/contested/insufficient_evidence`。
- Agent 引用必须回到 Deal-scoped artifact/source endpoint，不能展示宿主机路径。
- 向量检索命中只决定候选，最终引用仍使用归档 Evidence 坐标。

## 13. API 设计

### 13.1 产品 façade

```http
POST /api/primary-market/projects/{deal_id}/materials/prospectuses
GET  /api/primary-market/projects/{deal_id}/materials
GET  /api/primary-market/projects/{deal_id}/materials/{document_id}
GET  /api/primary-market/projects/{deal_id}/materials/{document_id}/parse-status
POST /api/primary-market/projects/{deal_id}/materials/{document_id}/reparse
POST /api/primary-market/projects/{deal_id}/materials/{document_id}/analysis-source/review
POST /api/primary-market/projects/{deal_id}/materials/{document_id}/analysis-source/disable
POST /api/primary-market/projects/{deal_id}/materials/{document_id}/supersede
GET  /api/primary-market/projects/{deal_id}/materials/{document_id}/artifacts/{artifact_name}
GET  /api/primary-market/projects/{deal_id}/materials/{document_id}/source/page/{page_number}
```

这些 endpoint 调用同一 application service，不复制 `/api/deals` 路由逻辑。

### 13.2 上传请求

```http
POST /api/primary-market/projects/DEAL-001/materials/prospectuses
Content-Type: multipart/form-data

file=<pdf>
exchange=SSE
board=star
filing_stage=registration_draft
document_date=2026-07-01
source_note=用户上传注册稿
supersedes_document_id=
```

成功响应：

```json
{
  "schema_version": "siq_primary_market_prospectus_upload_v1",
  "document": {
    "document_id": "DOC-...",
    "document_type": "prospectus",
    "document_status": "active",
    "parse_status": "queued",
    "analysis_source_status": "pending"
  },
  "parse_run": {
    "parse_run_id": "PRUN-...",
    "parser_task_id": "...",
    "status": "queued"
  },
  "status_url": "/api/primary-market/projects/DEAL-001/materials/DOC-.../parse-status"
}
```

新建任务返回 `202`。完全重复材料返回 `200` 并带 `reused=true`，不得通过 `409` 迫使普通用户理解 parser 去重语义。

### 13.3 Reparse

```json
{
  "reason": "parser_upgrade|quality_retry|manual",
  "parse_method": "auto",
  "formula_enable": true,
  "table_enable": true
}
```

Reparse 总是创建新 `parse_run_id`。默认不立即切换 current；只有新 run 完成归档和质量判定后才允许自动或人工切换。

### 13.4 人工质量复核

```json
{
  "decision": "activate|block",
  "capability_overrides": {
    "text_evidence": "ready",
    "financial_facts": "blocked"
  },
  "note": "文本可用，财务表仍需人工复核"
}
```

必须保存 reviewer、时间、原质量结果和 override 原因。不得允许把缺失 canonical Markdown 的源强制激活。

### 13.5 错误合同

| HTTP | code | 场景 |
| --- | --- | --- |
| 400 | `invalid_prospectus_metadata` | 枚举、日期或 supersedes 参数错误 |
| 400 | `invalid_pdf` | 扩展名/MIME/文件头不匹配 |
| 404 | `deal_or_material_not_found` | Deal ACL deny 也统一返回 404 |
| 409 | `material_state_conflict` | 在不可重试状态执行 reparse/supersede |
| 413 | `prospectus_too_large` | 超过统一上传上限 |
| 422 | `quality_review_invalid` | 非法质量 override |
| 429 | `parse_quota_exceeded` | 用户解析配额不足 |
| 502 | `pdf_parser_unavailable` | parser 提交或状态服务不可用 |
| 503 | `artifact_promotion_unavailable` | 共享存储或归档服务暂不可用 |

响应不得泄露 parser host、绝对路径或其他用户 task ID。

## 14. 后端模块改造

### 14.1 新增文件

建议最小新增：

```text
apps/api/services/pdf_parse_submission.py
apps/api/services/primary_market_materials.py
apps/api/services/primary_market_prospectus_quality.py
apps/api/tests/test_primary_market_materials.py
apps/api/tests/test_primary_market_prospectus_routes.py
```

职责：

- `pdf_parse_submission.py`：从 workspace router 抽取 parser submit、quota、dedupe、UserArtifact 逻辑。
- `primary_market_materials.py`：路径、合同、上传编排、parse run、reconcile、promotion、source activation、supersede。
- `primary_market_prospectus_quality.py`：组合 parser quality、artifact coverage 和招股书 profile rules。

如果 `primary_market_materials.py` 超过稳定职责边界，再按实际复杂度拆分 `primary_market_material_reconciler.py`；不要预先创建只有一层转发的 facade。

### 14.2 修改文件

```text
apps/api/routers/primary_market_meeting.py
apps/api/routers/workspace.py
apps/api/services/deal_documents.py
apps/api/services/deal_evidence.py
apps/api/services/deal_contracts.py
apps/api/services/ic_startup_retrieval.py
apps/api/services/path_config.py
apps/api/services/usage_service.py
apps/api/tests/test_deals_router.py
apps/api/tests/test_workspace_sync.py
apps/api/tests/test_primary_market_meeting_router.py
```

说明：

- 如果材料 façade 已从 `primary_market_meeting.py` 明显超出会议职责，应新建 `routers/primary_market_materials.py`，并在主 app 注册；这是推荐选择。
- `workspace.py` 只负责改为调用共享 submission service，不改变现有 `/api/pdf/upload` 合同。
- `deal_documents.py` 保持通用文档能力，增加 v2 metadata 和 parser kind 兼容。
- `deal_evidence.py` 增加 PDF artifact provider 和 snapshot。
- `deal_contracts.py` 增加 active source、snapshot 和 capability preflight。

### 14.3 PDF parser 修改

首期 parser 核心算法不重写，只增加契约透传：

```text
apps/pdf-parser/pdf_parser_request_utils.py
apps/pdf-parser/pdf_parser_task_lifecycle_service.py
apps/pdf-parser/pdf_parser_result_manifest_service.py
apps/pdf-parser/tests/test_pdf_parser_mineru_lifecycle.py
apps/pdf-parser/tests/test_pdf_parser_result_manifest_service.py
```

要求：

- 接收并持久化 `document_profile` 和受控 `source_context`。
- `document_profile` 进入 parse config hash。
- task/result manifest 返回 parser version、market、document profile、raw hash 和 config hash。
- parser 不写 Deal package，也不接受任意 Deal artifact path。

## 15. 前端改造

### 15.1 页面结构

在现有材料中心保留项目选择和材料列表，增加：

```text
材料类型
  - 招股书
  - Teaser
  - BP
  - 财务模型
  - 其他现有类型
```

选择“招股书”后：

- 只接受 PDF。
- `market` 固定为 CN，不向普通用户展示。
- 展示交易所、板块、文件阶段、文件日期和来源说明。
- 自动解析，不提供“是否解析”开关。
- 隐藏手工 task ID 绑定入口。

### 15.2 状态展示

每条招股书显示：

- 文件名、版本、交易所/板块、上传人、时间和大小。
- 当前 parse run 和 parser 状态。
- 文本、页码、财务、索引四项 capability。
- 是否为当前 active source。
- 是否使已有 receipt/report 变为 stale。

状态文案：

```text
已上传
提交解析中
排队中
解析中
归档中
质量待确认
可用于分析
可用于文本分析，财务受限
解析失败
已被新版替代
```

### 15.3 用户动作

```text
查看原件
查看解析结果
查看质量报告
查看原文页
重新解析
审核并启用
停用分析源
设为当前版本
标记由新版替代
```

被正式 Evidence 或报告引用后不展示硬删除；只提供停用或 supersede。

### 15.4 前端文件

```text
apps/web/src/pages/PrimaryMarketMaterials.tsx
apps/web/src/features/primary-market/primaryMarketApi.ts
apps/web/src/features/primary-market/primaryMarketApi.test.ts
apps/web/src/features/primary-market/primaryMarketViewModel.ts
apps/web/src/features/primary-market/primaryMarketViewModel.test.ts
apps/web/src/lib/dealTypes.ts
```

页面应使用 request generation/AbortController，防止切换 Deal 后旧 parse status 写入当前项目。

## 16. 权限与安全

### 16.1 权限矩阵

| 操作 | 权限 | 对象级检查 |
| --- | --- | --- |
| 查看材料/状态/产物 | `report.view` | Deal access view |
| 上传、reparse、质量确认、停用 | `report.create` | Deal access write |
| 删除未引用材料 | `report.create`，后续可细分 | owner/admin + Deal write |
| 强制 capability override | 建议 `report.edit` 或更细权限 | Deal write + audit |
| 运维重放 reconciler | `system.config` | task/document scope |

### 16.2 文件安全

- 文件名只用于展示，存储名使用 document ID。
- 校验扩展名、Content-Type、PDF magic、空文件和上限。
- parser 运行环境继续保持隔离和资源限制。
- 拒绝加密且无法解析的 PDF，并给出稳定错误码。
- 对超大页数、异常压缩率、嵌套对象和解析超时设置保护。
- artifact download 只能访问 allowlist 和 manifest 内文件。
- `Path.resolve()` 后必须 `is_relative_to()` 对应 Deal/run 根目录。

### 16.3 跨用户与去重

- 不能通过重复文件响应泄露其他 Deal 或其他用户是否上传过同一招股书。
- parser 可以在同 owner/config 范围内复用 task，但每个 Deal 必须建立独立 source/archive identity。
- 绑定已有 parser task 时同时校验 `UserArtifact(parse)` 和 Deal write 权限。
- 管理员也必须写 audit，不得静默跨项目关联。

## 17. 审计与可观察性

### 17.1 Audit events

新增稳定事件：

```text
deal_prospectus_uploaded
deal_prospectus_duplicate_reused
deal_prospectus_parse_submitted
deal_prospectus_parse_submit_failed
deal_prospectus_parse_status_changed
deal_prospectus_artifacts_promoted
deal_prospectus_artifact_conflict
deal_prospectus_quality_evaluated
deal_prospectus_source_activated
deal_prospectus_source_disabled
deal_prospectus_superseded
deal_evidence_snapshot_changed
deal_workflow_marked_stale
```

事件保存 ID、状态、hash 和相对路径，不保存绝对路径、token 或完整 parser 异常堆栈。

### 17.2 Metrics

建议指标：

```text
primary_market_prospectus_upload_total{result}
primary_market_prospectus_parse_total{result}
primary_market_prospectus_parse_duration_seconds
primary_market_prospectus_archive_duration_seconds
primary_market_prospectus_quality_total{status}
primary_market_prospectus_reconcile_total{result}
primary_market_analysis_source_total{status}
```

标签只能使用稳定枚举，不使用 deal ID、document ID 或 task ID，避免高基数。

### 17.3 日志关联

结构化日志允许包含：

```text
request_id
deal_id_hash
document_id
parse_run_id
parser_task_id_hash
stage
result
duration_ms
```

不得记录文件正文、用户 token 或原始绝对路径。

## 18. 配置

首期建议配置：

```text
SIQ_PRIMARY_MARKET_PROSPECTUS_MAX_FILE_BYTES
  默认继承 SIQ_PDF_UPLOAD_MAX_FILE_BYTES

SIQ_PRIMARY_MARKET_PROSPECTUS_MAX_BATCH_BYTES
  首期等于单文件上限，因为上传接口只接受一个招股书

SIQ_PRIMARY_MARKET_AUTO_ACTIVATE_QUALITY_PASS
  默认 1

SIQ_PRIMARY_MARKET_RECONCILE_ON_STARTUP
  默认 1

SIQ_PRIMARY_MARKET_RECONCILE_STARTUP_LIMIT
  默认使用保守有限值

SIQ_PRIMARY_MARKET_RECONCILE_INTERVAL_SECONDS
  默认 0，表示不开周期扫描；依赖按需同步和启动恢复
```

不新增 `SIQ_PRIMARY_MARKET_ROOT`。业务根继续由 `SIQ_WIKI_ROOT` 决定，防止同一 Deal 出现两套根目录。

## 19. 兼容与迁移

### 19.1 旧材料

- 旧 `DealDocument v1` 继续可读、可列出和可删除。
- `parser_kind` 缺失且存在 document-parser URL 时视为 `document`。
- 旧手工绑定入口保留，但在材料中心默认折叠到“兼容工具”。
- 不自动把旧 document-parser 任务转成 PDF parse run。

### 19.2 Manifest

Deal `manifest.json.documents[]` 增加新字段，但旧消费者必须容忍缺失：

```text
document_profile
document_status
parse_status
analysis_source_status
current_parse_run_id
supersedes_document_id
```

### 19.3 删除语义

当前删除会物理删除 raw 和 metadata。新规则：

- 未解析、未激活、未被 Evidence/报告引用的材料可物理删除。
- 已存在 parse run 但未被引用的材料默认软删除，后台按 retention 清理。
- 已被 Evidence、receipt、R1-R4 或 decision 引用的材料只能 disable/supersede。
- 删除 parser task 必须确认没有其他 UserArtifact/Deal source 引用。

## 20. 可执行任务分解

### PMM-00：建立合同和失败测试

范围：

- 新增文档/parse run/source/snapshot schema 常量和 validator。
- 为非法枚举、非法 ID、路径穿越、状态冲突和 v1 兼容建立单测。
- 固定目录和响应合同，不发起真实 parser 调用。

建议文件：

```text
apps/api/services/primary_market_materials.py
apps/api/tests/test_primary_market_materials.py
apps/web/src/lib/dealTypes.ts
```

验收：

- v1 文档兼容读取通过。
- 所有路径只能落在目标 Deal 目录。
- document/parse/source 三类状态不能非法跳转。

### PMM-01：抽取 PDF submission service

范围：

- 从 `workspace.py` 抽取 parser submission、dedupe、quota 和 UserArtifact 逻辑。
- `/api/pdf/upload` 改为调用新 service，HTTP 合同保持不变。
- 支持 `document_profile/source_context` 受控透传。

建议文件：

```text
apps/api/services/pdf_parse_submission.py
apps/api/routers/workspace.py
apps/api/tests/test_workspace_sync.py
```

验收：

- 现有 PDF upload 测试不回退。
- 超限不会调用 parser。
- parser 失败会释放 quota。
- duplicate、新任务和 reused task 的 UserArtifact 行为不变。

### PMM-02：实现招股书上传与自动提交

范围：

- 新增 primary market materials router。
- 保存 raw、metadata，自动提交 parser。
- 返回 202 和 status URL。
- 增加 Deal ACL 和双用户负向测试。

建议文件：

```text
apps/api/routers/primary_market_materials.py
apps/api/services/primary_market_materials.py
apps/api/tests/test_primary_market_prospectus_routes.py
apps/api/main.py
```

验收：

- owner 可上传，其他 analyst 对 private Deal 得到 404。
- 非 PDF、空文件和超限文件稳定失败。
- parser 不可用时 raw/metadata 保留且可重试。
- 用户无需手工 task ID。

### PMM-03：实现 reconcile 和不可变归档

范围：

- parser 状态映射。
- 非终态任务恢复。
- result manifest 校验、allowlist copy、hash、atomic rename 和 current pointer。
- 并发 reconcile 幂等。

验收：

- succeeded task 只生成一个完整 run。
- archive 中途失败不更新 current。
- hash 冲突不覆盖旧产物。
- API 重启后 queued/processing/archiving 能恢复或明确 interrupted。

### PMM-04：接入质量能力与分析源

范围：

- 组合 parser quality、financial checks 和基础 prospectus coverage。
- 写 analysis source registry。
- pass 自动启用、warning 人工复核、fail 阻断。
- 增加 source disable/supersede。

验收：

- financial fail 不会被财务智能体当作 verified facts。
- 文本可用但财务受限时返回 `ready_with_restrictions`。
- 所有人工 override 有 reviewer、note 和 audit。

### PMM-05：改造 Deal Evidence 和 snapshot

范围：

- 增加 PDF artifact provider。
- 从 content list/document full 生成带页码、block、bbox 的 Evidence。
- 写 active source set 和 evidence snapshot。
- snapshot 变化时标记 receipt/report readiness stale。

验收：

- Evidence 能从 PDF Deal archive 构建，不依赖 parser runtime 目录长期存在。
- citation 可回到原始 PDF 页。
- 未知/cross-run Evidence ID 被拒绝。
- 新版 source 启用后旧 receipt 不再满足正式 Agent gate。

### PMM-06：扩展 parser 招股书 profile

范围：

- 持久化 `document_profile/source_context`。
- 将 profile 纳入 config hash 和 result manifest。
- 增加章节 coverage 和报告期检查。
- 建立小型 A 股招股书回归样本集。

验收：

- 同一 PDF 使用不同 profile 不错误去重。
- 能识别核心章节并输出机器可读 coverage。
- 三年或三年一期财务期间缺失产生稳定 warning/fail。

### PMM-07：前端材料中心

范围：

- 增加招股书类型和专用表单。
- 接入上传、status、quality、reparse、activate、disable 和 supersede。
- 展示 capability 和版本链。
- 隐藏普通用户手工 task binding。

验收：

- 切换 Deal 时旧请求不会污染当前状态。
- 桌面和移动端文件名、状态和操作不重叠。
- 上传后无需离开页面即可看到状态推进。
- warning/fail 有明确可执行动作。

### PMM-08：IC Agent 与 workflow 联动

范围：

- startup receipt 加 `source_ids/evidence_snapshot_hash/capability_restrictions`。
- IC task payload 带 primary market ResearchIdentity。
- preflight 检查 receipt snapshot 是否当前。
- 已确认 R4 遇到新 source 时进入 review required，不自动覆盖。

验收：

- 六个专业 profile 能按角色召回同一招股书的不同维度。
- 财务受限源不会生成 verified 数字 claim。
- 报告 audit 保存使用的 source/snapshot identity。

### PMM-09：端到端与发布门禁

范围：

- API + fake parser 集成测试。
- 真实 parser 小样本 smoke。
- Playwright 上传到 Evidence/Agent readiness 的 E2E。
- 双用户 BOLA、超限、重启恢复、重复上传和 symlink escape 测试。

验收：

```text
上传 A 股招股书 PDF
  -> parse succeeded
  -> Deal archive 完整
  -> quality/capabilities 可见
  -> source active
  -> Evidence 有页码引用
  -> startup receipt 使用最新 snapshot
  -> Agent 输出可追溯到该招股书
```

## 21. 任务依赖与建议提交顺序

```text
PMM-00
  -> PMM-01
  -> PMM-02
  -> PMM-03
  -> PMM-04
  -> PMM-05
  -> PMM-08

PMM-01 -> PMM-06
PMM-02 + PMM-03 + PMM-04 -> PMM-07
PMM-05 + PMM-06 + PMM-07 + PMM-08 -> PMM-09
```

建议 PR/提交边界：

1. `api: add primary market material contracts`
2. `api: extract authenticated pdf submission service`
3. `api: add prospectus upload and parse orchestration`
4. `api: archive primary market pdf parse runs`
5. `api: register prospectus analysis sources and evidence snapshots`
6. `pdf-parser: add cn prospectus profile metadata and coverage`
7. `web: add prospectus workflow to primary market materials`
8. `agents: bind primary market receipts to evidence snapshots`
9. `tests: add prospectus materials end-to-end gates`

不要把 parser 算法、前端页面、Agent workflow 和全仓格式化放进同一个 PR。

## 22. 测试矩阵

### 22.1 API 单元测试

| 类别 | 用例 |
| --- | --- |
| 上传 | 正常 PDF、空文件、伪 PDF、超限、异常文件名 |
| 权限 | owner、其他 analyst、viewer、admin、private Deal 404 |
| 去重 | 同 Deal 同 hash、同 hash 不同 profile、同用户不同 Deal |
| parser | 新任务、duplicate、timeout、502、非法响应、quota release |
| 状态 | 合法迁移、非法迁移、cancel、retry、interrupted recovery |
| 归档 | manifest 缺失、hash mismatch、partial copy、重复 reconcile |
| 路径 | `..`、绝对路径、symlink escape、manifest 外 artifact |
| source | pass、warning、financial block、人工 override、disable |
| 版本 | reparse、新文件 supersede、current pointer 原子切换 |
| snapshot | source 激活、停用、替换、Evidence hash 变化、receipt stale |

### 22.2 Parser 测试

- `document_profile` 持久化和响应。
- profile 进入 config hash。
- result manifest identity 一致。
- 章节 coverage 对不同标题写法稳定。
- scanned/empty/encrypted PDF 的错误合同。
- 财务期间和表格质量输出。

### 22.3 Web 单元测试

- 招股书表单 payload。
- 202 response 和状态轮询。
- request abort/generation。
- capability 文案和操作启用条件。
- supersede/version chain。
- warning 人工确认。
- 手工 task binding 只在兼容模式出现。

### 22.4 E2E

1. 创建或加载 private Deal。
2. 上传固定小型 A 股招股书 fixture。
3. 看到 queued/processing/succeeded 状态。
4. 查看质量和原文页。
5. 激活 source。
6. 构建 Evidence。
7. 验证 IC readiness 和最新 snapshot。
8. 第二用户不能读取材料、状态、原文和 artifact。

## 23. 验证命令

实施时按变更范围执行：

```bash
cd apps/api
uv run python -m pytest \
  tests/test_primary_market_materials.py \
  tests/test_primary_market_prospectus_routes.py \
  tests/test_deals_router.py \
  tests/test_workspace_sync.py \
  tests/test_primary_market_meeting_router.py

cd apps/pdf-parser
python -m pytest \
  tests/test_pdf_parser_mineru_lifecycle.py \
  tests/test_pdf_parser_result_manifest_service.py \
  tests/test_pdf_parser_quality_service.py

cd apps/web
npm run test:unit
npm run check:frontend
```

跨服务合同完成后运行：

```bash
scripts/check_all.sh
```

真实 parser smoke 必须使用版本化小样本，不依赖实时外部下载。

## 24. 发布验收

### 24.1 功能验收

- 材料中心存在明确的“招股书”入口。
- 用户上传一次即可自动创建 parser task。
- 原件和解析产物位于稳定 Deal 路径。
- 用户能查看状态、质量、版本和引用页。
- 可用产物能成为 Deal 分析源和 IC 共享证据。

### 24.2 数据验收

- raw SHA256、parser task、parse run、archive manifest 和 source identity 可串联。
- parser runtime 目录被移除后，Deal archived artifacts 仍可构建 Evidence。
- 新 source 激活会刷新 snapshot，并使旧 receipt 正确 stale。
- Evidence/报告中的 `deal_id/document_id/parse_run_id` 一致。

### 24.3 安全验收

- 浏览器不能直连 parser。
- 其他用户无法通过 document/task/artifact/page ID 访问 private Deal 材料。
- 任意路径、symlink 和 manifest 外文件读取被拒绝。
- 超限和恶意 PDF 不进入 parser。
- 日志和响应无绝对路径、token、完整 parser 内部异常。

### 24.4 恢复验收

- parser/API 在 queued、processing、archiving 任一阶段重启后，任务最终进入可信终态。
- 归档失败不破坏旧 current run。
- reparse 不覆盖历史 run。
- duplicate/reconcile 重试不重复计费或重复写 Evidence。

## 25. 回滚策略

| 变更 | 回滚方式 |
| --- | --- |
| 新 façade API | 关闭前端入口，保留已上传 raw/metadata |
| submission service 抽取 | `/api/pdf/upload` 保持合同测试，必要时回退调用适配层 |
| 自动归档 | 停止 reconciler，不删除 parser task 和 Deal raw |
| source 激活 | disable 新 source，恢复前一 evidence snapshot |
| Evidence provider | 保留 document-parser provider，按 `parser_kind` 回退 |
| prospectus quality | 将自动激活关闭，统一进入人工 review |
| 前端 | 隐藏招股书专用 UI，不影响旧材料列表 |

回滚不得删除已归档 parse runs、审计事件或历史 Evidence snapshot。

## 26. 风险与控制

| 风险 | 控制 |
| --- | --- |
| 大型招股书导致内存/连接耗尽 | 复用 spool、分块 hash、明确 timeout、统一上传上限和 parser admission |
| parser task 成功但归档失败 | 独立 archiving 状态、幂等 reconcile、原子 rename |
| 多版招股书混用 | document version chain、current pointer、source set 和 snapshot hash |
| 财务解析错误进入投决 | capability gate、financial checks、Agent receipt restrictions |
| 跨 Deal 去重泄露 | Deal 业务去重与 parser owner 去重分层，响应不暴露其他对象 |
| 新证据未进入旧报告 | snapshot stale gate 和 decision review required |
| 目录增长过快 | 只归档 canonical artifacts，图片按需缓存，后续 retention/对象存储 |
| 章节标题差异导致误判 | 语义 alias、warning 优先、回归集校准，不在首版过度 fail closed |
| 现有材料流程回归 | parser kind 分支、旧 v1 兼容和定向回归 |

## 27. Codex 实施规则

后续 Codex 执行本任务书时必须遵守：

1. 每次只实施一个 PMM 任务或一个清晰子任务。
2. 修改前先读取当前工作树和相邻测试，不能覆盖用户已有改动。
3. 先补失败测试，再修改行为；纯 schema/文档步骤除外。
4. 优先复用 `deal_store` 路径锁、上传代理限制、Deal ACL、usage service 和 parser manifest。
5. 不通过调用 FastAPI route function 复用业务逻辑，必须抽 application service。
6. 不让 parser 接受任意 Deal 路径，也不让 parser 直接写 Deal package。
7. 不把绝对宿主机路径写入公开 metadata、API 或 Agent prompt。
8. 不把 `parse_status=succeeded` 等同于 `financial_facts=ready`。
9. 不用向量命中代替 Evidence 和源页坐标。
10. 每个任务完成后更新本文任务状态或对应实施记录，并记录验证命令和结果。

## 28. Definition of Done

本方案完成的标准不是“页面能上传 PDF”，而是以下闭环同时成立：

```text
用户上传招股书
  -> 原件归档可信
  -> parser 任务归属可信
  -> 任务状态可恢复
  -> 解析产物在 Deal 内不可变归档
  -> 质量能力可解释
  -> 分析源可启用、停用、替换
  -> Evidence 可追溯到 PDF 页
  -> receipt 绑定当前 evidence snapshot
  -> IC Agent 输出绑定同一 source identity
  -> 新版招股书不会与旧报告静默混用
  -> 全过程具备权限、审计和自动化回归
```

达到以上条件后，用户上传的 A 股招股书解析产物才真正成为一级市场可长期使用、可审计、可恢复的分析源。

## 29. 实施状态（2026-07-13）

| 任务 | 状态 | 已落地能力 |
| --- | --- | --- |
| PMM-00 | completed | v2 合同、枚举/ID 校验、安全路径、v1 兼容和四类状态机 |
| PMM-01 | completed | 共享 PDF submission service，workspace 兼容迁移，profile/source context 透传 |
| PMM-02 | completed | Deal ACL 招股书上传、原件归档、自动 parser 提交、UserArtifact 和错误合同 |
| PMM-03 | completed | 状态 reconcile、不可变 staging/rename、逐文件 hash、并发幂等和启动恢复 |
| PMM-04 | completed | 章节/文本/页码/财务能力判定、自动激活、人工 review、disable/supersede |
| PMM-05 | completed | Deal PDF archive Evidence provider、页码/block/bbox 引用和 Evidence snapshot |
| PMM-06 | completed | `cn_a_share_prospectus` parser profile、profile-aware hash、coverage/期间检查 |
| PMM-07 | completed | 招股书表单、轮询、质量/capability/版本链及移动/桌面响应式界面 |
| PMM-08 | completed | receipt/source/snapshot/ResearchIdentity 绑定、stale preflight 和报告审计身份 |
| PMM-09 | completed | fake parser/失败恢复/BOLA/超限/重复/symlink/并发/E2E 发布门禁 |

验证结果：

- PMM API 定向：`72 passed`。
- 既有 startup/preflight/R2-R4 定向回归：`12 passed`。
- PDF parser 全量：`488 passed, 10 skipped, 2 subtests passed`。
- 前端单元测试：`309 passed`。
- Playwright 材料中心移动端/桌面端：`2 passed`。
- Python compile 与 `git diff --check`：通过。
- API 全量：`1859 passed, 2 skipped, 7 failed`；失败来自并行工作树中未由本方案修改的财务 guard/fallback 断言和缺少 `pytest-asyncio` 的 runtime coordination 用例。
- 前端 lint：通过；build 被并行工作树中未由本方案修改的 `meeting-transcription/meetingStream.ts:280` 类型错误阻断。
