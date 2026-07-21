# API CI Wiki fixture

This directory contains minimal slices of public company disclosures used by
hermetic API tests. It deliberately excludes original PDFs, full reports,
runtime databases, user uploads, generated analysis, and model output.

Regenerate it only from an authorized local Wiki checkout:

```bash
python scripts/maintenance/build_api_ci_wiki_fixture.py \
  --legacy-wiki-root "$SIQ_LEGACY_WIKI_ROOT"
```

The legacy root is required only for the historical BASF locator contract.
Every generated company slice retains its task/report identity and only the
metrics, table windows, and page mappings exercised by CI.
