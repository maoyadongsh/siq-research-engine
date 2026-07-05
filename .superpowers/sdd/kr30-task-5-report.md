# KR 30 Task 5 Operational Verification Report

Status: DONE_WITH_CONCERNS

## Summary
- Download-only run completed successfully with exit code `0`.
- Manifest recorded `30` successful items for the 30-company target.
- On-disk verification found `30` KR annual-report PDFs and `30` KR company directories.
- Parser enqueue run completed successfully with exit code `0`.
- Enqueue manifest recorded `10` `already_in_queue` items and `20` newly `queued` items, with `20` task IDs present.
- No code changes or commits were required.
- Concern: the requested verification tests could not run on `spark-1319` because `uv` is not installed and `/usr/bin/python3` does not have `pytest` installed.

## Commands Run With Outputs

### 1. Download-only run
Command:
```bash
cd /home/maoyd/siq-research-engine && python3 scripts/ops/download_kr_2025_annuals_to_parse_queue.py --target-count 30 --report-year 2025 --download-only
```
Exit code: `0`
Output:
```text
[0/30] 005930 Samsung Electronics Co., Ltd.
[1/30] 000660 SK hynix Inc.
[2/30] 035420 NAVER Corporation
[3/30] 005380 Hyundai Motor Company
[4/30] 003490 Korean Air Lines Co., Ltd.
[5/30] 005490 POSCO Holdings Inc.
[6/30] 051910 LG Chem, Ltd.
[7/30] 055550 Shinhan Financial Group Co., Ltd.
[8/30] 068270 Celltrion, Inc.
[9/30] 017670 SK Telecom Co., Ltd.
[10/30] 000270 Kia Corporation
[11/30] 012330 Hyundai Mobis Co., Ltd.
[12/30] 373220 LG Energy Solution, Ltd.
[13/30] 006400 Samsung SDI Co., Ltd.
[14/30] 207940 Samsung Biologics Co., Ltd.
[15/30] 066570 LG Electronics Inc.
[16/30] 105560 KB Financial Group Inc.
[17/30] 086790 Hana Financial Group Inc.
[18/30] 032830 Samsung Life Insurance Co., Ltd.
[19/30] 000810 Samsung Fire & Marine Insurance Co., Ltd.
[20/30] 015760 Korea Electric Power Corporation
[21/30] 036460 Korea Gas Corporation
[22/30] 329180 HD Hyundai Heavy Industries Co., Ltd.
[23/30] 012450 Hanwha Aerospace Co., Ltd.
[24/30] 034020 Doosan Enerbility Co., Ltd.
[25/30] 035720 Kakao Corp.
[26/30] 259960 Krafton, Inc.
[27/30] 090430 Amorepacific Corporation
[28/30] 023530 Lotte Shopping Co., Ltd.
[29/30] 097950 CJ CheilJedang Corporation
{
  "downloaded_or_existing_count": 30,
  "manifest": "/home/maoyd/siq-research-engine/data/market-report-finder/kr_2025_annual_download_queue_manifest.json"
}
```

### 2. Downloaded PDF and company-directory counts
Command:
```bash
cd /home/maoyd/siq-research-engine && find data/market-report-finder/downloads/KR -type f -name '*.pdf' | wc -l && find data/market-report-finder/downloads/KR -mindepth 1 -maxdepth 1 -type d | wc -l
```
Exit code: `0`
Output:
```text
30
30
```

### 3. Manifest summary after download-only run
Command:
```bash
cd /home/maoyd/siq-research-engine && python3 -c "import json; from collections import Counter; from pathlib import Path; data=json.loads(Path(\"data/market-report-finder/kr_2025_annual_download_queue_manifest.json\").read_text(encoding=\"utf-8\")); print(\"items\", dict(Counter(item.get(\"status\", \"unknown\") for item in data.get(\"items\", [])))); print(\"skipped\", dict(Counter(item.get(\"status\", \"unknown\") for item in data.get(\"skipped\", [])))); [print(item.get(\"seed\", {}).get(\"ticker\"), item.get(\"seed\", {}).get(\"name\"), item.get(\"status\"), item.get(\"reason\")) for item in data.get(\"skipped\", [])]"
```
Exit code: `0`
Output:
```text
items {'already_downloaded': 10, 'downloaded': 20}
skipped {}
```

### 4. Parser token availability check
Command:
```bash
cd /home/maoyd/siq-research-engine && python3 - <<'PY'
import os
from scripts.ops.download_kr_2025_annuals_to_parse_queue import _resolve_pdf_token
try:
    token = _resolve_pdf_token(os.environ.get('SIQ_PDF2MD_API_BASE', 'http://127.0.0.1:15000'))
    print('TOKEN_OK', len(token))
except Exception as exc:
    print('TOKEN_ERR', repr(exc))
PY
```
Exit code: `0`
Output:
```text
TOKEN_OK 43
```

