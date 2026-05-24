# FinSight PDF to Markdown

FinSight PDF to Markdown 是一个面向中国上市公司财报 PDF 的本地解析、复核和结构化抽取 Web 工具。它不是单纯的“PDF 转 Markdown”：项目本身负责上传、队列、任务状态、结果缓存、质量诊断、表格溯源、人工修正、财务数据抽取和勾稽校验；底层 PDF 解析能力由本地 MinerU API 与 VLM 服务提供。

核心链路：

```text
PDF 上传
  -> SQLite 本地任务队列
  -> MinerU / VLM 解析
  -> Markdown 与中间产物落盘
  -> 页码标记、表格索引、质量报告
  -> 三大表与主要财务指标结构化抽取
  -> 财务勾稽校验
  -> 前端溯源复核与人工修正
```

当前代码中的主要 schema / rule 版本：

| 产物 | 当前版本 |
| --- | --- |
| `quality_report.json` | `10` |
| `content_list_enhanced.json` | `8` |
| `document_full.json` | `1` |
| `financial_data.json` | `13` |
| `financial_checks.json` | `12` |
| 财务规则 | `financial_rules_v14` |

可以通过 `/api/health` 查看正在运行的 Web 进程实际加载的版本。如果代码已更新但健康接口仍显示旧版本，说明运行进程尚未重启。

## 核心能力

- 支持一次上传最多 5 个 PDF，写入本地持久化队列。
- 队列 worker 会在 MinerU 与 VLM 均健康时提交任务，并避免同时向上游提交多个活跃任务。
- 支持 `hybrid-http-client`、`pipeline`、`vlm-http-client` 后端，以及 `auto`、`txt`、`ocr` 解析方式。
- 支持页码范围、公式识别、表格识别等提交参数。
- 自动保存 Markdown、MinerU 中间 JSON、模型输出、content list、图片资源和结果摘要。
- 尽量完整复现 PDF 内容：正文、表格、图片、图表、公式、页码、bbox、目录候选、脚注、附注关系和 PDF 原页预览会被拆成可阅读 Markdown 与可追溯 JSON 两条线保存。
- 自动生成质量报告、表格索引、增强来源索引、完整增强版 Markdown 与完整解析总账 JSON。
- 自动抽取资产负债表、利润表、现金流量表、主要会计数据和主要财务指标。
- 自动执行财务勾稽校验，覆盖三大表表内公式、跨章节指标一致性、粗略财务指标复核和同比异常提示。
- 前端提供 Markdown 预览、关键表候选、优先复核表、PDF 原页渲染、bbox 高亮、页级阅读视图和人工表格修正。
- 解析、复核、校验闭环：每条财务事实都尽量保留来源表、期间、单位、行号、PDF 页码和候选证据，便于从结果回到原 PDF。
- 旧任务在读取时会按需补齐缺失或过期的质量、财务和增强产物。
- 可选接入本地 OpenAI-compatible LLM/vLLM 表格裁判；默认关闭，且只判断表格性质，不抽取金额。

## 解析与复核亮点

这个项目的重点是“把 PDF 解析成可复核证据链”，而不是只输出一份看起来像原文的 Markdown。

- `result.md` 保留正文和表格主干，并插入 `[PDF_PAGE: N]` 标记，让 Markdown 可以按 PDF 页回溯。
- `content_list.json` 保留 MinerU 原始内容块，包括 `page_idx`、`bbox`、表格体、caption、footnote、图片路径等上游证据。
- `content_list_enhanced.json` 将 Markdown 表格和 content list 做二次对齐，标注每张表的来源置信度、PDF 页码、bbox、多级表头、脚注、目录、图片语义和财报项目附注关系。
- `result_complete.md` 不覆盖原文，而是在末尾追加“PDF 可恢复信息附录”，把 Markdown 难表达的 PDF 结构补回来。
- `document_full.json` 是完整解析总账，把原文、结构、质量、财务、校验、图片和产物索引集中到一个机器可读文件。
- 前端复核工作台支持从质量报告候选表跳回 Markdown 行、PDF 页、表格 bbox 和原页 PNG；能编辑表格并把人工修正保存为独立证据。
- 财务校验不会只给“通过/失败”一句话，而是记录 `rule_id`、公式、左右值、差异、容差、输入字段、来源表和降级原因。

因此，一份 PDF 完成解析后，理想状态下会同时具备三种视角：给人读的 Markdown、给机器处理的结构化 JSON、给审计/复核回看的 PDF 原页和 bbox 证据。

## 项目结构

