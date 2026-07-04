# PDF Parser Content Dedupe Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the current PDF parsing flow from creating duplicate parse tasks for byte-identical PDFs, even when filenames differ.

**Architecture:** Add SHA-256 persistence and duplicate lookup to the PDF parser task store, then make the workspace PDF proxy hash-aware so quota and parse-artifact reuse stay aligned with parser behavior. Keep the change inside the existing parser and workspace proxy boundaries without introducing new services or data stores.

**Tech Stack:** Flask, FastAPI, SQLite, SQLModel, Python stdlib `hashlib`, pytest/unittest

## Global Constraints

- Scope is limited to the current PDF parser and workspace PDF proxy.
- Canonical duplicate key is the uploaded PDF binary SHA-256 digest.
- Failed and cancelled tasks must not block future uploads.
- Explicit reparse remains allowed.
- Market attribution behavior must stay intact.

---

### Task 1: Persist and enforce parser-side content hashes

**Files:**
- Modify: `apps/pdf-parser/pdf_parser_task_repository.py`
- Modify: `apps/pdf-parser/pdf_parser_app_impl.py`
- Test: `apps/pdf-parser/tests/test_runtime_paths_and_task_state.py`

**Interfaces:**
- Consumes: existing parser task repository helpers and upload route flow
- Produces:
  - `find_duplicate_file_hash_task(db_path, file_sha256, *, normalize_task=None) -> dict | None`
  - parser task records that include `file_sha256`
  - upload route HTTP 409 payloads for content duplicates

- [ ] **Step 1: Write the failing parser tests**

```python
duplicate = app._find_duplicate_file_hash_task(file_sha256)
assert duplicate["task_id"] == "completed-task"
```

```python
response, status = app._upload_files()
assert status == 409
assert response.json["error"] == "duplicate_file_content"
```

- [ ] **Step 2: Run parser tests to verify they fail**

Run: `cd apps/pdf-parser && python3 -m pytest tests/test_runtime_paths_and_task_state.py -q`
Expected: FAIL because `file_sha256` is not stored/looked up yet and content duplicates are not rejected.

- [ ] **Step 3: Add repository support for file hashes**

```python
CREATE TABLE IF NOT EXISTS tasks (..., file_sha256 TEXT, ...)
CREATE INDEX IF NOT EXISTS idx_tasks_file_sha256 ON tasks(file_sha256)
```

```python
def find_duplicate_file_hash_task(db_path, file_sha256, *, normalize_task=None):
    rows = conn.execute(
        "SELECT * FROM tasks WHERE file_sha256 = ? ORDER BY created_at DESC",
        (file_sha256,),
    ).fetchall()
```

- [ ] **Step 4: Compute and store SHA-256 during parser upload**

```python
digest = hashlib.sha256()
while True:
    chunk = file.stream.read(UPLOAD_CHUNK_SIZE)
    if not chunk:
        break
    digest.update(chunk)
    outfile.write(chunk)
file_sha256 = digest.hexdigest()
```

```python
task = {
    ...,
    "file_sha256": file_sha256,
}
```

- [ ] **Step 5: Reject same-content duplicates before task creation**

```python
if file_sha256 in seen_hashes:
    return _duplicate_content_response(display_filename, message=...)
duplicate_task = _find_duplicate_file_hash_task(file_sha256)
if duplicate_task:
    return _duplicate_content_response(display_filename, duplicate_task, message=...)
```

- [ ] **Step 6: Preserve `file_sha256` on explicit reparse**

```python
"file_sha256": source_task.get("file_sha256") or _sha256_file(upload_path),
```

- [ ] **Step 7: Run parser tests to verify they pass**

Run: `cd apps/pdf-parser && python3 -m pytest tests/test_runtime_paths_and_task_state.py -q`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add apps/pdf-parser/pdf_parser_task_repository.py apps/pdf-parser/pdf_parser_app_impl.py apps/pdf-parser/tests/test_runtime_paths_and_task_state.py
git commit -m "feat: dedupe parser uploads by content hash"
```

### Task 2: Make workspace PDF proxy hash-aware for quota and reuse

**Files:**
- Modify: `apps/api/routers/workspace.py`
- Test: `apps/api/tests/test_workspace_sync.py`

**Interfaces:**
- Consumes:
  - upstream `/api/tasks` payloads that may include `file_sha256`
  - upstream HTTP 409 payloads with `error` in `{"duplicate_filename", "duplicate_file_content"}`
- Produces:
  - quota pre-counting based on new content, not only new filenames
  - parse-artifact reuse for content-duplicate responses

- [ ] **Step 1: Write the failing workspace tests**

```python
assert quota_calls == [{"event_type": PARSE_EVENT, "increment": 0}]
assert response.status_code == 409
assert artifact.source == "reused_parse"
```

- [ ] **Step 2: Run workspace tests to verify they fail**

Run: `cd apps/api && .venv/bin/python -m pytest tests/test_workspace_sync.py -q`
Expected: FAIL because the proxy still counts by filename only and only special-cases `duplicate_filename`.

- [ ] **Step 3: Compute upload SHA-256 values in the workspace proxy**

```python
digest = hashlib.sha256(content).hexdigest()
uploads.append({"filename": filename, "content": content, "content_type": ..., "file_sha256": digest})
```

- [ ] **Step 4: Count only truly new documents toward quota**

```python
existing_hashes = {
    str(task.get("file_sha256") or ""): task
    for task in existing_tasks.values()
    if task.get("file_sha256")
}
new_parse_count = sum(
    1
    for upload in uploads
    if upload["filename"] not in existing_tasks and upload["file_sha256"] not in existing_hashes
)
```

- [ ] **Step 5: Treat content-duplicate 409s as reusable existing tasks**

```python
if response.status_code == 409 and payload.get("error") in {"duplicate_filename", "duplicate_file_content"}:
    ...
```

- [ ] **Step 6: Run workspace tests to verify they pass**

Run: `cd apps/api && .venv/bin/python -m pytest tests/test_workspace_sync.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add apps/api/routers/workspace.py apps/api/tests/test_workspace_sync.py
git commit -m "feat: reuse duplicate pdf uploads by content hash"
```

## Self-Review

- Spec coverage: parser persistence, parser duplicate enforcement, workspace quota behavior, and workspace reuse are all covered.
- Placeholder scan: no `TODO` or unresolved task references remain.
- Type consistency: all new logic passes plain task dictionaries carrying `file_sha256: str | None`.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-05-pdf-parser-content-dedupe.md`. Two execution options:

1. Subagent-Driven (recommended) - I dispatch a fresh subagent per task, review between tasks, fast iteration
2. Inline Execution - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
