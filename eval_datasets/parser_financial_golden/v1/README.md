# 解析器财务 Golden 数据集

本 manifest 用于把大型真实财报样本保留在 Git checkout 之外，同时版本化它们的 hash 和预期 parser 输出。

PR 合同检查：

```bash
python3 scripts/maintenance/run_parser_financial_golden_gate.py --mode contract
```

离线/自托管样本检查：

```bash
python3 scripts/maintenance/run_parser_financial_golden_gate.py \
  --mode offline-samples \
  --sample-root /path/to/financial-markdown-samples
```

`SIQ_FINANCIAL_GOLDEN_SAMPLE_ROOT` 可以提供样本根目录。报告默认写入被忽略的 `artifacts/eval-runs/parser-financial-golden/`。

真实 PDF 身份与 parser/MinerU 就绪预检查：

```bash
python3 scripts/maintenance/run_parser_financial_pdf_release_gate.py \
  --mode preflight \
  --pdf-root data/market-report-finder/downloads
```

自托管端到端发布门禁（408 页样本可能运行数小时）：

```bash
python3 scripts/maintenance/run_parser_financial_pdf_release_gate.py \
  --mode live-http \
  --pdf-root data/market-report-finder/downloads \
  --parser-url http://127.0.0.1:15000 \
  --deadline-seconds 10800
```

`live-http` 会校验版本化源 PDF hash 和页数，要求真实 parser 报告 MinerU 就绪，上传 PDF，等待任务完成，获取新生成的 Markdown，并运行财务 golden 断言。它会记录新 Markdown hash，但不要求与基线逐字节相等，因为 MinerU 输出可能随运行版本变化。

自托管发布包装器默认关闭该外层门禁。需要时显式启用：

```bash
SIQ_PARSER_FINANCIAL_PDF_GATE_MODE=live-http \
SIQ_PARSER_FINANCIAL_PDF_GATE_REQUIRED=1 \
SIQ_FINANCIAL_GOLDEN_PDF_ROOT=/read-only/market-report-downloads \
SIQ_PDF_PARSER_URL=http://127.0.0.1:15000 \
bash scripts/ops/run_market_postgres_release_gate.sh --mode offline-postgres
```

可接受模式是 `off`、`preflight` 和 `live-http`。可选门禁失败时会保留 BLOCKED 报告，但不改变包装器退出码。设置 `SIQ_PARSER_FINANCIAL_PDF_GATE_REQUIRED=1` 后，缺少显式模式或任何 BLOCKED 结果都会让发布包装器失败。
