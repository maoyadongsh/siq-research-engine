# Task 1 Report: HK DDL Contract For Structured Ingestion

Status: DONE_WITH_CONCERNS

Commit: d5daf3ab6378ba3208476605e350fe33f067814b

Modified files:
- db/ddl/020_create_pdf2md_hk_schema.sql
- db/imports/tests/test_import_hk_evidence_package.py

Report file:
- .superpowers/sdd/task-1-report.md

## RED

Command run from the required implementation worktree, using the existing base repo virtualenv interpreter because the worktree has no apps/api/.venv:

```bash
cd /home/maoyd/siq-research-engine-hk-pg-impl
/home/maoyd/siq-research-engine/apps/api/.venv/bin/python -m pytest db/imports/tests/test_import_hk_evidence_package.py::test_hk_ddl_exposes_agent_recall_columns_and_views -q --tb=short
```

Result: FAILED as expected.

Key output:

```text
FAILED db/imports/tests/test_import_hk_evidence_package.py::test_hk_ddl_exposes_agent_recall_columns_and_views
AssertionError: assert 'alter table pdf2md_hk.filings add column if not exists report_id text' in ddl
1 failed in 0.06s
```

## GREEN

Command:

```bash
cd /home/maoyd/siq-research-engine-hk-pg-impl
/home/maoyd/siq-research-engine/apps/api/.venv/bin/python -m pytest db/imports/tests/test_import_hk_evidence_package.py::test_hk_ddl_exposes_agent_recall_columns_and_views -q --tb=short
```

Result: PASSED.

Key output:

```text
.                                                                        [100%]
1 passed in 0.05s
```

Post-commit verification command:

```bash
cd /home/maoyd/siq-research-engine-hk-pg-impl
/home/maoyd/siq-research-engine/apps/api/.venv/bin/python -m pytest db/imports/tests/test_import_hk_evidence_package.py::test_hk_ddl_exposes_agent_recall_columns_and_views -q --tb=short
```

Result: PASSED.

Key output:

```text
.                                                                        [100%]
1 passed in 0.03s
```

Additional same-file regression check:

```bash
cd /home/maoyd/siq-research-engine-hk-pg-impl
/home/maoyd/siq-research-engine/apps/api/.venv/bin/python -m pytest db/imports/tests/test_import_hk_evidence_package.py -q --tb=short
```

Result: PASSED.

Key output:

```text
.....                                                                    [100%]
5 passed in 0.04s
```

## Literal Brief Command Check

The brief's literal command changes directory to /home/maoyd/siq-research-engine, which is the base repo on branch master, not the requested worktree /home/maoyd/siq-research-engine-hk-pg-impl. Running it literally after the worktree commit produced:

```bash
cd /home/maoyd/siq-research-engine
apps/api/.venv/bin/python -m pytest db/imports/tests/test_import_hk_evidence_package.py::test_hk_ddl_exposes_agent_recall_columns_and_views -q --tb=short
```

Result: ERROR, test not found in base repo/master.

Key output:

```text
ERROR: not found: /home/maoyd/siq-research-engine/db/imports/tests/test_import_hk_evidence_package.py::test_hk_ddl_exposes_agent_recall_columns_and_views
no tests ran in 0.00s
```

Concern: I used the worktree-equivalent command for RED/GREEN so importer.DDL_PATH resolved to the changed worktree files. The literal brief command does not target the requested worktree on this host.

## Self-Review

- Confirmed worktree branch is codex/hk-postgres-ingestion.
- Added only the requested DDL contract test to db/imports/tests/test_import_hk_evidence_package.py.
- Added idempotent HK schema extensions to db/ddl/020_create_pdf2md_hk_schema.sql: report_id, bbox columns, retrieval chunk recall columns, company exchange, unique/lookup indexes, v_agent_financial_facts, and v_latest_company_reports.
- Caught and fixed a shell-quoting regression in the unique index predicate so it now reads hkex_stock_code <> ''.
- Ran git diff --check successfully.
- Committed only the two requested task files with message: feat(hk): extend postgres schema for agent recall.
- Left pre-existing untracked .superpowers/sdd/progress.md and .superpowers/sdd/task-1-brief.md uncommitted; task-1-report.md is written as requested and not included in the code commit.
