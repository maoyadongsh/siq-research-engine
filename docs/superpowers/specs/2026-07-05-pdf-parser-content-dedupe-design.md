# PDF Parser Content Dedupe Design

**Goal:** Prevent the current PDF/financial-report parsing pipeline from creating duplicate parse tasks when two uploads contain the exact same document bytes, even if filenames differ.

## Scope

This change is limited to the current PDF parsing path used by the financial-report workflow:

- `apps/pdf-parser` task storage and upload handling
- `apps/api/routers/workspace.py` PDF proxy quota/reuse logic

Out of scope:

- `document_parser` task flow
- download-source selection
- downstream analysis, evidence, or wiki ingestion behavior

## Problem

The current parser only blocks duplicate uploads by `filename`. That allows the same PDF to be submitted again under a different filename, which:

- creates redundant parse tasks
- wastes parser capacity
- consumes user quota incorrectly
- makes task lists noisier than they should be

## Design

### 1. Canonical duplicate key

Use the PDF file's binary SHA-256 digest as the canonical "same document" key.

- Same bytes => same document => do not create a new parse task
- Different bytes => treat as a new document, even if filenames match a prior failed/cancelled task

### 2. Parser-side persistence

Persist `file_sha256` on PDF parser tasks in SQLite.

The parser will:

- compute the digest while streaming the upload to disk
- store `file_sha256` on the task record
- expose it in the internal `/api/tasks` payload so the API proxy can make quota decisions without another storage layer

### 3. Parser-side duplicate checks

The parser upload route will enforce three checks:

1. duplicate filename inside the same request
2. duplicate content hash inside the same request
3. duplicate content hash against existing non-failed, non-cancelled tasks

For content duplicates, return HTTP 409 with the existing task payload, parallel to current duplicate-filename behavior.

Explicit reparse remains allowed. Reparse tasks will carry the same `file_sha256` as the source upload.

### 4. API proxy behavior

`workspace.authenticated_pdf_upload` already reads full file bytes before forwarding them upstream. It will:

- compute SHA-256 for each uploaded file
- use upstream `/api/tasks` results to recognize existing `file_sha256` values
- exclude hash-duplicates from quota pre-counting
- treat upstream `duplicate_file_content` the same way it currently treats `duplicate_filename` for artifact linking and reuse

### 5. User-visible behavior

From the user's perspective:

- exact duplicate PDFs are not parsed again
- already completed tasks continue to be reused
- quota is only consumed by truly new documents
- market attribution behavior remains unchanged

## Testing

Add focused tests for:

- parser task repository hash persistence and duplicate lookup
- parser upload rejecting same-content different-name duplicates
- workspace proxy not counting same-content duplicates toward parse quota
- workspace proxy reusing existing parse artifacts when upstream reports content duplicates