```text
pdf2md_web/
  app.py                         Flask 后端、任务队列、路由、质量报告、溯源与下载接口
  financial_extractor.py          财务报表/指标抽取、行业规则、财务勾稽、可选 LLM 裁判
  mineru_client.py                MinerU/VLM 健康检查、JSON 请求、流式 multipart 上传
  path_config.py                  运行数据目录解析，兼容 legacy 与 data/ 分层布局
  artifact_manager.py             运行产物清理工具
  quality_report.py               质量报告常量与 schema 版本
  quality_engine.py               可测试的质量报告辅助逻辑
  task_store.py                   任务状态常量与状态判断
  pdf_source_viewer.py             PDF/source 视图相关辅助代码
  run.sh                          启动 Web 应用；依赖已有 MinerU/VLM 服务
  requirements.txt                Web 层直接 Python 依赖
  static/app.js                   单页前端交互逻辑
  templates/index.html            单页 UI 模板与样式
  scripts/                        离线重建、诊断、回测与评测脚本
  tests/                          单元测试
  uploads/                        运行数据：上传后的原始 PDF
  results/                        运行数据：Web 缓存的 Markdown 与衍生产物
  output/                         运行数据：MinerU 原始输出或排障材料
  tasks.db                        运行数据：SQLite 任务数据库
  .financial_llm_cache/           运行数据：可选 LLM 表格裁判缓存
```

`uploads/`、`results/`、`output/`、`tasks.db` 和 `.financial_llm_cache/` 都是运行数据，不是纯代码资产。迁移或备份时需要按目标决定是否一起复制。

## 依赖

Python 依赖见 `requirements.txt`：

```text
Flask>=3.0,<4
pypdf>=4,<7
```

运行完整功能还需要：

- MinerU API：默认 `http://127.0.0.1:8003`
- VLM API：默认 `http://127.0.0.1:8002`
- `pdftoppm`：用于前端按需渲染 PDF 原页 PNG
- 一个可运行 Python 环境；仓库自带 `run.sh` 默认使用 `/home/maoyd/.venvs/mineru_native`

`run.sh` 只启动 Web 应用，不会启动 MinerU、VLM 或 vLLM。上游服务需要提前启动并保持健康。

## 启动

当前机器如果已有 `/home/maoyd/.venvs/mineru_native`：

```bash
cd /home/maoyd/finsight/pdf2md_web
./run.sh
```

默认监听：

```text
http://127.0.0.1:5000
```

也可以直接用 Python 启动：

```bash
cd /home/maoyd/finsight/pdf2md_web
python3 -m pip install -r requirements.txt
python3 app.py
```

常用覆盖项：

```bash
PORT=5001 \
HOST=127.0.0.1 \
MINERU_API_URL=http://127.0.0.1:8003 \
VLM_API_URL=http://127.0.0.1:8002 \
./run.sh
```

默认只监听 `127.0.0.1`。如果需要暴露给局域网，请显式设置 `HOST=0.0.0.0`，并建议同时设置访问 token：

```bash
PDF2MD_ACCESS_TOKEN='change-me' HOST=0.0.0.0 ./run.sh
```

设置 token 后，请用以下任一方式访问 API：

- URL 参数：`http://127.0.0.1:5000/?token=<PDF2MD_ACCESS_TOKEN>`
- 请求头：`X-PDF2MD-Token: <PDF2MD_ACCESS_TOKEN>`
- 首次带 token 打开首页后，后端会写入 `pdf2md_token` cookie

## 数据目录

默认保持历史布局，运行数据直接落在项目根目录：

```text
uploads/
results/
output/
tasks.db
.financial_llm_cache/
```

新部署建议启用分层数据目录：

```bash
PDF2MD_USE_DATA_LAYOUT=1 ./run.sh
```

启用后默认路径变为：

```text
data/uploads/
data/results/
data/output/
data/db/tasks.db
data/cache/financial_llm/
data/logs/
```

也可以把数据放到项目外：

```bash
PDF2MD_DATA_DIR=/data/pdf2md ./run.sh
```

单项路径仍可单独覆盖：

| 变量 | 作用 |
| --- | --- |
| `UPLOAD_FOLDER` | 上传 PDF 目录 |
| `RESULTS_FOLDER` | Web 结果目录 |
| `OUTPUT_FOLDER` | MinerU 输出目录 |
| `TASK_DB_PATH` | SQLite 数据库路径 |
| `FINANCIAL_LLM_CACHE_FOLDER` | Web 任务级 LLM 裁判缓存根目录 |
| `PDF2MD_LOG_DIR` | 日志目录；当前代码只负责创建目录 |

## 前端使用流程

1. 打开 Web 页面。
2. 确认 MinerU 与 VLM 健康指示均正常。
3. 选择 1 到 5 个 PDF。
4. 在高级配置中选择后端、解析方式、页码范围、公式识别和表格识别。
5. 点击“批量入队”。
6. 在最近任务或当前任务面板中查看队列位置、日志、阶段和进度。
7. 任务完成后查看 Markdown、产物文件、质量报告、财务校验和可视化溯源。
8. 如表格解析有误，可在表格溯源工作台中编辑并保存修正，再下载修正版 Markdown。

推荐财报默认参数：

- `backend=hybrid-http-client`
- `parse_method=auto`
- `formula_enable=true`
- `table_enable=true`

扫描件、复杂版式、图表密集或局部页质量较差时，可尝试 `vlm-http-client + ocr/auto`，并优先用页码范围局部重跑。

## 任务数据流

上传接口不会在请求线程中等待 MinerU 完整解析。实际链路如下：

