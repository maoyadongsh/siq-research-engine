# 多市场 Document Full PostgreSQL 回测用例

本目录保存多市场 PostgreSQL 设计使用的微型 `document_full.json` 示例。

非 A 股 PostgreSQL 门禁覆盖 **HK、JP、KR、EU 和 US**。CN/A 股 fixture 只能作为 legacy A 股路径的小型行结构合同示例保留在这里；CN/A 股导入和 PostgreSQL DB 门禁不属于非 A 多市场门禁。

这些 fixture 有意保持很小。`cases.json` 采用架构文档中的断言风格（`market`、`company_id`、`report_year`、`period_key`、`assertions[].expected_value`、`required_evidence`），并增加 fixture path、单位/币种/evidence 检查。示例覆盖 importer family 在写入市场 schema 前必须保留的行形态：

- EU period-map 报表项，包含 `values`、`raw_values` 和 `sources`。
- HK/JP/KR row-per-period 报表项，包含 `value`、`raw_value` 和 `evidence`。
- US SEC HTML/iXBRL facts，包含 `concept`、`context_ref`、`unit` 和 `html_anchor`。
- CN period-map fixture 仅是 legacy/A 股合同 fixture，不计入非 A 股 PostgreSQL 门禁覆盖。

运行：

```bash
python3 db/imports/backtests/market_document_full_postgres_backtest.py
```

默认 runner 写入：

- `artifacts/eval-runs/local/market_document_full_postgres_backtest.json`
- `artifacts/eval-runs/local/market_document_full_postgres_backtest.md`

完整 JSON/Markdown 门禁输出应保留在被忽略的 artifact 目录中，例如 `artifacts/eval-runs/local/` 或 `artifacts/eval-runs/ci/`。本目录已跟踪的 `backtest_report.json` 只是小型脱敏摘要，不要用完整门禁结果覆盖它。

当前模式是 fixture contract 回测加真实样本 manifest 预检查：它会验证 `document_full.json` 身份、数值、单位/币种、证据形态、固定 fact lookup，并确认每个非 CN 市场在 manifest 中至少列出三个真实 `document_full.json` 样本。合同模式只检查 manifest 结构，不要求文件实际存在。

运行态 PostgreSQL DDL 权威来源是已提交的 `db/ddl/*.sql`。`db/imports/market_ingestion_contract.py` 生成的 reset DDL 包含 `DROP SCHEMA CASCADE`，只用于合同/dry-run 检查或测试中的显式不安全 reset 调用。

严格生产门禁需要显式开启，因为它会写 PostgreSQL，并从仓库 checkout 外读取真实样本。将 `SIQ_MARKET_POSTGRES_SAMPLE_ROOT` 配置为替换 manifest 路径前缀 `data/` 的根目录。例如 manifest 路径：

```text
data/pdf-parser/results/<task_id>/document_full.json
```

会解析为：

```text
$SIQ_MARKET_POSTGRES_SAMPLE_ROOT/pdf-parser/results/<task_id>/document_full.json
```

可通过环境变量或等价显式选项运行可移植门禁入口：

```bash
python3 scripts/maintenance/run_market_document_full_postgres_gate.py \
  --mode offline-postgres \
  --production-sample-root /srv/siq-market-postgres-samples
```

门禁会拒绝 checkout 内部根目录，并在打开 PostgreSQL 连接前列出所有缺失样本。底层 runner 也支持同一环境变量：

```bash
SIQ_MARKET_POSTGRES_SAMPLE_ROOT=/srv/siq-market-postgres-samples \
python3 db/imports/backtests/market_document_full_postgres_backtest.py \
  --db --import-before-db-check --idempotency \
  --production-sample-db --production-agent-query
```

该模式会导入微型 fixtures，导入 `production_sample_manifest.json` 中所有真实样本并允许同市场样本共存，重复每次导入以验证幂等性，检查 table-family/evidence 计数，通过各市场 `v_agent_financial_facts` view 验证固定 Agent 问题，并探测每个真实样本导入后的 `parse_run_id` 是否具备 Agent-view facts、values 和可审阅 evidence。DB 导入模式还会比较源 `document_full`/Wiki package facts 与 PostgreSQL Agent-view rows 的同类指标，使数值、单位/币种和 evidence 漂移能在报告中显式呈现；自动生成的真实样本比较会为当前规则尚未暴露到 Agent view 的源 facts 记录非阻断 parity warning。
