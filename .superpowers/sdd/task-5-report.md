# Task 5 Report: API command defaults for HK DB and Milvus collection

## Status
Complete on `master` in `/home/maoyd/siq-research-engine` via SSH alias `spark-1319`.

## Requirements Source
Read `/home/maoyd/siq-research-engine/.superpowers/sdd/task-5-brief.md` before implementation. Requirements were clear; no `NEEDS_CONTEXT` was needed.

## Red Phase
Added the required failing tests first:

- `test_market_report_settings_defaults` now asserts `MARKET_DATABASES["HK"] == "siq_hk"` and `MARKET_VECTOR_COLLECTIONS["HK"] == "siq_hk_reports"`.
- `test_market_package_import_env_defaults_hk_database` asserts the HK env overlay includes `SIQ_HK_PGDATABASE=siq_hk` and US does not receive that HK env var.
- `test_market_vector_ingest_args_defaults_hk_collection` asserts HK vector ingest args include `--collection siq_hk_reports` when payload omits `collection`.

Initial focused test run failed as expected:

```text
3 failed, 48 passed
AttributeError: settings has no attribute MARKET_DATABASES
AttributeError: market_report_commands has no attribute market_package_import_env
TypeError: market_vector_ingest_args() got an unexpected keyword argument 'market'
```

## Implementation
Changed only the API settings/command layer plus the necessary API router call sites:

- Added `MARKET_DATABASES` with HK default `siq_hk`, configurable through `SIQ_HK_PGDATABASE`.
- Added `MARKET_VECTOR_COLLECTIONS` with HK default `siq_hk_reports`, configurable through `SIQ_HK_MILVUS_COLLECTION`.
- Added pure helper `market_package_import_env(market, market_databases, base_env=None)` returning market-specific env overlay entries such as `SIQ_HK_PGDATABASE`.
- Kept concrete database URLs and passwords out of command args; existing `database_url` payload behavior remains explicit and separate.
- Wired `_run_market_package_import()` to pass `env={**os.environ, **market_package_import_env(...)}` only when the payload does not specify `database_url`.
- Updated `market_vector_ingest_args()` to default `--collection` from `MARKET_VECTOR_COLLECTIONS` when payload omits `collection`; explicit payload collection still wins.
- Wired `_run_market_vector_ingest()` to pass the market and collection defaults into the command helper.

No importer internals, package generation, DDL, frontend, or vector ingestion script internals were modified.

## Verification
Ran syntax and focused API tests:

```bash
python3 -m py_compile apps/api/services/market_report_settings.py apps/api/services/market_report_commands.py apps/api/routers/market_reports.py
cd /home/maoyd/siq-research-engine/apps/api
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_market_report_settings.py tests/test_market_report_commands.py
```

Result:

```text
51 passed in 0.07s
```

## Files Changed

- `apps/api/services/market_report_settings.py`
- `apps/api/services/market_report_commands.py`
- `apps/api/routers/market_reports.py`
- `apps/api/tests/test_market_report_settings.py`
- `apps/api/tests/test_market_report_commands.py`
- `.superpowers/sdd/task-5-report.md`

## Concerns
The brief listed four owned files, but actual API command execution required a small router call-site change so the env overlay and vector defaults are passed to subprocess execution. I kept that router change narrowly scoped to the Task 5 behavior.

---

## Review Fix: HK import env precedence

Fixed review findings from `review-4881fcf..cc55375.diff`:

- HK package import with no payload `database_url` now passes a sanitized subprocess environment that removes inherited `DATABASE_URL` and sets/preserves `SIQ_HK_PGDATABASE`, allowing the HK importer to target `siq_hk`.
- Explicit payload `database_url` behavior remains unchanged: the URL is still passed as `--database-url`, and no subprocess env override is attached for that path.
- Added router/call-site coverage proving inherited `DATABASE_URL` is absent from the HK command env while `SIQ_HK_PGDATABASE=siq_hk` is present.
- Added command coverage proving explicit HK vector `collection` wins over the HK default collection.
- Added settings override coverage for `SIQ_HK_PGDATABASE` and `SIQ_HK_MILVUS_COLLECTION`.

Red phase:

```text
4 failed, 109 passed, 4 warnings in 0.65s
KeyError: 'PATH'
NameError: name 'os' is not defined
AssertionError: explicit database_url path received env=None
NameError: name 'os' is not defined
```

Verification:

```bash
cd /home/maoyd/siq-research-engine/apps/api
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_market_report_settings.py tests/test_market_report_commands.py tests/test_market_reports_proxy.py
```

Result:

```text
113 passed, 4 warnings in 0.53s
```

---

## Re-review Fix: keep explicit database URLs out of argv

Fixed the Task 5 re-review blocker:

- Removed `--database-url <url>` from market package import command argv, including explicit payload `database_url` cases.
- Added `database_url` support to `market_package_import_env(...)`, where explicit payload values are written as `DATABASE_URL` and overwrite any inherited `DATABASE_URL`.
- Updated `_run_market_package_import()` to always ask the command helper for import env. HK no-payload imports still receive a sanitized env without inherited `DATABASE_URL` and with `SIQ_HK_PGDATABASE=siq_hk`; non-HK no-payload imports still avoid an env override.
- Updated command and router proxy tests to assert explicit URLs are present in env, absent from argv, and absent from displayed commands.

Red phase:

```text
3 failed, 2 warnings in 0.52s
AssertionError: --database-url still present in args
TypeError: market_package_import_env() got an unexpected keyword argument 'database_url'
AssertionError: explicit HK import argv still contained --database-url postgres://secret
```

Verification:

```bash
cd /home/maoyd/siq-research-engine/apps/api
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_market_report_settings.py tests/test_market_report_commands.py tests/test_market_reports_proxy.py
```

Result:

```text
114 passed, 4 warnings in 0.53s
```