1. `POST /api/upload` 保存 PDF 到 `uploads/<task_id>.pdf`。
2. 写入 `tasks.db`，任务状态为 `queued`。
3. 后台 worker 领取最早的 queued 任务并标记为 `submitting`。
4. Web 后端提交到 MinerU `/tasks`，记录上游 `mineru_task_id`。
5. 前端轮询 `/api/status/<task_id>`。
6. MinerU 完成后，后端从 `/tasks/<mineru_task_id>/result` 拉取 Markdown、中间产物和图片。
7. Web 写入 `results/<task_id>/`，并生成质量、财务、增强索引和总账 JSON。
8. 如果任务为 `completed` 但本地没有 `result.md`，状态会降级为 `completed_missing_artifact`，可尝试补拉或重跑。

常见状态：

```text
queued -> submitting -> pending/processing -> completed
completed + missing result.md -> completed_missing_artifact
queued/submitting/pending/processing -> failed
queued/submitting/pending/processing -> cancelled
```

## 结果产物

新任务的结果目录：

```text
results/<task_id>/
```

常见文件：

| 文件/目录 | 说明 |
| --- | --- |
| `result.md` | 最终 Markdown，包含可见 `[PDF_PAGE: N]` 页码标记 |
| `result_complete.md` | 完整增强版 Markdown，在原文后追加 PDF 可恢复信息附录 |
| `corrected_result.md` | 应用人工表格修正后的 Markdown，下载修正版时生成 |
| `document_full.json` | 完整解析总账 JSON |
| `quality_report.json` | A 股财报解析质量报告 |
| `table_index.json` | 表格索引与表级溯源摘要 |
| `financial_data.json` | 结构化财务数据 |
| `financial_checks.json` | 财务勾稽校验结果 |
| `content_list.json` | MinerU 原始内容列表 |
| `content_list_enhanced.json` | Web 增强来源索引 |
| `middle.json` | MinerU 版面中间产物 |
| `model_output.json` | 上游模型输出 |
| `result_payload_summary.json` | MinerU 返回摘要 |
| `images/` | 上游返回的图片、表格图或相关资源 |
| `pdf_pages/` | 前端打开 PDF 原页预览时按需生成的 PNG 缓存 |
| `corrections.json` | 人工复核修正记录；保存修正后出现 |

旧任务可能只有 `results/<task_id>.md`。后端会尽量基于旧 Markdown 补齐质量报告、表格索引和财务产物；如果缺少 `content_list.json`，则无法补出真实 PDF bbox，只能做 Markdown 页码层面的推断。

### 完整复现 PDF 的产物分工

- 原始 PDF：保存在 `uploads/<task_id>.pdf`，是最终事实来源。
- 原始 Markdown：`result.md`，承载可读正文和表格主干，保持简洁。
- 增强 Markdown：`result_complete.md`，追加目录候选、脚注、附注关系、多级表头、图片/图表/公式识别摘要等 PDF 可恢复信息。
- 原始结构：`content_list.json`、`middle.json`、`model_output.json`，用于排查上游解析质量。
- 增强结构：`content_list_enhanced.json`，用于把 Markdown 中的表格、页码、bbox、图片语义、脚注和附注导航统一成可查询索引。
- 复核证据：`quality_report.json`、`table_index.json`、`financial_data.json`、`financial_checks.json` 和 `corrections.json`，用于解释“解析得怎样、财务事实从哪来、勾稽为什么通过或提示复核”。
- 页面证据：`pdf_pages/` 是前端按需渲染的 PDF 原页 PNG，结合 bbox 高亮使用，不替代原始 PDF。

这套分工的好处是：原文保持可读，结构保持可编程，图片/版面证据不被塞进巨大 Markdown，复核时又可以一路从财务数值回到表格、页码、bbox 和原 PDF。

## `document_full.json`

`document_full.json` 面向程序读取、审计追溯、RAG 入库和批量评测。它集中保存：

- 任务元数据、提交参数和源文件引用。
- 完整 Markdown 正文、字符数、行数和按 `[PDF_PAGE: N]` 切分的页级索引。
- MinerU `content_list`、`middle_json`、`model_output` 和结果摘要。
- Web 增强来源索引，包括表格来源、页码/bbox、目录、脚注、附注关系、图片/图表/公式语义块和质量信号。
- 质量报告、财务抽取和财务校验完整内容。
- 图片资源、已渲染 PDF 页资源和白名单可打开产物清单。

它不会把 PDF 原文件、页面截图和图片资源以内嵌 base64 放进 JSON，而是保存 path/url 引用。这样可以保持文件体积可控，同时保留可追溯性。正式引用、审计底稿或争议数据仍应打开原始 PDF 和对应页复核。

`document_full.json` 的关键价值是“完整复盘”：下游系统不需要猜某个数值来自哪里，可以沿着 `financial_data -> quality_report/table_index -> content_list_enhanced -> pdf_pages/uploads` 找到结构化值、候选表、Markdown 行、PDF 页码、bbox 和原始 PDF。

## 质量报告

`quality_report.json` 的目标不是给出“审计结论”，而是形成可追溯的解析质量诊断层。它主要回答：

- 文档类型是什么：完整年报、半年报、季报、年度报告摘要等。
- Markdown 是否覆盖核心章节和关键财务表。
- 关键表能否定位到 Markdown 表、PDF 页码和上游 bbox。
- 候选表是正式主表，还是附注、风险管理表、经营分析表、联营/合营企业信息等易混淆表格。
- 哪些表需要人工优先复核。

