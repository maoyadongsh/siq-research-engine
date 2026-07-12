# Parser Financial Golden Dataset

This manifest keeps large, real financial report samples outside the Git checkout while versioning their hashes and expected parser outputs.

PR contract check:

```bash
python3 scripts/maintenance/run_parser_financial_golden_gate.py --mode contract
```

Offline/self-hosted sample check:

```bash
python3 scripts/maintenance/run_parser_financial_golden_gate.py \
  --mode offline-samples \
  --sample-root /path/to/financial-markdown-samples
```

`SIQ_FINANCIAL_GOLDEN_SAMPLE_ROOT` can supply the sample root. Reports are written under ignored `artifacts/eval-runs/parser-financial-golden/` by default.

Real PDF identity and parser/MinerU readiness preflight:

```bash
python3 scripts/maintenance/run_parser_financial_pdf_release_gate.py \
  --mode preflight \
  --pdf-root data/market-report-finder/downloads
```

Self-hosted end-to-end release gate (the 408-page sample can take hours):

```bash
python3 scripts/maintenance/run_parser_financial_pdf_release_gate.py \
  --mode live-http \
  --pdf-root data/market-report-finder/downloads \
  --parser-url http://127.0.0.1:15000 \
  --deadline-seconds 10800
```

`live-http` verifies the versioned source PDF hash and page count, requires the
real parser to report MinerU readiness, uploads the PDF, waits for a completed
task, fetches fresh Markdown, and runs the financial golden assertions. It
records the fresh Markdown hash but does not require byte-for-byte equality
with the baseline because MinerU output can change across runtime versions.

The self-hosted release wrapper defaults this outer gate to `off`. Enable it
explicitly with:

```bash
SIQ_PARSER_FINANCIAL_PDF_GATE_MODE=live-http \
SIQ_PARSER_FINANCIAL_PDF_GATE_REQUIRED=1 \
SIQ_FINANCIAL_GOLDEN_PDF_ROOT=/read-only/market-report-downloads \
SIQ_PDF_PARSER_URL=http://127.0.0.1:15000 \
bash scripts/ops/run_market_postgres_release_gate.sh --mode offline-postgres
```

Accepted modes are `off`, `preflight`, and `live-http`. A failed optional gate
keeps its BLOCKED report without changing the wrapper exit code. With
`SIQ_PARSER_FINANCIAL_PDF_GATE_REQUIRED=1`, a missing explicit mode or any
BLOCKED result fails the release wrapper.
