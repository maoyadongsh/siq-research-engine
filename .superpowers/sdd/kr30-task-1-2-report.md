status: DONE
commits:
  - 1c25b9bced15dd9223556bbb6fe3a5481f0c283e
files_changed:
  - services/market-report-finder/src/market_report_finder_service/markets/kr/catalog.py
  - services/market-report-finder/tests/test_kr_catalog.py
tests_run:
  - command: cd /home/maoyd/siq-research-engine/services/market-report-finder && uv run pytest tests/test_kr_catalog.py -q
    output: "4 passed in 0.10s"
  - command: cd /home/maoyd/siq-research-engine/services/market-report-finder && uv run pytest tests/test_dart_client.py -q
    output: "8 passed in 0.07s"
self_review_notes:
  - Kept the original first 10 KR catalog entries in order and appended the 20 brief-listed tickers to reach exactly 30 entries.
  - Filtered empty aliases in company_entity() so blank company_id values do not leak into CompanyEntity.aliases.
  - Left downloaded data, CN/A-share behavior, and unrelated files untouched.
concerns:
  - The new 20 entries intentionally use empty company_id values because the brief asked not to fabricate unverified DART corp codes.