核心字段：

```json
{
  "schema_version": 10,
  "report_kind": "annual_report",
  "report_year": 2025,
  "table_count": 337,
  "found_sections": ["重要提示", "公司简介"],
  "found_financial_tables": ["资产负债表", "利润表", "现金流量表"],
  "core_financial_table_candidates": [],
  "indicator_table_candidates": [],
  "key_table_candidates": {},
  "suspicious_tables": [],
  "table_index": [],
  "warnings": [],
  "info_messages": []
}
```

表格来源置信度：

- `content_list_body_exact`：Markdown 表格与 `content_list.table_body` 精确匹配，页码和 bbox 来自上游解析产物。
- `markdown_marker_inferred`：未匹配到上游表体，但能由 `[PDF_PAGE: N]` 推断页码；不会伪造 bbox。
- `unresolved`：既没有精确来源，也没有可用页码标记，应优先复核。

完整年报重点关注 7 类核心表：

- `主要会计数据`
- `主要财务指标`
- `非经常性损益`
- `资产负债表`
- `利润表`
- `现金流量表`
- `所有者权益变动表`

季报不会强制要求所有者权益变动表；年度报告摘要会提示需要切换全文，而不是把缺少三大表直接当作解析失败。

## 结构化财务数据

`financial_data.json` 当前 schema 为 `13`，规则版本为 `financial_rules_v14`。抽取对象包括：

- 合并/母公司资产负债表。
- 合并/母公司利润表。
- 合并/母公司现金流量表。
- 主要会计数据。
- 主要财务指标。

抽取规则会尽量区分正式主表与附注表、经营分析表、风险敞口表、联营/合营企业财务信息等非主表语境。只有识别到可解释的标题、结构、期间列和项目组合时，才会生成正式财务事实。

核心抽取边界：

- 资产负债表需要资产、负债、权益三段结构，不能只凭单个“资产总计”命中。
- 利润表需要营业收入、营业利润、利润总额、净利润、归母净利润等利润桥接项目。
- 现金流量表需要经营、投资、筹资三类现金流及现金及现金等价物桥接项目。
- 期间列会识别 `2025年12月31日`、`期末余额`、`2025年度`、`本期发生额` 等常见披露方式，并结合报告年度标准化。
- 单位会从标题、caption、表头和附近文本解析，支持元、千元、万元、百万元、亿元等倍率。
- 金融行业会启用银行、证券、保险相关字段别名和解释边界，避免把风险到期日分析、敞口表等附表误当主表。

## 财务勾稽校验逻辑

`financial_checks.json` 当前 schema 为 `12`。它不是完整审计程序，而是一组面向解析质量和基础一致性的可解释校验。每条校验都尽量记录：

- `rule_id`：稳定规则编号，例如 `bs.assets_eq_liabilities_plus_equity`。
- `rule_name`：人类可读公式。
- `statement_type` / `scope` / `period`：报表类型、口径和期间。
- `left`：被校验项目名称和值。
- `right`：公式和计算值。
- `diff` / `tolerance`：差异和容差。
- `status`：`pass`、`fail`、`warning` 或 `skipped`。
- `inputs`：参与公式的规范字段。
- `source_tables`：可恢复时记录输入字段来自哪些源表，便于发现混表、续表错位或附注误入。
- `reason`：跳过、软检查提示、解析疑似异常或降级原因。

### 状态语义

| 状态 | 含义 |
| --- | --- |
| `pass` | 左右两端都有值，且差异在容差内 |
| `fail` | 左右两端都有值，差异超出容差，且没有解析证据异常降级理由 |
| `warning` | 需要复核的软提示；可能来自跨章节口径差异、粗略指标、同比异常，或硬公式失败但来源证据疑似异常 |
| `skipped` | 缺字段、缺期间列、缺正式报表或文档类型不适用，当前证据不足以计算该规则 |

总状态 `overall_status` 的计算很直接：只要存在硬 `fail` 就是 `fail`；没有硬失败且至少有一条 `pass` 就是 `pass`；否则是 `skipped`。因此 `overall_status=pass` 不代表所有规则都计算过，它代表没有发现硬性勾稽失败。

### 容差与单位对齐

统一容差函数：

```text
tolerance = max(scale, max(|left|, |right|) * 0.000005, 1.0)
```

其中 `scale` 通常来自报表单位倍率。这个设计同时覆盖三类误差：

- 表内披露单位导致的最小金额尺度。
- 四舍五入和展示精度带来的微小差异。
- 极小值或空值附近的最小绝对容差。

跨章节指标与正式报表比对时，系统会尝试高置信单位自动对齐，候选倍率包括：

```text
1, 1000, 10000, 1000000, 0.001, 0.0001, 0.000001
```

只有换算后进入容差范围才接受该倍率，并会在公式中标注 `* factor`。系统不会为了凑平而任意缩放。

### 资产负债表规则

对每个已识别期间执行：

1. `资产总计 = 负债合计 + 所有者权益合计`
2. `资产总计 = 负债和所有者权益总计`
3. `流动资产合计 + 非流动资产合计 = 资产总计`
4. `流动负债合计 + 非流动负债合计 = 负债合计`
5. 合并口径下：`归属于母公司权益 + 少数股东权益 = 所有者权益合计`