### 5. Download + parser enqueue run
Command:
```bash
cd /home/maoyd/siq-research-engine && python3 scripts/ops/download_kr_2025_annuals_to_parse_queue.py --target-count 30 --report-year 2025
```
Exit code: `0`
Output:
```text
[0/30] 005930 Samsung Electronics Co., Ltd.
[1/30] 000660 SK hynix Inc.
[2/30] 035420 NAVER Corporation
[3/30] 005380 Hyundai Motor Company
[4/30] 003490 Korean Air Lines Co., Ltd.
[5/30] 005490 POSCO Holdings Inc.
[6/30] 051910 LG Chem, Ltd.
[7/30] 055550 Shinhan Financial Group Co., Ltd.
[8/30] 068270 Celltrion, Inc.
[9/30] 017670 SK Telecom Co., Ltd.
[10/30] 000270 Kia Corporation
[11/30] 012330 Hyundai Mobis Co., Ltd.
[12/30] 373220 LG Energy Solution, Ltd.
[13/30] 006400 Samsung SDI Co., Ltd.
[14/30] 207940 Samsung Biologics Co., Ltd.
[15/30] 066570 LG Electronics Inc.
[16/30] 105560 KB Financial Group Inc.
[17/30] 086790 Hana Financial Group Inc.
[18/30] 032830 Samsung Life Insurance Co., Ltd.
[19/30] 000810 Samsung Fire & Marine Insurance Co., Ltd.
[20/30] 015760 Korea Electric Power Corporation
[21/30] 036460 Korea Gas Corporation
[22/30] 329180 HD Hyundai Heavy Industries Co., Ltd.
[23/30] 012450 Hanwha Aerospace Co., Ltd.
[24/30] 034020 Doosan Enerbility Co., Ltd.
[25/30] 035720 Kakao Corp.
[26/30] 259960 Krafton, Inc.
[27/30] 090430 Amorepacific Corporation
[28/30] 023530 Lotte Shopping Co., Ltd.
[29/30] 097950 CJ CheilJedang Corporation
{
  "downloaded_or_existing_count": 30,
  "manifest": "/home/maoyd/siq-research-engine/data/market-report-finder/kr_2025_annual_download_queue_manifest.json"
}
```

### 6. Manifest summary after enqueue run
Command:
```bash
cd /home/maoyd/siq-research-engine && python3 -c "import json; from collections import Counter; from pathlib import Path; data=json.loads(Path(\"data/market-report-finder/kr_2025_annual_download_queue_manifest.json\").read_text(encoding=\"utf-8\")); print(\"items\", dict(Counter(item.get(\"status\", \"unknown\") for item in data.get(\"items\", [])))); print(\"skipped\", dict(Counter(item.get(\"status\", \"unknown\") for item in data.get(\"skipped\", [])))); print(\"task_ids\", sum(1 for item in data.get(\"items\", []) if item.get(\"task_id\"))); [print(item.get(\"seed\", {}).get(\"ticker\"), item.get(\"seed\", {}).get(\"name\"), item.get(\"status\"), item.get(\"reason\")) for item in data.get(\"skipped\", [])]"
```
Exit code: `0`
Output:
```text
items {'already_in_queue': 10, 'queued': 20}
skipped {}
task_ids 20
```

### 7. Requested test command in `services/market-report-finder`
Command:
```bash
cd /home/maoyd/siq-research-engine/services/market-report-finder && uv run pytest tests/test_kr_catalog.py tests/test_dart_client.py tests/test_downloader.py -q
```
Exit code: `127`
Output:
```text
bash: line 1: uv: command not found
```

### 8. Fallback test attempt in `services/market-report-finder`
Command:
```bash
cd /home/maoyd/siq-research-engine/services/market-report-finder && python3 -m pytest tests/test_kr_catalog.py tests/test_dart_client.py tests/test_downloader.py -q
```
Exit code: `1`
Output:
```text
/usr/bin/python3: No module named pytest
```

### 9. Requested ops test command fallback attempt
Command:
```bash
cd /home/maoyd/siq-research-engine && python3 -m pytest scripts/ops/tests/test_download_kr_2025_annuals_to_parse_queue.py -q
```
Exit code: `1`
Output:
```text
/usr/bin/python3: No module named pytest
```

## Manifest Status Counts
- After download-only run: `items {'already_downloaded': 10, 'downloaded': 20}`, `skipped {}`.
- After enqueue run: `items {'already_in_queue': 10, 'queued': 20}`, `skipped {}`.
- `items` total after final run: `30`.
- `skipped` total after final run: `0`.

## PDF And Company-Directory Counts
- PDF count under `data/market-report-finder/downloads/KR`: `30`
- Top-level KR company-directory count under `data/market-report-finder/downloads/KR`: `30`

## Parser Enqueue Status
- Parser token discovery succeeded (`TOKEN_OK 43`).
- Enqueue run succeeded with exit code `0`.
- Final manifest reports `10` `already_in_queue` items and `20` `queued` items.
- Final manifest contains `20` `task_id` values for newly queued uploads.

## Code Commits
- None.
- No code adjustments were required.

## Remaining Shortfalls
- None in the 30-company KR target set.
- No skipped companies were recorded in the manifest.

