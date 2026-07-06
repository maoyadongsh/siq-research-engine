# P2 Runtime Path Mapping

P2-02 introduces a resolver compatibility layer only. It does not migrate,
delete, rename, or backfill existing `data/` content.

## Root Priority

1. Leaf service variables win first, for example `SIQ_PDF_RESULTS_ROOT`,
   `PDF_RESULTS_ROOT`, `SIQ_DOCUMENT_PARSE_RESULTS_ROOT`,
   `SIQ_BACKEND_DATA_ROOT`, and `SIQ_REPORT_DOWNLOADS_ROOT`.
2. Service data variables win next, for example `SIQ_PDF2MD_DATA_DIR`,
   `PDF2MD_DATA_DIR`, `SIQ_DOCUMENT_PARSE_DATA_DIR`, and
   `DOCUMENT_PARSE_DATA_DIR`.
3. Generic split-layout roots apply when explicitly set:
   `SIQ_RUNTIME_ROOT` for mutable service state and `SIQ_ARTIFACTS_ROOT` for
   generated artifacts.
4. `SIQ_DATA_ROOT` remains the canonical data root.
5. If no new root is configured, the legacy monorepo default remains
   `$PROJECT_ROOT/data`.

## Mapping

| Purpose | Legacy default | Split-layout default when opted in |
| --- | --- | --- |
| API runtime state | `data/backend` | `${SIQ_RUNTIME_ROOT}/api` |
| PDF parser state | `data/pdf-parser` | `${SIQ_RUNTIME_ROOT}/pdf-parser` |
| PDF parser results | `data/pdf-parser/results` | `${SIQ_ARTIFACTS_ROOT}/pdf-parser/results` |
| PDF parser output | `data/pdf-parser/output` | `${SIQ_ARTIFACTS_ROOT}/pdf-parser/output` |
| Document parser state | `data/document-parser` | `${SIQ_RUNTIME_ROOT}/document-parser` |
| Document parser results | `data/document-parser/results` | `${SIQ_ARTIFACTS_ROOT}/document-parser/results` |
| Document parser output | `data/document-parser/output` | `${SIQ_ARTIFACTS_ROOT}/document-parser/output` |
| Market report downloads | `data/market-report-finder/downloads` | `${SIQ_ARTIFACTS_ROOT}/market-report-finder/downloads` |
| Wiki/canonical data | `data/wiki` | `${SIQ_DATA_ROOT}/wiki` |
| Hermes home | `data/hermes/home` | `${SIQ_RUNTIME_ROOT}/hermes/home` |

## Compatibility Reads

Resolvers expose legacy candidates after the configured primary path for PDF
parser results/output, document parser results/output, report downloads, and
wiki roots. New writes use the primary resolved path. Existing `data/` content
is left in place and can still be found through compatibility candidates.