如果报表没有直接抽到 `资产总计`，但能由 `负债和所有者权益总计` 或 `负债合计 + 所有者权益合计` 推导，系统会用可解释的总资产兜底参与检查。母公司/公司口径不会强行执行少数股东权益桥接。

### 利润表规则

对每个已识别期间执行：

1. `利润总额 = 营业利润 + 营业外收入 - 营业外支出`
2. `净利润 = 利润总额 - 所得税费用`
3. `净利润 = 归母净利润 + 少数股东损益`
4. `综合收益总额 = 净利润 + 其他综合收益`

利润表的符号处理是“候选桥接最接近原则”：营业外支出、所得税费用、现金流出等字段在 PDF/Markdown 中可能以正数、负数、括号或符号丢失形式出现，系统会比较 `+` 和 `-` 两种候选，选择与正式合计最接近的方向。

综合收益桥接会按披露口径选择最接近的候选：

- `净利润 + 其他综合收益的税后净额`
- `净利润 + 归属于母公司股东的其他综合收益 + 归属于少数股东的其他综合收益`
- 母公司口径或少数股东 OCI 缺失时：`净利润 + 归属口径其他综合收益`
- 如果综合收益总额与净利润在容差内相等，可按未列示 OCI 为 0 处理

对于少数股东损益明显被 OCR 或表格拆分截断的场景，系统会在严格条件下使用 `净利润 - 归母净利润` 推导少数股东损益参与桥接，并保留输入字段说明。

### 现金流量表规则

对每个已识别期间执行：

1. `经营活动现金流量净额 = 经营现金流入小计 - 经营现金流出小计`
2. `投资活动现金流量净额 = 投资现金流入小计 - 投资现金流出小计`
3. `筹资活动现金流量净额 = 筹资现金流入小计 - 筹资现金流出小计`
4. `现金及现金等价物净增加额 = 经营净额 + 投资净额 + 筹资净额 + 汇率影响`
5. `期末现金及现金等价物余额 = 期初余额 + 现金及现金等价物净增加额`

现金流入/流出同样使用最接近候选处理符号方向。若 PDF 披露为“净减少额”，系统按抽取数值正负参与计算，不再用文本方向强行改写数值。

### 跨章节软一致性

系统会把“主要会计数据/主要财务指标”与正式三大表做同期间、同口径比对：

1. `主要会计数据营业收入 = 合并利润表营业收入`
2. `主要会计数据利润总额 = 合并利润表利润总额`
3. `主要会计数据归母净利润 = 合并利润表归母净利润`
4. `主要会计数据经营现金流 = 合并现金流量表经营活动现金流量净额`
5. `主要会计数据总资产 = 合并资产负债表资产总计`
6. `主要会计数据归母净资产 = 合并资产负债表归母权益`

这些规则使用 `_soft_check`：如果不一致，状态会从 `fail` 降为 `warning`。原因是主要会计数据可能存在重述后口径、调整后指标、普通股股东口径、监管口径、单位披露差异或抽取错位，直接判硬失败容易误伤。

### 粗略指标与同比提示

系统还生成三类辅助复核线索：

- `ratio.asset_liability_ratio`：`资产负债率 = 负债合计 / 资产总计`。非金融企业资产负债率较高时记为 `warning`；银行、证券、保险不套用普通企业高负债提示。
- `rough.parent_nav_per_share`：`每股净资产 ≈ 归母权益 / 期末总股本`。不一致时为 `warning`，因为正式口径可能涉及股本、优先股、永续债或单位问题。
- `rough.basic_eps`：`基本每股收益 ≈ 归母净利润 / 期末总股本`。不一致时为 `warning`，因为正式 EPS 通常使用加权平均股数。

同比异常提示覆盖营业收入、归母净利润、经营活动现金流量净额、总资产、总负债。若本期相对上期绝对变化超过 30%，生成 `warning`，提示结合年报正文和附注解释复核。这不是勾稽失败。

### 解析证据异常降级

有些公式左右不平，不一定代表财务披露错误，也可能是解析证据错源。当前硬公式失败会在两类情况下自动降级为 `warning`：

- `source_scope_mismatch_suspect`：公式左右两端来源表号跨度异常，常见于上游把合并表、母公司续表或附注表混入同一口径。
- `parse_suspect_magnitude_mismatch`：左右两端金额量级差异极大，常见于 OCR 少位、逗号切分、单元格截断或表格拆分。

降级后仍会保留 `diff`、`tolerance`、`source_tables` 和 `reason`，让人工复核能看到问题，而不是把解析错位伪装成真正财务硬失败。

### 报告类型处理

- 完整年报、半年报或季报会尽量抽取正式三大表；缺少合并资产负债表、利润表或现金流量表时，`warnings` 会明确提示。
- 年度报告摘要或半年度报告摘要通常没有完整三大表；系统会提示“摘要不能当作完整年报”，而不是把缺三大表静默视为通过。
- 季报天然不要求所有年报专属披露项，质量报告和财务检查会按可用证据执行。

## 可选 LLM 表格裁判

