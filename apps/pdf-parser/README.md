# SIQ PDF 解析服务

## 模块定位

`apps/pdf-parser` 是 SIQ 面向财报 PDF 的专业解析运行时。它服务于 A 股为主的财报解析链路，也为部分港股、日股、韩股和 bridge 场景提供 PDF 版面事实能力。这个模块的目标不是“把 PDF 变成一段文本”，而是把财报 PDF 变成可校验、可引用、可人工复核的研究底座。

## 产品归属与业务边界

PDF parser 主要服务二级市场投研分析智能体集群，同时作为应用中心的专业财报解析能力被一级市场尽调材料复用。

| 产品面 | 关系 | 边界 |
| --- | --- | --- |
| 二级市场 | 官方披露 PDF -> 财报结构化事实 -> analysis/factcheck/tracking/legal | 提供 page/table/source/financial artifact，不直接给投资建议 |
| 一级市场 | 尽调材料中的财报、审计报告、招股书附件等可复用解析能力 | 作为证据生产工具，不替代专家判断和投委会决策 |
| 应用中心 | `/parse*` 与文档/向量工作流共享 artifact、quality 和 source 访问心智 | 财报专用语义由本服务负责，通用文件归 `apps/document-parser` |

## 在系统中的位置

```text
PDF 披露文件
  -> apps/pdf-parser
     -> Markdown / content list / quality / financial data / source APIs
     -> Wiki / PostgreSQL / 前端溯源 / Agent 消费
```

它在系统里承担的是“财报专业解析面”的角色：

- 上游接收上传 PDF 或下载后的披露文件。
- 中游通过 MinerU / VLM 和本地增强逻辑生成标准产物。
- 下游把结果交给 API、Web、db/imports、市场 package 构建和 Hermes 消费。

## 核心能力

| 能力 | 说明 |
| --- | --- |
| 任务化解析 | 上传 PDF 后进入 SQLite 任务队列，支持状态查询、取消、重试与删除 |
| Markdown 与结构产物 | 输出 Markdown、`document_full.json`、`content_list_enhanced.json` 等标准产物 |
| 表格与页码增强 | 建立页码、表格索引、页面锚点和来源坐标 |
| 质量报告 | 输出 `quality_report.json`，区分可用、warning、失败等状态 |
| 财务抽取 | 生成 `financial_data.json` 和 `financial_checks.json` |
| 人工修正 | 支持表格关系修正、逻辑表拆分 / 合并和纠错回放 |
| Source API | 提供表格、页面、页图和 artifact 下载接口 |

## 当前最新状态

| 方向 | 状态 | 说明 |
| --- | --- | --- |
| 港股商业 MVP | 作为 HK 年报 package 的 parser 入口 | 解析结果进入 Wiki evidence package，并由 package quality gates 决定能否入库 |
| 多市场 PDF profile | KR / JP / HK 等市场有专属 profile 与质量适配 | 同一 parser 保留市场差异，不把所有财报当成 A 股格式 |
| 质量 artifact | `content_list_enhanced.json`、`quality_report.json`、`financial_data.json`、`financial_checks.json` 作为核心事实层 | 后续 rules、API、Web、importer 和 Agent 都依赖这些产物 |
| Source 回跳 | 页图、表格、Markdown、bbox 和 source payload 联动 | 支撑研究员人工复核和 Agent 引用回放 |
| 架构治理 | route payload owner 正在继续下沉 | 避免 `pdf_parser_app_impl.py` 继续膨胀，保持 response contract 稳定 |

PDF parser 的商业价值在于把“PDF 年报”变成可复核的证据资产，而不是只产出一份 Markdown。质量状态、表格坐标和财务勾稽共同决定后续能否进入数据库和语义层。

## 高精度解析机制

SIQ 对财报 PDF 采用“多视图一致性”而不是单结果解析。Markdown 适合阅读，content list 适合重建段落/表格顺序，页图与 bbox 适合人工复核，`document_full.json` 适合跨服务消费，任一视图都不能单独代表完整事实。