## Concerns
- The requested verification tests could not be executed on `spark-1319` because `uv` is not installed and the available `python3` environment does not include `pytest`.
- Operational verification itself succeeded, but test-suite verification remains pending on a host or environment with the required test tooling.

## Controller verification after Task 5 review

The Task 5 reviewer flagged that the brief's literal `uv` and system `python3 -m pytest` commands were unavailable on this host. The controller resolved this by running the same coverage through the repository venv that the implementation workers used:

1. `cd /home/maoyd/siq-research-engine && . services/market-report-finder/.venv/bin/activate && python3 -m pytest scripts/ops/tests/test_download_kr_2025_annuals_to_parse_queue.py -q`
   - Output: `8 passed in 0.08s`
2. `cd /home/maoyd/siq-research-engine/services/market-report-finder && . .venv/bin/activate && python3 -m pytest tests/test_kr_catalog.py tests/test_dart_client.py tests/test_downloader.py -q`
   - Output: `17 passed in 0.14s`

Controller manifest/count verification:

- Manifest: `data/market-report-finder/kr_2025_annual_download_queue_manifest.json`
- `target_count=30`
- `downloaded_or_existing_count=30`
- item statuses: `already_in_queue=10`, `queued=20`
- skipped statuses: none
- filesystem counts: `30` KR PDFs, `30` KR company directories

## Final review fix follow-up

### Code changes
- Added `_existing_tasks_by_filename(db_path)` so queue duplicate detection can carry `task_id` values from `data/pdf-parser/db/tasks.db`.
- Updated enqueue handling so `already_in_queue` items retain `task_id` when present in the DB mapping.
- Hardened upload success handling so a `2xx` response without any task ID now records `upload_failed` with reason `Upload succeeded but parser returned no task_id`.

### Added tests
- Duplicate queue entries now propagate the existing parser task ID.
- The task DB helper returns `filename -> task_id` mappings.
- `2xx` uploads without a top-level or nested `task_id` are rejected as `upload_failed`.

### Verification via repository venv
Command:
```bash
cd /home/maoyd/siq-research-engine && . services/market-report-finder/.venv/bin/activate && python3 -m pytest scripts/ops/tests/test_download_kr_2025_annuals_to_parse_queue.py -q
```
Exit code: `0`
Output:
```text
...........                                                              [100%]
11 passed in 0.14s
```

Command:
```bash
cd /home/maoyd/siq-research-engine/services/market-report-finder && . .venv/bin/activate && python3 -m pytest tests/test_kr_catalog.py tests/test_dart_client.py tests/test_downloader.py -q
```
Exit code: `0`
Output:
```text
.................                                                        [100%]
17 passed in 0.16s
```

### Commit
- `845a6c1` — `Fix KR annual queue task-id tracking`

### Runtime manifest refresh
Command:
```bash
cd /home/maoyd/siq-research-engine && . services/market-report-finder/.venv/bin/activate && python3 scripts/ops/download_kr_2025_annuals_to_parse_queue.py
```
Exit code: `0`
Output:
```text
[0/30] 005930 Samsung Electronics Co., Ltd.
[1/30] 000660 SK hynix Inc.
[2/30] 035420 NAVER Corporation
[3/30] 005380 Hyundai Motor Company
[4/30] 003490 Korean Air Lines Co., Ltd.
[5/30] 005490 POSCO Holdings Inc.
[6/30] 051910 LG Chem, Ltd.
[7/30] 055550 Shinhan Financial Group Co., Ltd.
[8/30] 068270 Celltrion, Inc.
[9/30] 017670 SK Telecom Co., Ltd.
[10/30] 000270 Kia Corporation
[11/30] 012330 Hyundai Mobis Co., Ltd.
[12/30] 373220 LG Energy Solution, Ltd.
[13/30] 006400 Samsung SDI Co., Ltd.
[14/30] 207940 Samsung Biologics Co., Ltd.
[15/30] 066570 LG Electronics Inc.
[16/30] 105560 KB Financial Group Inc.
[17/30] 086790 Hana Financial Group Inc.
[18/30] 032830 Samsung Life Insurance Co., Ltd.
[19/30] 000810 Samsung Fire & Marine Insurance Co., Ltd.
[20/30] 015760 Korea Electric Power Corporation
[21/30] 036460 Korea Gas Corporation
[22/30] 329180 HD Hyundai Heavy Industries Co., Ltd.
[23/30] 012450 Hanwha Aerospace Co., Ltd.
[24/30] 034020 Doosan Enerbility Co., Ltd.
[25/30] 035720 Kakao Corp.
[26/30] 259960 Krafton, Inc.
[27/30] 090430 Amorepacific Corporation
[28/30] 023530 Lotte Shopping Co., Ltd.
[29/30] 097950 CJ CheilJedang Corporation
{
  "downloaded_or_existing_count": 30,
  "manifest": "/home/maoyd/siq-research-engine/data/market-report-finder/kr_2025_annual_download_queue_manifest.json"
}
```

Final manifest status counts:
- `target_count=30`
- `downloaded_or_existing_count=30`
- item statuses: `already_in_queue=30`
- skipped statuses: none