默认不调用大模型。只有同时配置以下变量，才会启用 OpenAI-compatible LLM/vLLM 裁判：

```bash
FINANCIAL_LLM_JUDGE_ENABLED=1 \
FINANCIAL_LLM_API_BASE=http://127.0.0.1:8000 \
FINANCIAL_LLM_MODEL=qwen3.6 \
./run.sh
```

边界很重要：

- LLM 只判断候选表是否为正式资产负债表、利润表或现金流量表。
- LLM 不抽取金额，不写入财务事实。
- 金额、期间列、单位倍率、科目别名和勾稽仍由脚本规则处理。
- 裁判结果会按任务缓存，避免重复请求。

相关环境变量：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `FINANCIAL_LLM_JUDGE_ENABLED` | `0` | 是否启用 LLM 表格裁判 |
| `FINANCIAL_LLM_API_BASE` | 空 | OpenAI-compatible API base，可填根路径或 `/v1` |
| `FINANCIAL_LLM_MODEL` | `qwen3.6` | 裁判模型名 |
| `FINANCIAL_LLM_TIMEOUT` | `45` | 请求超时秒数 |
| `FINANCIAL_LLM_CACHE_FOLDER` | 见数据目录 | Web 任务级缓存根目录 |
| `FINANCIAL_LLM_CACHE_DIR` | 空 | 直接调用 `financial_extractor.py` 时使用的缓存目录 |

## 可视化复核

前端质量报告里的“关键表候选”和“优先复核表”可点击打开溯源工作台：

- 自动跳转到 Markdown 对应行。
- 展示表序号、Markdown 行号、PDF 页码、来源置信度、bbox、表题、单位、行列规模、数字密度和可疑原因。
- 左侧可在“当前 PDF 页”和“当前表格”之间切换。
- 右侧按需渲染 PDF 原页 PNG，并根据 bbox 高亮目标表格。
- 若表格 HTML 或同页文本块提供可信坐标，点击单元格可尝试定位单元格或文本锚点。
- 无法高置信定位时只展示表级 bbox，避免制造假精确。
- 表格可编辑，保存后写入 `corrections.json`。

PDF 原页缓存路径：

```text
results/<task_id>/pdf_pages/page_<page_number>.png
```

保存修正后：

- `corrections.json` 记录人工修改。
- `result_complete.md` 会按需刷新。
- 下载修正版时生成或更新 `corrected_result.md`。

## API

如果设置了 `PDF2MD_ACCESS_TOKEN`，除首页以外的 API 都需要 token。

### 健康检查

```http
GET /api/health
```

返回 Flask、MinerU、VLM、提交可用性、warning 和 schema/rule 版本。

### 上传并入队

```http
POST /api/upload
Content-Type: multipart/form-data
```

字段：

| 字段 | 说明 |
| --- | --- |
| `files` / `file` | PDF 文件；`files` 支持批量 |
| `backend` | `hybrid-http-client`、`pipeline`、`vlm-http-client` |
| `parse_method` | `auto`、`txt`、`ocr` |
| `start_page_id` | 起始页，非负整数；按 MinerU 参数语义传递 |
| `end_page_id` | 结束页，非负整数 |
| `formula_enable` | 是否启用公式识别 |
| `table_enable` | 是否启用表格识别 |

返回本地 `task_id` 列表。

### 查询状态

```http
GET /api/status/<task_id>?since=<log_index>
```

返回任务状态、阶段、队列位置、进度估算、日志增量和结果是否就绪。

### 获取结果

```http
GET /api/result/<task_id>
```

返回 Markdown 和产物状态；必要时会从 MinerU 补拉结果并补齐衍生产物。

### 质量与财务

```http
GET /api/quality/<task_id>
GET /api/financial/<task_id>
```

读取或按需重建质量报告、结构化财务数据和财务校验。

### 产物文件

```http
GET /api/artifact/<task_id>/<artifact_name>
```

允许直接打开：

```text
result.md
result_complete.md
document_full.json
quality_report.json
table_index.json
financial_data.json
financial_checks.json
middle.json
content_list.json
content_list_enhanced.json
model_output.json
images
images/<filename>
```

### 下载

```http
GET /api/download/<task_id>
GET /api/download_complete/<task_id>
GET /api/download_corrected/<task_id>
```

分别下载原始 Markdown、完整增强版 Markdown、人工修正版 Markdown。

### 溯源与修正

```http
GET  /api/source/<task_id>/table/<table_index>
GET  /api/source/<task_id>/page/<page_number>
GET  /api/pdf_page/<task_id>/<page_number>
POST /api/source/<task_id>/table/<table_index>/correction
```

用于表级溯源、页级阅读视图、PDF 原页渲染和人工表格修正。

### 任务管理

```http
GET    /api/tasks
POST   /api/cancel/<task_id>
POST   /api/refetch/<task_id>
POST   /api/reparse/<task_id>
DELETE /api/tasks/<task_id>
```

- `cancel`：停止本地跟踪；如果已有上游 `mineru_task_id`，会尝试通知 MinerU 删除任务。
- `refetch`：强制从 MinerU 结果接口重新拉取，适用于本地结果缺失。
- `reparse`：复制原始上传 PDF，创建一个新任务重新解析。
- `DELETE`：删除任务记录、上传 PDF、本地结果目录和旧版 Markdown。

