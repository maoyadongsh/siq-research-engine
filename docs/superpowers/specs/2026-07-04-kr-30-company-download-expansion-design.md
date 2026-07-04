# KR 30 Company Annual Report Download Expansion Design

Date: 2026-07-04

Repository: `/home/maoyd/siq-research-engine`

## Goal

Expand the Korean market annual-report download set from 10 companies to 30 companies, focused on mainstream listed Korean companies across different industries.

The target state is:

- Keep the existing 10 downloaded 2025 KR annual-report PDFs.
- Add 20 additional mainstream Korean listed companies.
- Download each additional company's 2025 annual report PDF from public DART where possible.
- Enqueue successfully downloaded PDFs into the existing PDF parser queue when the parser service is reachable.
- Leave a manifest that records selected companies, industry labels, download paths, parser task IDs, skips, and errors.

This work expands the download and parser-input dataset only. It does not claim the KR evidence-package, PostgreSQL import, Milvus rebuild, or evidence-click workflow is complete.

## Current State

The KR finder currently has a curated catalog of 10 companies in:

`services/market-report-finder/src/market_report_finder_service/markets/kr/catalog.py`

Those 10 companies already have 2025 annual-report PDFs in:

`data/market-report-finder/downloads/KR`

The 10 existing companies are:

| Ticker | Company | Industry |
| --- | --- | --- |
| 005930 | Samsung Electronics Co., Ltd. | Semiconductors / Electronics |
| 000660 | SK hynix Inc. | Semiconductors |
| 035420 | NAVER Corporation | Internet Services |
| 005380 | Hyundai Motor Company | Automotive |
| 003490 | Korean Air Lines Co., Ltd. | Airlines |
| 005490 | POSCO Holdings Inc. | Steel |
| 051910 | LG Chem, Ltd. | Chemicals / Battery Materials |
| 055550 | Shinhan Financial Group Co., Ltd. | Banking |
| 068270 | Celltrion, Inc. | Biopharmaceuticals |
| 017670 | SK Telecom Co., Ltd. | Telecommunications |

`DartPublicClient` can locate DART public annual-report filings and transform them into downloadable PDF candidates without requiring `DART_API_KEY`.

## Proposed Additional 20 Companies

The additional companies intentionally broaden industry coverage beyond the first 10.

| Ticker | Company | Industry |
| --- | --- | --- |
| 000270 | Kia Corporation | Automotive |
| 012330 | Hyundai Mobis Co., Ltd. | Auto Parts |
| 373220 | LG Energy Solution, Ltd. | Batteries |
| 006400 | Samsung SDI Co., Ltd. | Batteries / Electronic Materials |
| 207940 | Samsung Biologics Co., Ltd. | Biopharmaceuticals |
| 066570 | LG Electronics Inc. | Consumer Electronics |
| 105560 | KB Financial Group Inc. | Banking |
| 086790 | Hana Financial Group Inc. | Banking |
| 032830 | Samsung Life Insurance Co., Ltd. | Insurance |
| 000810 | Samsung Fire & Marine Insurance Co., Ltd. | Insurance |
| 015760 | Korea Electric Power Corporation | Utilities |
| 036460 | Korea Gas Corporation | Utilities / Gas |
| 329180 | HD Hyundai Heavy Industries Co., Ltd. | Shipbuilding |
| 012450 | Hanwha Aerospace Co., Ltd. | Aerospace / Defense |
| 034020 | Doosan Enerbility Co., Ltd. | Power Equipment |
| 035720 | Kakao Corp. | Internet Platforms |
| 259960 | Krafton, Inc. | Gaming |
| 090430 | Amorepacific Corporation | Consumer / Beauty |
| 023530 | Lotte Shopping Co., Ltd. | Retail |
| 097950 | CJ CheilJedang Corporation | Food / Consumer Staples |

## Architecture

### Catalog Expansion

Extend `KR_ANNUAL_REPORT_CATALOG` from 10 to 30 entries, preserving the existing `KrAnnualReportCatalogEntry` shape and matching behavior.

Each new entry should include:

- `industry`
- `ticker`
- `company_name`
- useful aliases in English, Korean, and Chinese where practical
- `company_id` / DART corp code only when verified

The download path should use ticker-based DART public search as the reliable baseline because `DartPublicClient` does not require `DART_API_KEY`. If a corp code is not confidently verified, do not fabricate it. The batch manifest may record a missing `corp_code`; later evidence-package work can enrich it from OpenDART corp-code data when `DART_API_KEY` is available.

### Batch Download Helper

Add `scripts/ops/download_kr_2025_annuals_to_parse_queue.py`, following the existing HK helper pattern.

Responsibilities:

- Build a candidate pool from the KR catalog.
- Default to target count 30.
- Skip companies that already have a 2025 KR annual-report PDF under the download directory.
- Query public DART for the selected company's 2025 annual report.
- Download the selected PDF with `ReportDownloader`.
- Optionally enqueue the PDF into `apps/pdf-parser` via `/api/upload`.
- Write a JSON manifest incrementally so partial runs are recoverable.

Suggested manifest path:

`data/market-report-finder/kr_2025_annual_download_queue_manifest.json`

### Data Flow

```text
KR catalog seed
  -> KrReportFinder / DartPublicClient
  -> DART public annual-report search
  -> DART PDF download URL
  -> ReportDownloader
  -> data/market-report-finder/downloads/KR/<company>/2025/年报/
  -> optional PDF parser enqueue
  -> data/pdf-parser/results/<task_id>/
  -> manifest with success/skip/error rows
```

## Error Handling

The batch helper should distinguish:

- `already_downloaded`: a matching PDF already exists locally.
- `not_found`: DART public search did not return a matching 2025 annual report.
- `download_failed`: DART returned a candidate but the PDF download failed.
- `already_in_queue`: PDF parser already knows this filename.
- `queued`: upload succeeded and returned a parser task ID.
- `upload_failed`: PDF downloaded but parser enqueue failed.

The script should continue on individual company failures and summarize totals at the end.

## Testing And Verification

Implementation should verify:

- KR finder tests still pass.
- The expanded catalog can resolve all 30 companies by ticker.
- The batch helper supports `--download-only`, `--target-count`, `--code`, `--codes`, and `--skip-code`.
- Running the helper in `--download-only` mode downloads missing PDFs without duplicating the existing 10.
- If the parser service is reachable, successfully downloaded PDFs are queued or reported as already queued.
- Final filesystem count under `data/market-report-finder/downloads/KR` reaches 30 company directories and 30 2025 annual-report PDFs, unless DART does not expose one of the selected filings. Any shortfall must be explicit in the manifest.

Suggested commands:

```bash
cd /home/maoyd/siq-research-engine/services/market-report-finder
uv run pytest tests/test_dart_client.py tests/test_downloader.py

cd /home/maoyd/siq-research-engine
python3 scripts/ops/download_kr_2025_annuals_to_parse_queue.py --target-count 30 --report-year 2025 --download-only
find data/market-report-finder/downloads/KR -type f -name '*.pdf' | wc -l
```

## Non-Goals

- Do not modify A-share parser behavior or CN schemas.
- Do not generate KR wiki evidence packages in this step.
- Do not import KR facts into PostgreSQL in this step.
- Do not rebuild Milvus collections in this step.
- Do not use LLMs to invent financial facts or fill missing DART data.
