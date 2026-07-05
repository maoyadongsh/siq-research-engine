# SIQ PostgreSQL 入库工具

## 目录职责

`db/imports` 保存把 `document_full.json`、通用文档 package 和多市场 evidence package 写入 PostgreSQL 的导入工具，以及少量只读查询辅助入口。它负责把文件型证据层转换成结构化事实层，供 Agent、查询工具和回归分析复用。

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
| `financial_query_api.py` | 只读财务查询 API |
| `stock_name_to_code.py` | 名称与代码映射辅助 |

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

## 关键边界或治理规则

- importer 负责“结构化写入”，不负责重新解释业务事实。
- 数据库是结构化索引与查询层，不取代 Wiki / package 作为原始证据层。
- 市场隔离必须清晰，schema、company identity、package path 和 report id 不可混用。
- 数据库口令和连接串只通过环境变量提供，不写入脚本或 README。
- 幂等、limit、递归处理和只读辅助能力应优先于一次性脚本式导入。

## 维护建议

- 新增字段时同步更新 DDL、importer、README 和消费侧查询逻辑。
- 市场 package 路径变更时，要检查 importer 是否仍能正确恢复 company / report identity。
- 需要做 smoke test 时优先使用 `--limit` 或单 package 导入。
- 对 Agent 消费重要的表或 view，应保留足够 evidence 坐标字段。