## 数据库

任务状态保存在 SQLite：

```text
tasks.db
```

主要字段包括：

- 本地 `task_id`
- 上游 `mineru_task_id`
- 文件名、大小、PDF 页数
- 状态、阶段、时间戳
- 上传路径、Markdown 路径
- 取消标记、错误信息
- 提交配置 JSON
- 日志 JSON
- 最近一次上游状态 payload

导入 `app.py` 不会自动启动 queue worker；首次请求或显式调用 `initialize_app(start_worker=True)` 后才会初始化数据库并启动后台 worker。

## 清理策略

默认值偏保守，不自动删除 Web 层任务数据：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `TASK_RETENTION_HOURS` | `0` | `0` 表示不按时间清理任务、上传 PDF 和 `results/` |
| `CLEANUP_INTERVAL_SECONDS` | `600` | 清理检查间隔 |
| `CLEANUP_ORPHAN_DATA` | `0` | 是否清理未被 `tasks.db` 引用的 uploads/results 子项 |
| `CLEANUP_OUTPUT_FOLDER` | `0` | 是否清理 `output/` 过期子目录 |
| `OUTPUT_RETENTION_HOURS` | `24` | `output/` 清理保留小时数；仅在 `CLEANUP_OUTPUT_FOLDER=1` 时生效 |

如果设置 `TASK_RETENTION_HOURS > 0`，Web 层会清理终态任务的数据库记录、上传 PDF、Markdown 和结果目录。活跃任务不会被按时间清理。

## 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `PORT` | `5000` | Flask 端口 |
| `HOST` | `127.0.0.1` | Flask 监听地址 |
| `FLASK_DEBUG` | `0` | 是否启用 Flask debug |
| `MINERU_API_URL` | `http://127.0.0.1:8003` | MinerU API 地址 |
| `VLM_API_URL` | `http://127.0.0.1:8002` | VLM API 地址 |
| `PDF2MD_ACCESS_TOKEN` | 空 | 可选访问 token |
| `MAX_FILE_SIZE` | `104857600` | 单个 PDF 最大字节数，默认 100 MB |
| `MAX_FILES_PER_UPLOAD` | `5` | 单次最多上传文件数 |
| `MAX_BATCH_UPLOAD_SIZE` | `MAX_FILE_SIZE * MAX_FILES_PER_UPLOAD` | Flask 请求体大小上限 |
| `QUEUE_POLL_SECONDS` | `3` | 后台队列 worker 等待间隔 |
| `PAGE_ESTIMATE_SECONDS` | `18` | 进度估算使用的单页秒数 |
| `STATUS_CACHE_SECONDS` | `1.5` | 上游状态查询缓存间隔 |
| `MINERU_SUBMIT_TIMEOUT_SECONDS` | `900` | 提交 PDF 到 MinerU 的超时 |
| `MINERU_STATUS_TIMEOUT_SECONDS` | `30` | 查询 MinerU 状态超时 |
| `MINERU_STATUS_FAILURE_TOLERANCE` | `6` | 连续状态查询失败多少次后标记失败 |
| `STALE_SUBMITTING_SECONDS` | `1800` | submitting 卡住后的恢复阈值 |
| `PDF2MD_FILE_CACHE_MAX_ITEMS` | `32` | 本地文件读取缓存条目数 |
| `PDF2MD_USE_DATA_LAYOUT` | `0` | 是否启用 `data/` 分层布局 |
| `PDF2MD_DATA_DIR` | 空 | 自定义数据根目录；设置后自动启用分层布局 |

## 离线脚本

所有脚本默认读取当前 `app.py` 解析出的 `RESULTS_FOLDER` 和 `DB_PATH`，也可通过参数覆盖。

### 重建财务与质量产物

```bash
/home/maoyd/.venvs/mineru_native/bin/python scripts/rebuild_financial_artifacts.py --order recent --limit 50 --force
```

常用参数：

- `--task-id <id>`：只处理指定任务，可重复。
- `--dry-run`：只报告 stale/current。
- `--force`：即使当前 schema 已最新也重建。
- `--results-dir` / `--db`：指定结果目录和数据库。

### 重建增强索引与完整 Markdown

```bash
/home/maoyd/.venvs/mineru_native/bin/python scripts/rebuild_content_list_enhanced.py --limit 50 --write-complete-md
```

用于补齐或升级 `content_list_enhanced.json` 与 `result_complete.md`。

### 诊断 content_list 覆盖率

```bash
/home/maoyd/.venvs/mineru_native/bin/python scripts/diagnose_content_list_quality.py --limit 80 --order recent
```

单任务深查：

```bash
/home/maoyd/.venvs/mineru_native/bin/python scripts/diagnose_content_list_quality.py \
  --task-id <task_id> \
  --search-artifacts \
  --sample-limit 8
```

重点看 Markdown 表格总数、`content_list.table_body` 表格数、精确匹配表数、只能由 Markdown 页码推断的表数，以及缺页码或无来源表样本。

### 回测近期结果