| 精度层 | 机制 | 结果 |
| --- | --- | --- |
| 原始身份 | task、market、源 PDF、result manifest 与 artifact hash 绑定 | 防止任务重跑后引用到旧结果 |
| 版面恢复 | MinerU/VLM 上游 + page marker + enhanced content list | 保留章节、段落、公式、表格和页序 |
| 表格恢复 | table index、逻辑表 merge/split、row/cell/bbox、table relation | 跨页表与复杂附注可人工定位和修正 |
| 多市场适配 | CN/HK/JP/KR/EU/US quality/profile adapter | 不把不同语言、监管格式和报告结构强行套用同一规则 |
| 质量评估 | coverage、结构完整性、关键 artifact、市场专属 warning | 解析结果以 pass/warning/fail/degraded 表达，不静默乐观 |
| 可回放纠错 | correction endpoint、修正包、重建脚本和 result contract audit | 人工修正可以沉淀并在重跑后复核 |

页面、表格、bbox、Markdown 行与 artifact hash 的共同存在，是后续问答能做到“回答一句、回跳一处”的基础。图片和图表不会只被丢成占位符：页图/figure 作为多模态证据保留，可由本地 VLM 或 Nemotron 类模型做二次理解，但模型描述不会覆盖原始 source locator。

## 财务抽取与勾稽校验

`financial_data.json` 保存抽取事实，`financial_checks.json` 保存检查结果，二者不能合并成一个“看起来正确”的结果文件。抽取时重点保留：

- 原始文本与规范值并存，币种、金额倍率、正负号、括号负数、期间起止和审计口径分开处理。
- QTD/YTD、当期/上期、期末/期初不靠列位置猜测，而由市场 profile、表头和 source metadata 共同判断。
- 三大表是否齐全、关键指标是否缺失、source locator 是否完整进入质量报告。
- parser 侧校验只证明 artifact 内部自洽；跨市场 canonical rule、正式报表桥和回答级计算守卫由 `services/market-report-rules` 与 `apps/api` 继续完成。

因此解析成功不等于可入库，更不等于可直接用于投资结论。只有通过 package quality gate 的产物才能晋升到 canonical/retrieval/production。

## 技术难点

`apps/pdf-parser` 的难度不在“把 PDF OCR 出来”，而在“把财报里的结构性事实提出来且可追溯”：

- 版面复杂：财报中的目录、注释、图表、跨页表、附注表与主表经常交叉出现。
- 表格语义复杂：同一张表里可能同时包含单位、期间、子项目、合并范围和脚注，需要避免把结构误当数值。
- 页码与内容不同步：PDF 页图、Markdown、content list 和 table index 必须保持足够一致，前端才能可靠跳回证据。
- 财务抽取风险高：QTD / YTD、单位缩放、币种、审计状态和期间口径都可能让结果失真。
- 质量门禁必须诚实：当上游解析失败时，系统宁可失败，也不能静默退化成低质量文本输出。

## 关键接口或标准产物

### 关键 API

| API | 用途 |
| --- | --- |
| `GET /api/health` | 查看服务状态、上游地址与 artifact 版本 |
| `GET /api/tasks` | 列出任务 |
| `POST /api/upload` | 上传 PDF 并创建解析任务 |
| `POST /api/cancel/<task_id>` | 取消任务 |
| `POST /api/refetch/<task_id>` | 重新抓取结果视图 |
| `POST /api/reparse/<task_id>` | 触发重新解析 |
| `GET /api/status/<task_id>` | 查看任务状态和日志 |
| `GET /api/result/<task_id>` | 查看主结果 |
| `GET /api/quality/<task_id>` | 查看质量报告 |
| `GET /api/financial/<task_id>` | 查看财务抽取结果 |
| `GET /api/artifact/<task_id>/<artifact_name>` | 读取标准 artifact |
| `GET /api/source/<task_id>/table/<table_index>` | 表格溯源 |
| `GET /api/source/<task_id>/page/<page_number>` | 页面溯源 |
| `GET /api/pdf_page/<task_id>/<page_number>` | PDF 页图 |
| `POST /api/source/<task_id>/table/<table_index>/correction` | 表格人工修正 |
| `GET /api/download/<task_id>` | 下载结果包 |
| `GET /api/download_complete/<task_id>` | 下载完整归档包 |
| `GET /api/download_corrected/<task_id>` | 下载修正后结果 |
| `DELETE /api/tasks/<task_id>` | 删除任务与运行态产物 |

### 核心 artifact

