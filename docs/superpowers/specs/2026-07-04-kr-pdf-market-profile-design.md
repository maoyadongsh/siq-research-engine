# KR PDF Market Profile Design

## Goal

Add a Korean-market profile for PDF parser quality reports, financial artifacts, and financial checks so KR annual reports stop using A-share section names, table names, and missing-statement warnings.

## Evidence

Ten completed KR parser results were sampled locally. All contained Korean DART annual-report signals such as `재무에 관한 사항`, `연결 재무상태표`, `연결 손익계산서`, `연결 포괄손익계산서`, `연결 현금흐름표`, and `연결 자본변동표`.

## Scope

- Add `apps/pdf-parser/kr_market_profile.py`.
- Wire KR profile into quality report construction next to the existing JP profile.
- Wire KR market handling into financial artifact generation and checks.
- Keep parsing/extraction transport unchanged. This change interprets parser outputs; it does not change MinerU/OCR behavior.
- Existing KR task artifacts can be regenerated after code is deployed.

## Quality Report Rules

KR key sections:

- `회사의 개요`
- `사업의 내용`
- `이사의 경영진단 및 분석의견`
- `재무에 관한 사항`
- `감사인의 감사의견`
- `임원 및 직원 등에 관한 사항`
- `계열회사 등에 관한 사항`

KR core financial table candidates:

- `요약재무정보`
- `Consolidated Statement of Financial Position` / `연결 재무상태표`
- `Consolidated Statement of Profit or Loss` / `연결 손익계산서`
- `Consolidated Statement of Comprehensive Income` / `연결 포괄손익계산서`
- `Consolidated Statement of Cash Flows` / `연결 현금흐름표`
- `Consolidated Statement of Changes in Equity` / `연결 자본변동표`

Candidate scoring should use headings, captions, previews, near text, and compacted Korean text. Negative filters should avoid note tables whose preview only references `연결재무상태표상` without being a primary statement.

## Financial Rules

KR V1 should be conservative:

- Financial data must carry `market: KR` and a KR report kind.
- Financial checks must never emit A-share warnings such as missing `合并资产负债表`, `合并利润表`, or `合并现金流量表`.
- If no structured KR statements are extracted, return `overall_status: skipped` with KR-specific warnings explaining that core statements were not extracted from structured tables and that DART/XBRL or visual review may be required.
- If structured statement extraction is later added, enable numeric checks such as `total assets = total liabilities + total equity` and cash-flow opening/ending cash checks.

## Testing

- Unit-test market detection for explicit `KR`, filename `_KR_`, and DART public filenames.
- Unit-test KR quality candidates from Korean table previews.
- Unit-test KR quality warnings do not mention A-share core tables.
- Unit-test financial artifact generation passes `market=KR` and produces KR checks.
- Replay one completed KR task to confirm `quality_report.json`, `financial_data.json`, and `financial_checks.json` use KR labels.
