Status: DONE_WITH_CONCERNS

Commits:
- `531c0ec` `feat(kr): add annual report batch download helper`

Files changed:
- `scripts/ops/download_kr_2025_annuals_to_parse_queue.py`
- `scripts/ops/tests/test_download_kr_2025_annuals_to_parse_queue.py`

Tests run with outputs:
1. Red step using the repo venv because host `python3` lacks `pytest`:
   - Command:
     `cd /home/maoyd/siq-research-engine && . services/market-report-finder/.venv/bin/activate && python3 -m pytest scripts/ops/tests/test_download_kr_2025_annuals_to_parse_queue.py -q`
   - Output:
     `ERROR scripts/ops/tests/test_download_kr_2025_annuals_to_parse_queue.py - FileNotFoundError: [Errno 2] No such file or directory: '/home/maoyd/siq-research-engine/scripts/ops/download_kr_2025_annuals_to_parse_queue.py'`
2. Owned helper tests after implementation:
   - Command:
     `cd /home/maoyd/siq-research-engine && . services/market-report-finder/.venv/bin/activate && python3 -m pytest scripts/ops/tests/test_download_kr_2025_annuals_to_parse_queue.py -q`
   - Output:
     `.....                                                                    [100%]`
     `5 passed in 0.11s`
3. Requested market-report-finder regression set, run via the existing venv interpreter because `uv` is not installed on the host:
   - Command:
     `cd /home/maoyd/siq-research-engine/services/market-report-finder && . .venv/bin/activate && python3 -m pytest tests/test_kr_catalog.py tests/test_dart_client.py tests/test_downloader.py -q`
   - Output:
     `.................                                                        [100%]`
     `17 passed in 0.16s`

Self-review notes:
- The helper follows the existing HK operational script structure to minimize surprise.
- KR company selection stays ticker-first through `KrAnnualReportCatalog.resolve_company(...)` and `DartPublicClient.list_filings(...)`.
- The script does not require `DART_API_KEY` and does not fabricate corp codes for manual/blank-corp catalog entries.
- Existing downloaded PDFs are detected and reused before any network lookup or download.
- The change is scoped to the two owned files; no CN/A-share or unrelated repo behavior was modified.

Concerns:
- The exact brief command `python3 -m pytest scripts/ops/tests/test_download_kr_2025_annuals_to_parse_queue.py -q` fails on this host unless the `services/market-report-finder/.venv` environment is activated first, because system `python3` does not have `pytest`.
- The exact brief command `uv run pytest tests/test_kr_catalog.py tests/test_dart_client.py tests/test_downloader.py -q` cannot run on this host because `uv` is not installed in PATH or the project venv; the equivalent `python3 -m pytest` invocation inside the existing venv passed.


## Fix review

Files changed:
- `scripts/ops/download_kr_2025_annuals_to_parse_queue.py`
- `scripts/ops/tests/test_download_kr_2025_annuals_to_parse_queue.py`

Commit:
- `8c615c9` `fix(kr): handle skipped annual download targets`

Tests with outputs:
1. `cd /home/maoyd/siq-research-engine && . services/market-report-finder/.venv/bin/activate && python3 -m pytest scripts/ops/tests/test_download_kr_2025_annuals_to_parse_queue.py -q`
   - Output: `8 passed in 0.09s`
2. `cd /home/maoyd/siq-research-engine/services/market-report-finder && . .venv/bin/activate && python3 -m pytest tests/test_kr_catalog.py tests/test_dart_client.py tests/test_downloader.py -q`
   - Output: `17 passed in 0.12s`

Concerns:
- None beyond the existing repo-wide uncommitted changes outside the owned files.