```bash
/home/maoyd/.venvs/mineru_native/bin/python scripts/backtest_recent_results.py --limit 80
```

该脚本不会改写 `results/`，会在内存中按当前规则重算质量、财务抽取和勾稽。退出码：

- `0`：未发现完整年报核心候选缺口或财务硬失败。
- `1`：存在需要排查的问题。

### 解析质量量化评测

```bash
/home/maoyd/.venvs/mineru_native/bin/python scripts/evaluate_parse_quality.py --limit 110
```

默认写入 `reports/`：

- `parse_quality_eval_<timestamp>.json`
- `parse_quality_eval_<timestamp>.md`

评分是工程代理指标，不是人工标注真值。它用于持续回测和定位低质量样本。

## 开发与验证

运行单元测试：

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

当前测试重点覆盖：

- 运行数据路径解析与 legacy/data 布局兼容。
- 任务状态语义和缺失产物状态。
- PDF 页码标记与表格页码推断。
- `content_list` 精确表格来源匹配。
- 增强索引中的脚注、目录、财报项目附注关联。
- 财务报表抽取、财务指标抽取、银行/证券/保险行业规则。
- 年报摘要、季报、LLM 裁判边界。

README 修改通常不需要跑完整测试；代码逻辑、schema 或抽取规则变更后建议至少跑单元测试，并对近期任务执行一次 `backtest_recent_results.py`。

## 运维排查

### 前端提示 MinerU 不可提交

先看健康接口：

```bash
curl -s http://127.0.0.1:5000/api/health
```

重点字段：`mineru`、`vlm`、`submit_ready`、`warning`、`mineru_detail`、`vlm_detail`。如果 MinerU 或 VLM 为 false，先恢复上游服务。Web worker 只有在 `submit_ready=true` 时才会提交 queued 任务。

### 任务卡在 submitting

`STALE_SUBMITTING_SECONDS` 控制 submitting 卡住后的恢复阈值，默认 1800 秒。检查上游 MinerU 是否已重启、提交请求是否超时，以及任务日志中是否出现提交失败。

### completed 但没有 Markdown

状态会转为 `completed_missing_artifact`。可尝试：

```http
POST /api/refetch/<task_id>
```

如果上游结果已经被 MinerU 清理，只能使用 `POST /api/reparse/<task_id>` 基于原始上传 PDF 创建新任务；前提是 `uploads/<task_id>.pdf` 仍存在。

### 旧任务没有 PDF 页码或 bbox

旧任务如果缺少 `content_list.json`，系统无法恢复真实 PDF bbox。它最多能根据 Markdown 中已有的 `[PDF_PAGE: N]` 标记推断页码。

### PDF 数量和 Markdown 数量不一致

这是正常现象：PDF-only 通常表示任务尚未完成、结果尚未取回、本地结果被清理，或上游结果已丢失；MD-only 通常来自旧版历史结果、手工导入结果，或原始上传 PDF 被清理。

### 表格页码或高亮不准

优先检查 `table_index.json` 或 `content_list_enhanced.json` 中的来源：

- `content_list_body_exact`：页码和 bbox 来自上游 content list，可信度较高。
- `markdown_marker_inferred`：只有页码推断，没有 bbox。
- `unresolved`：没有可靠来源，需要人工复核。

### 产物 schema 不是最新

先确认 Web 进程版本：

```bash
curl -s http://127.0.0.1:5000/api/health
```

重启 Web 进程后，对历史结果运行重建脚本：

```bash
/home/maoyd/.venvs/mineru_native/bin/python scripts/rebuild_financial_artifacts.py --force --order recent --limit 50
/home/maoyd/.venvs/mineru_native/bin/python scripts/rebuild_content_list_enhanced.py --limit 50 --write-complete-md
```

## 备份与迁移

最小代码迁移：

```text
*.py
requirements.txt
run.sh
static/
templates/
scripts/
tests/
README.md
```

带历史任务和产物迁移：

```text
uploads/
results/
output/                 # 可选，通常体积大
tasks.db
.financial_llm_cache/   # 可选
reports/                # 可选
```

如果目标机器使用不同路径，建议优先设置：

```bash
PDF2MD_DATA_DIR=/data/pdf2md
```

这样可以把运行数据和代码目录分离，后续升级代码更稳。

## 专业边界

- 本项目生成的是自动解析和自动校验证据链，不是审计结论。
- `financial_checks` 的 `pass` 表示当前抽取事实在规则容差内一致，不表示 PDF 披露一定无误。
- `warning` 是复核线索，不等同硬失败。
- `skipped` 表示当前证据不足以执行该条规则，不表示通过。
- `document_full.json`、`content_list_enhanced.json` 和 `result_complete.md` 适合追溯、RAG 和复核；正式引用仍以原始 PDF 为准。

## 后续优化方向

- 提高 `content_list.table_body` 对 Markdown 表格的精确覆盖率，让更多表拥有真实 bbox。
- 增强金融行业、境外上市公司和特殊披露格式的主表识别。
- 为 LLM 裁判增加更细的可解释回放与离线评估集。
- 增加更严格的 API 集成测试和端到端样本测试。
- 将部署脚本、服务托管配置和运行数据路径进一步模板化，减少机器专属假设。
