# SIQ PostgreSQL 入库工具

## 目录职责

`db/imports` 保存把 `document_full.json`、通用文档 package 和多市场 evidence package 写入 PostgreSQL 的导入工具，以及少量只读查询辅助入口。它负责把文件型证据层转换成结构化事实层，供 Agent、查询工具和回归分析复用。

## 产品归属与业务边界

PostgreSQL 入库层是三条产品面的结构化事实账本。

| 产品面 | 作用 | 边界 |
| --- | --- | --- |
| 二级市场 | 将 market evidence package、financial_data、source map 和 document_full 入库，支撑 Agent 查询和报告复核 | 不绕过 package quality gate，不把低质量 artifact 静默固化 |
| 一级市场 | 支撑 deal evidence、项目材料和 IC 阶段产物的结构化查询扩展 | 不替代 Wiki/data room 原始证据，也不替代人工签核 |
| 应用中心 | 将通用文档、解析结果和批处理产物写入可查询 schema | 数据库是结构化索引和账本，原始事实仍需回到 package/artifact |

多市场财报 evidence package 入库必须对齐 A 股公司级 Wiki 语义。A 股使用 `data/wiki/companies/<stock_code>-<company>/reports/<report_id>/`；日本市场使用 `data/wiki/jp/companies/<ticker>-<company>/reports/<report_id>/`。JP manifest 必须保留 `company_wiki_path`、`wiki_report_path`、`company_wiki_id` 和 `report_id`，PostgreSQL 的 JP importer 以 `wiki_report_path` 作为 parse run 的知识库定位入口。`data/wiki/jp_reports/` 只作历史兼容或迁移来源。

## 在系统中的位置

```text
parser / market package 产物
  -> db/imports
     -> PostgreSQL schemas / views / fact tables
     -> API / Agent / SQL query / 回归分析
```

这里的关键价值是：把“文件存在磁盘上”升级为“事实可按结构查询且仍保留证据回溯坐标”。

## 核心内容

| 文件 | 作用 |
| --- | --- |
| `import_document_full_to_postgres.py` | 导入 PDF parser 的 `document_full.json` |
| `import_document_parse_package_to_postgres.py` | 导入通用文档解析 package |
| `import_hk_evidence_package_to_postgres.py` | 导入港股 package |
| `import_jp_evidence_package_to_postgres.py` | 导入日股 package |
| `import_kr_evidence_package_to_postgres.py` | 导入韩股 package |
| `import_eu_evidence_package_to_postgres.py` | 导入欧股 package |
| `import_market_xbrl_package_to_postgres.py` | 导入多市场 XBRL package |
| `import_sec_filing_to_postgres.py` | 导入美股 SEC package |
| `analyze_market_document_full_duplicates.py` | 只读分析非 A 市场同一 filing 下的历史重复 parse runs |
| `cleanup_market_document_full_parse_runs.py` | 显式清理非 A 市场 obsolete parse runs，默认 dry-run，拒绝 CN/A 股 |
| `financial_query_api.py` | 只读财务查询 API |
| `stock_name_to_code.py` | 名称与代码映射辅助 |

## 当前最新状态

| 方向 | 状态 | 说明 |
| --- | --- | --- |
| 多市场 schema | `sec_us`、`pdf2md_hk`、`edinet_jp`、`dart_kr`、`eu_ifrs` 等市场隔离 | 避免不同市场公司、报告和指标口径混写 |
| Evidence package 导入 | HK / US / JP / KR / EU 均有专门 importer | 保留各市场 package path、company identity 和 evidence 坐标 |
| 质量门禁配合 | importer 本身负责写入，是否允许执行由 API / package gate 控制 | warning/fail package 默认不应绕过控制面直接污染数据库 |
| 只读查询 | `financial_query_api.py` 提供财务查询辅助 | 给 Agent 和调试场景提供结构化事实入口 |
| MVP 支撑 | HK package import 是二级市场商业 MVP 的关键动作 | 成功入库必须建立在 package quality 可接受或显式 force override 之上 |

PostgreSQL 层的商业价值是把文件型证据资产变成可查询、可聚合、可复盘的数据资产，同时不丢失原始 evidence package 的审计坐标。

## 典型用法

### 导入 `document_full.json`

```bash
cd /home/maoyd/siq-research-engine
python3 db/imports/import_document_full_to_postgres.py --ddl
python3 db/imports/import_document_full_to_postgres.py data/pdf-parser/results --recursive
```

### 导入单个通用文档或市场 package

```bash
cd /home/maoyd/siq-research-engine
python3 db/imports/import_document_parse_package_to_postgres.py /path/to/package
python3 db/imports/import_eu_evidence_package_to_postgres.py --package /path/to/eu/package --ddl
```

### 启动只读查询 API

```bash
cd /home/maoyd/siq-research-engine
uvicorn db.imports.financial_query_api:app --host 0.0.0.0 --port 18188
```

### 非 A 市场重复入库治理

先只读分析，再 dry-run，最后才 `--apply`。这些工具只允许 HK/JP/KR/EU/US；A 股/CN 使用旧 `siq.pdf2md` 维护工具，不走这里。

```bash
cd /home/maoyd/siq-research-engine
python3 db/imports/analyze_market_document_full_duplicates.py --market HK --json
python3 db/imports/cleanup_market_document_full_parse_runs.py --market HK --parse-run-id <obsolete_parse_run_id>
python3 db/imports/cleanup_market_document_full_parse_runs.py --market HK --parse-run-id <obsolete_parse_run_id> --apply
```

如果要按时间清理，必须优先加 `--company-id`、`--filing-id` 或具体 `--parse-run-id`。整市场 `--older-than` 需要额外传 `--allow-market-wide-older-than`，只应在审完 dry-run 计数后使用。

## 关键边界或治理规则

- importer 负责“结构化写入”，不负责重新解释业务事实。
- 数据库是结构化索引与查询层，不取代 Wiki / package 作为原始证据层。
- 市场隔离必须清晰，schema、company identity、package path 和 report id 不可混用。
- 数据库口令和连接串只通过环境变量提供，不写入脚本或 README。
- 幂等、limit、递归处理和只读辅助能力应优先于一次性脚本式导入。
- 非 A 市场历史重复入库清理必须先 analyze，再 cleanup dry-run；不要用清理工具触碰 A 股/CN 数据。

## 维护建议

- 新增字段时同步更新 DDL、importer、README 和消费侧查询逻辑。
- 市场 package 路径变更时，要检查 importer 是否仍能正确恢复 company / report identity。
- 需要做 smoke test 时优先使用 `--limit` 或单 package 导入。
- 对 Agent 消费重要的表或 view，应保留足够 evidence 坐标字段。

## 技术创新、难点与商业价值

数据库导入层承担最后一道事实防线。它不会因为 JSON 可解析就认为数据可入库，而是联合检查 package 质量、持久化目标、写入计数、来源身份和重复运行结果。

| 能力 | 技术难点 | 商业价值 |
| --- | --- | --- |
| 质量门禁 | 汇总 parser、rules、hash、coverage 与强制覆盖语义 | 阻止低质量材料静默污染生产数据 |
| 市场隔离 | 不同 schema、键空间、报告身份和符号口径 | 避免跨市场同名科目被错误合并 |
| 幂等与可对账 | stable key、upsert、expected/persisted/affected row 验证 | 支持批量重跑、失败恢复和审计对账 |
| 持久化后校验 | 事务完成后确认关键实体真实落库 | 避免“命令成功但业务数据缺失”的假成功 |

这使 PostgreSQL 从被动存储升级为受治理的研究事实账本，适合机构批处理、数据交付和后续 BI/API 消费。