| 产物 | 作用 |
| --- | --- |
| `document_full.json` | 文档统一事实合同 |
| `content_list_enhanced.json` | 增强后的段落与表格结构层 |
| `quality_report.json` | 解析质量门禁 |
| `table_relations.json` | 表格关系与逻辑表信息 |
| `financial_data.json` | 财务事实抽取结果 |
| `financial_checks.json` | 勾稽与一致性校验结果 |

## 启动方式

### 标准启动

```bash
cd /home/maoyd/siq-research-engine/apps/pdf-parser
./run.sh
```

默认地址：

```text
http://127.0.0.1:15000
```

### 常用覆盖

```bash
cd /home/maoyd/siq-research-engine/apps/pdf-parser
HOST=127.0.0.1 \
PORT=15000 \
MINERU_API_URL=http://127.0.0.1:8003 \
VLM_API_URL=http://127.0.0.1:8002 \
SIQ_PDF2MD_DATA_DIR=/home/maoyd/siq-research-engine/data/pdf-parser \
./run.sh
```

`run.sh` 默认激活 `runtimes/mineru-native` 环境，并连接本机 MinerU / VLM 上游。该脚本只启动 Flask 服务，不会自动拉起上游模型服务。

## 关键环境变量

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `SIQ_MINERU_VENV` | `$PROJECT_ROOT/runtimes/mineru-native` | MinerU Python 环境 |
| `SIQ_PDF2MD_DATA_DIR` | `$SIQ_DATA_ROOT/pdf-parser` | 运行态根目录 |
| `SIQ_PDF_UPLOADS_ROOT` | `$DATA_DIR/uploads` | 上传目录 |
| `SIQ_PDF_RESULTS_ROOT` | `$DATA_DIR/results` | 结果目录 |
| `SIQ_PDF_OUTPUT_ROOT` | `$DATA_DIR/output` | 中间输出目录 |
| `SIQ_PDF_TASK_DB_PATH` | `$DATA_DIR/db/tasks.db` | SQLite 任务库 |
| `SIQ_FINANCIAL_LLM_CACHE_ROOT` | `$DATA_DIR/cache/financial_llm` | 财务判断缓存 |
| `SIQ_PDF2MD_LOG_ROOT` | `$DATA_DIR/logs` | 日志目录 |
| `MINERU_API_URL` | `http://127.0.0.1:8003` | 上游 MinerU API |
| `VLM_API_URL` | `http://127.0.0.1:8002` | 上游视觉模型服务 |
| `TASK_RETENTION_HOURS` | `0` | 任务保留策略 |

## 验证方式

```bash
cd /home/maoyd/siq-research-engine/apps/pdf-parser
python3 -m pytest tests
bash -n run.sh
```

若改动了 source、artifact、quality 或 financial 路由，至少补跑对应测试模块，并手动调用 `/api/health` 与一个任务的 `/api/result/<task_id>` 或 `/api/source/...` 验证链路。

## 维护原则

- 财报质量门禁优先于“尽量返回结果”；低质量结果不能伪装成高可信事实层。
- 与页码、表格索引、source 坐标相关的变更必须验证前端溯源是否还能回跳。
- 任何财务规则更新都应同步反映到版本、测试和 README 描述里。
- 运行态目录、缓存、上传 PDF 和日志不写回源码目录。
- 当上游 MinerU / VLM 失败时，应显式暴露失败而不是偷偷降级为简单文本输出。

## 创新性与商业价值

PDF parser 面向“高价值、低容错”的财务披露。它不仅提取 Markdown，还保留页面、bbox、表格、财务科目、勾稽检查、目录和脚注关系，让解析质量可以被量化和阻断。

| 创新点 | 实现方式 | 商业价值 |
| --- | --- | --- |
| 解析与财务语义并行 | 版面块、表格、`financial_data`、`financial_checks` 同任务产出 | 研究员无需在 OCR 文本上重新手工搭建三表 |
| 证据坐标稳定化 | page/table/row/column/Markdown line 与 artifact hash | 数字可回到披露原页，支持复核和审计抽样 |
| 质量可计算 | statement coverage、bridge checks、parser warnings、quality report | 低质量结果在入库前被识别 |
| 市场 profile 隔离 | 市场差异在 profile 与规则层消化 | 扩展全球市场同时保留会计与披露差异 |

技术难点集中在扫描件、跨页表、单位/币种、负数括号、合并口径、脚注和多语言标题。任一环节失真，都可能让看似正确的财务数字失去可用性。
