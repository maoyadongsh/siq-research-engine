-- Named SQL blocks used by PROGRAM/import_document_full_to_postgres.py.
-- Placeholders use psycopg named style: %(field_name)s.

-- name: delete_document_children
DELETE FROM pdf2md.raw_payload_refs WHERE task_id = %(task_id)s;
DELETE FROM pdf2md.financial_all_metrics_wide WHERE task_id = %(task_id)s;
DELETE FROM pdf2md.financial_cash_flow_statement_items WHERE task_id = %(task_id)s;
DELETE FROM pdf2md.financial_income_statement_items WHERE task_id = %(task_id)s;
DELETE FROM pdf2md.financial_balance_sheet_items WHERE task_id = %(task_id)s;
DELETE FROM pdf2md.financial_checks WHERE task_id = %(task_id)s;
DELETE FROM pdf2md.financial_key_metrics WHERE task_id = %(task_id)s;
DELETE FROM pdf2md.financial_statement_items WHERE task_id = %(task_id)s;
DELETE FROM pdf2md.financial_statements WHERE task_id = %(task_id)s;
DELETE FROM pdf2md.financial_note_links WHERE task_id = %(task_id)s;
DELETE FROM pdf2md.toc_entries WHERE task_id = %(task_id)s;
DELETE FROM pdf2md.footnotes WHERE task_id = %(task_id)s;
DELETE FROM pdf2md.quality_warnings WHERE task_id = %(task_id)s;
DELETE FROM pdf2md.document_tables WHERE task_id = %(task_id)s;
DELETE FROM pdf2md.content_blocks WHERE task_id = %(task_id)s;
DELETE FROM pdf2md.document_pages WHERE task_id = %(task_id)s;
DELETE FROM pdf2md.document_artifacts WHERE task_id = %(task_id)s;
DELETE FROM pdf2md.document_chunks WHERE task_id = %(task_id)s;
DELETE FROM pdf2md.evidence_citations WHERE task_id = %(task_id)s;

-- name: upsert_document
INSERT INTO pdf2md.documents (
    task_id, mineru_task_id, filename, status, stage, created_at, completed_at, generated_at,
    pdf_page_count, schema_version, report_kind, report_year, submit_config, source_files,
    result_dir, document_full_path, markdown_path, complete_markdown_path, markdown_chars,
    markdown_line_count, financial_overall_status, quality_summary, financial_summary,
    resources_summary, raw_task, raw_json_hash, raw_json_size_bytes, imported_at, updated_at
) VALUES (
    %(task_id)s, %(mineru_task_id)s, %(filename)s, %(status)s, %(stage)s, %(created_at)s,
    %(completed_at)s, %(generated_at)s, %(pdf_page_count)s, %(schema_version)s, %(report_kind)s,
    %(report_year)s, %(submit_config)s, %(source_files)s, %(result_dir)s, %(document_full_path)s,
    %(markdown_path)s, %(complete_markdown_path)s, %(markdown_chars)s, %(markdown_line_count)s,
    %(financial_overall_status)s, %(quality_summary)s, %(financial_summary)s, %(resources_summary)s,
    %(raw_task)s, %(raw_json_hash)s, %(raw_json_size_bytes)s, now(), now()
) ON CONFLICT (task_id) DO UPDATE SET
    mineru_task_id = EXCLUDED.mineru_task_id,
    filename = EXCLUDED.filename,
    status = EXCLUDED.status,
    stage = EXCLUDED.stage,
    created_at = EXCLUDED.created_at,
    completed_at = EXCLUDED.completed_at,
    generated_at = EXCLUDED.generated_at,
    pdf_page_count = EXCLUDED.pdf_page_count,
    schema_version = EXCLUDED.schema_version,
    report_kind = EXCLUDED.report_kind,
    report_year = EXCLUDED.report_year,
    submit_config = EXCLUDED.submit_config,
    source_files = EXCLUDED.source_files,
    result_dir = EXCLUDED.result_dir,
    document_full_path = EXCLUDED.document_full_path,
    markdown_path = EXCLUDED.markdown_path,
    complete_markdown_path = EXCLUDED.complete_markdown_path,
    markdown_chars = EXCLUDED.markdown_chars,
    markdown_line_count = EXCLUDED.markdown_line_count,
    financial_overall_status = EXCLUDED.financial_overall_status,
    quality_summary = EXCLUDED.quality_summary,
    financial_summary = EXCLUDED.financial_summary,
    resources_summary = EXCLUDED.resources_summary,
    raw_task = EXCLUDED.raw_task,
    raw_json_hash = EXCLUDED.raw_json_hash,
    raw_json_size_bytes = EXCLUDED.raw_json_size_bytes,
    updated_at = now();

-- name: insert_artifact
INSERT INTO pdf2md.document_artifacts (
    task_id, artifact_name, kind, path, url, exists, size_bytes, mtime, sha256, raw
) VALUES (
    %(task_id)s, %(artifact_name)s, %(kind)s, %(path)s, %(url)s, %(exists)s,
    %(size_bytes)s, %(mtime)s, %(sha256)s, %(raw)s
);

-- name: insert_page
INSERT INTO pdf2md.document_pages (
    task_id, page_number, page_index, markdown_start, markdown_end, block_count, preview, raw
) VALUES (
    %(task_id)s, %(page_number)s, %(page_index)s, %(markdown_start)s, %(markdown_end)s,
    %(block_count)s, %(preview)s, %(raw)s
);

-- name: insert_content_block
INSERT INTO pdf2md.content_blocks (
    task_id, block_index, block_type, page_idx, page_number, bbox, text_preview,
    image_path, table_html_present, raw
) VALUES (
    %(task_id)s, %(block_index)s, %(block_type)s, %(page_idx)s, %(page_number)s, %(bbox)s,
    %(text_preview)s, %(image_path)s, %(table_html_present)s, %(raw)s
);

-- name: insert_table
INSERT INTO pdf2md.document_tables (
    task_id, table_index, markdown_line, pdf_page_number, pdf_page_index, bbox, source,
    confidence, source_image_path, rows_count, cells_count, heading, unit, preview,
    report_year, is_multi_level_header_candidate, is_suspicious, suspect_reasons,
    source_caption, source_footnote, structure, raw
) VALUES (
    %(task_id)s, %(table_index)s, %(markdown_line)s, %(pdf_page_number)s, %(pdf_page_index)s,
    %(bbox)s, %(source)s, %(confidence)s, %(source_image_path)s, %(rows_count)s,
    %(cells_count)s, %(heading)s, %(unit)s, %(preview)s, %(report_year)s,
    %(is_multi_level_header_candidate)s, %(is_suspicious)s, %(suspect_reasons)s,
    %(source_caption)s, %(source_footnote)s, %(structure)s, %(raw)s
);

-- name: insert_quality_warning
INSERT INTO pdf2md.quality_warnings (task_id, warning_index, warning)
VALUES (%(task_id)s, %(warning_index)s, %(warning)s);

-- name: insert_footnote
INSERT INTO pdf2md.footnotes (task_id, footnote_index, page_number, markdown_line, text, raw)
VALUES (%(task_id)s, %(footnote_index)s, %(page_number)s, %(markdown_line)s, %(text)s, %(raw)s);

-- name: insert_toc_entry
INSERT INTO pdf2md.toc_entries (task_id, toc_index, title, level, page_number, markdown_line, raw)
VALUES (%(task_id)s, %(toc_index)s, %(title)s, %(level)s, %(page_number)s, %(markdown_line)s, %(raw)s);

-- name: insert_financial_note_link
INSERT INTO pdf2md.financial_note_links (
    task_id, link_index, item_name, canonical_name, note_title, note_ref, table_index, page_number, raw
) VALUES (
    %(task_id)s, %(link_index)s, %(item_name)s, %(canonical_name)s, %(note_title)s,
    %(note_ref)s, %(table_index)s, %(page_number)s, %(raw)s
);

-- name: insert_financial_statement
INSERT INTO pdf2md.financial_statements (
    task_id, statement_id, statement_type, statement_name, scope, scope_name, title, unit,
    scale, currency, table_indexes, line_numbers, columns, raw
) VALUES (
    %(task_id)s, %(statement_id)s, %(statement_type)s, %(statement_name)s, %(scope)s,
    %(scope_name)s, %(title)s, %(unit)s, %(scale)s, %(currency)s, %(table_indexes)s,
    %(line_numbers)s, %(columns)s, %(raw)s
);

-- name: insert_financial_statement_item
INSERT INTO pdf2md.financial_statement_items (
    task_id, statement_id, item_index, period_key, item_name, canonical_name, value,
    raw_value, source, raw_item
) VALUES (
    %(task_id)s, %(statement_id)s, %(item_index)s, %(period_key)s, %(item_name)s,
    %(canonical_name)s, %(value)s, %(raw_value)s, %(source)s, %(raw_item)s
);

-- name: insert_balance_sheet_item
INSERT INTO pdf2md.financial_balance_sheet_items (
    task_id, statement_id, item_index, period_key, company_id, filing_id, parse_run_id,
    stock_code, stock_name, exchange, report_year, report_period, statement_name, scope, scope_name, item_name, canonical_name,
    value, raw_value, unit, currency, source_page_number, source_table_index, source_bbox,
    source, raw_item
) VALUES (
    %(task_id)s, %(statement_id)s, %(item_index)s, %(period_key)s, %(company_id)s,
    %(filing_id)s, %(parse_run_id)s, %(stock_code)s, %(stock_name)s, %(exchange)s,
    %(report_year)s, %(report_period)s,
    %(statement_name)s, %(scope)s, %(scope_name)s, %(item_name)s, %(canonical_name)s,
    %(value)s, %(raw_value)s, %(unit)s, %(currency)s, %(source_page_number)s,
    %(source_table_index)s, %(source_bbox)s, %(source)s, %(raw_item)s
);

-- name: insert_income_statement_item
INSERT INTO pdf2md.financial_income_statement_items (
    task_id, statement_id, item_index, period_key, company_id, filing_id, parse_run_id,
    stock_code, stock_name, exchange, report_year, report_period, statement_name, scope, scope_name, item_name, canonical_name,
    value, raw_value, unit, currency, source_page_number, source_table_index, source_bbox,
    source, raw_item
) VALUES (
    %(task_id)s, %(statement_id)s, %(item_index)s, %(period_key)s, %(company_id)s,
    %(filing_id)s, %(parse_run_id)s, %(stock_code)s, %(stock_name)s, %(exchange)s,
    %(report_year)s, %(report_period)s,
    %(statement_name)s, %(scope)s, %(scope_name)s, %(item_name)s, %(canonical_name)s,
    %(value)s, %(raw_value)s, %(unit)s, %(currency)s, %(source_page_number)s,
    %(source_table_index)s, %(source_bbox)s, %(source)s, %(raw_item)s
);

-- name: insert_cash_flow_statement_item
INSERT INTO pdf2md.financial_cash_flow_statement_items (
    task_id, statement_id, item_index, period_key, company_id, filing_id, parse_run_id,
    stock_code, stock_name, exchange, report_year, report_period, statement_name, scope, scope_name, item_name, canonical_name,
    value, raw_value, unit, currency, source_page_number, source_table_index, source_bbox,
    source, raw_item
) VALUES (
    %(task_id)s, %(statement_id)s, %(item_index)s, %(period_key)s, %(company_id)s,
    %(filing_id)s, %(parse_run_id)s, %(stock_code)s, %(stock_name)s, %(exchange)s,
    %(report_year)s, %(report_period)s,
    %(statement_name)s, %(scope)s, %(scope_name)s, %(item_name)s, %(canonical_name)s,
    %(value)s, %(raw_value)s, %(unit)s, %(currency)s, %(source_page_number)s,
    %(source_table_index)s, %(source_bbox)s, %(source)s, %(raw_item)s
);

-- name: insert_all_metrics_wide
INSERT INTO pdf2md.financial_all_metrics_wide (
    task_id, period_key, company_id, filing_id, parse_run_id, stock_code, stock_name, exchange, report_year, report_period,
    balance_sheet, income_statement, cash_flow_statement, key_metrics, all_metrics, raw
) VALUES (
    %(task_id)s, %(period_key)s, %(company_id)s, %(filing_id)s, %(parse_run_id)s,
    %(stock_code)s, %(stock_name)s, %(exchange)s, %(report_year)s, %(report_period)s, %(balance_sheet)s, %(income_statement)s,
    %(cash_flow_statement)s, %(key_metrics)s, %(all_metrics)s, %(raw)s
);

-- name: insert_financial_key_metric
INSERT INTO pdf2md.financial_key_metrics (
    task_id, metric_index, period_key, metric_name, canonical_name, value, raw_value, unit, source, raw_metric
) VALUES (
    %(task_id)s, %(metric_index)s, %(period_key)s, %(metric_name)s, %(canonical_name)s,
    %(value)s, %(raw_value)s, %(unit)s, %(source)s, %(raw_metric)s
);

-- name: insert_financial_check
INSERT INTO pdf2md.financial_checks (
    task_id, check_index, rule_id, rule_name, statement_type, scope, period, status,
    diff, tolerance, inputs, left_side, right_side, raw
) VALUES (
    %(task_id)s, %(check_index)s, %(rule_id)s, %(rule_name)s, %(statement_type)s,
    %(scope)s, %(period)s, %(status)s, %(diff)s, %(tolerance)s, %(inputs)s,
    %(left_side)s, %(right_side)s, %(raw)s
);

-- name: insert_raw_payload_ref
INSERT INTO pdf2md.raw_payload_refs (task_id, payload_name, path, url, size_bytes, sha256, summary)
VALUES (%(task_id)s, %(payload_name)s, %(path)s, %(url)s, %(size_bytes)s, %(sha256)s, %(summary)s);

-- name: prepare_company_identity
UPDATE pdf2md.companies
SET stock_code = NULL,
    raw = raw || jsonb_build_object(
        'identity_migration', jsonb_build_object(
            'superseded_by_company_id', %(company_id)s,
            'superseded_stock_code', %(stock_code)s,
            'updated_at', now()
        )
    ),
    updated_at = now()
WHERE stock_code = %(stock_code)s
  AND company_id <> %(company_id)s;

-- name: upsert_company
INSERT INTO pdf2md.companies (
    company_id, stock_code, stock_name, exchange, industry, listing_status, aliases, raw, updated_at
) VALUES (
    %(company_id)s, %(stock_code)s, %(stock_name)s, %(exchange)s, %(industry)s,
    %(listing_status)s, %(aliases)s, %(raw)s, now()
) ON CONFLICT (company_id) DO UPDATE SET
    stock_code = CASE
        WHEN EXCLUDED.raw->'company_json'->>'synthetic_stock_code' = 'true'
          OR EXCLUDED.raw->'company_json'->>'identity_kind' IN ('generic_subject', 'non_a_share')
          OR EXCLUDED.raw->'company_json'->>'identity_route' LIKE '%%non_a_share%%'
          OR EXCLUDED.raw->'company_json'->>'identity_route' LIKE '%%generic%%'
        THEN EXCLUDED.stock_code
        ELSE COALESCE(EXCLUDED.stock_code, pdf2md.companies.stock_code)
    END,
    stock_name = EXCLUDED.stock_name,
    exchange = COALESCE(EXCLUDED.exchange, pdf2md.companies.exchange),
    industry = COALESCE(EXCLUDED.industry, pdf2md.companies.industry),
    listing_status = COALESCE(EXCLUDED.listing_status, pdf2md.companies.listing_status),
    aliases = EXCLUDED.aliases,
    raw = pdf2md.companies.raw || EXCLUDED.raw,
    updated_at = now();

-- name: upsert_non_a_share_company
INSERT INTO pdf2md.non_a_share_companies (
    non_a_share_company_id, company_id, display_name, legal_name, market, exchange,
    security_code, synthetic_code, identity_kind, identity_route, aliases, raw, updated_at
) VALUES (
    %(non_a_share_company_id)s, %(company_id)s, %(display_name)s, %(legal_name)s,
    %(market)s, %(exchange)s, %(security_code)s, %(synthetic_code)s,
    %(identity_kind)s, %(identity_route)s, %(aliases)s, %(raw)s, now()
) ON CONFLICT (non_a_share_company_id) DO UPDATE SET
    company_id = EXCLUDED.company_id,
    display_name = EXCLUDED.display_name,
    legal_name = EXCLUDED.legal_name,
    market = EXCLUDED.market,
    exchange = EXCLUDED.exchange,
    security_code = EXCLUDED.security_code,
    synthetic_code = EXCLUDED.synthetic_code,
    identity_kind = EXCLUDED.identity_kind,
    identity_route = EXCLUDED.identity_route,
    aliases = EXCLUDED.aliases,
    raw = pdf2md.non_a_share_companies.raw || EXCLUDED.raw,
    updated_at = now();

-- name: normalize_company_identity
UPDATE pdf2md.company_filings
SET company_id = %(company_id)s,
    updated_at = now()
WHERE company_id IN (
    SELECT company_id
    FROM pdf2md.companies
    WHERE company_id <> %(company_id)s
      AND (
        company_id LIKE (%(stock_code)s || '-%%')
        OR raw->'identity_migration'->>'superseded_by_company_id' = %(company_id)s
      )
);
UPDATE pdf2md.documents
SET company_id = %(company_id)s,
    updated_at = now()
WHERE company_id IN (
    SELECT company_id
    FROM pdf2md.companies
    WHERE company_id <> %(company_id)s
      AND (
        company_id LIKE (%(stock_code)s || '-%%')
        OR raw->'identity_migration'->>'superseded_by_company_id' = %(company_id)s
      )
);
UPDATE pdf2md.financial_statement_items
SET company_id = %(company_id)s
WHERE company_id IN (
    SELECT company_id
    FROM pdf2md.companies
    WHERE company_id <> %(company_id)s
      AND (
        company_id LIKE (%(stock_code)s || '-%%')
        OR raw->'identity_migration'->>'superseded_by_company_id' = %(company_id)s
      )
);
UPDATE pdf2md.financial_key_metrics
SET company_id = %(company_id)s
WHERE company_id IN (
    SELECT company_id
    FROM pdf2md.companies
    WHERE company_id <> %(company_id)s
      AND (
        company_id LIKE (%(stock_code)s || '-%%')
        OR raw->'identity_migration'->>'superseded_by_company_id' = %(company_id)s
      )
);
UPDATE pdf2md.financial_balance_sheet_items
SET company_id = %(company_id)s
WHERE company_id IN (
    SELECT company_id
    FROM pdf2md.companies
    WHERE company_id <> %(company_id)s
      AND (
        company_id LIKE (%(stock_code)s || '-%%')
        OR raw->'identity_migration'->>'superseded_by_company_id' = %(company_id)s
      )
);
UPDATE pdf2md.financial_income_statement_items
SET company_id = %(company_id)s
WHERE company_id IN (
    SELECT company_id
    FROM pdf2md.companies
    WHERE company_id <> %(company_id)s
      AND (
        company_id LIKE (%(stock_code)s || '-%%')
        OR raw->'identity_migration'->>'superseded_by_company_id' = %(company_id)s
      )
);
UPDATE pdf2md.financial_cash_flow_statement_items
SET company_id = %(company_id)s
WHERE company_id IN (
    SELECT company_id
    FROM pdf2md.companies
    WHERE company_id <> %(company_id)s
      AND (
        company_id LIKE (%(stock_code)s || '-%%')
        OR raw->'identity_migration'->>'superseded_by_company_id' = %(company_id)s
      )
);
UPDATE pdf2md.financial_all_metrics_wide
SET company_id = %(company_id)s
WHERE company_id IN (
    SELECT company_id
    FROM pdf2md.companies
    WHERE company_id <> %(company_id)s
      AND (
        company_id LIKE (%(stock_code)s || '-%%')
        OR raw->'identity_migration'->>'superseded_by_company_id' = %(company_id)s
      )
);
UPDATE pdf2md.generated_reports
SET company_id = %(company_id)s
WHERE company_id IN (
    SELECT company_id
    FROM pdf2md.companies
    WHERE company_id <> %(company_id)s
      AND (
        company_id LIKE (%(stock_code)s || '-%%')
        OR raw->'identity_migration'->>'superseded_by_company_id' = %(company_id)s
      )
);
UPDATE pdf2md.gold_financial_items
SET company_id = %(company_id)s
WHERE company_id IN (
    SELECT company_id
    FROM pdf2md.companies
    WHERE company_id <> %(company_id)s
      AND (
        company_id LIKE (%(stock_code)s || '-%%')
        OR raw->'identity_migration'->>'superseded_by_company_id' = %(company_id)s
      )
);
DELETE FROM pdf2md.companies
WHERE company_id <> %(company_id)s
  AND (
    company_id LIKE (%(stock_code)s || '-%%')
    OR raw->'identity_migration'->>'superseded_by_company_id' = %(company_id)s
  );

-- name: prepare_filing_task_rebind
UPDATE pdf2md.company_filings
SET task_id = NULL,
    updated_at = now()
WHERE task_id = %(task_id)s
  AND filing_id <> %(filing_id)s;

-- name: rebind_filing_links
UPDATE pdf2md.parse_runs
SET filing_id = %(filing_id)s,
    updated_at = now()
WHERE task_id = %(task_id)s
  AND filing_id IS DISTINCT FROM %(filing_id)s;
UPDATE pdf2md.documents
SET filing_id = %(filing_id)s,
    updated_at = now()
WHERE task_id = %(task_id)s
  AND filing_id IS DISTINCT FROM %(filing_id)s;
UPDATE pdf2md.financial_statement_items
SET filing_id = %(filing_id)s
WHERE task_id = %(task_id)s
  AND filing_id IS DISTINCT FROM %(filing_id)s;
UPDATE pdf2md.financial_key_metrics
SET filing_id = %(filing_id)s
WHERE task_id = %(task_id)s
  AND filing_id IS DISTINCT FROM %(filing_id)s;
UPDATE pdf2md.financial_balance_sheet_items
SET filing_id = %(filing_id)s
WHERE task_id = %(task_id)s
  AND filing_id IS DISTINCT FROM %(filing_id)s;
UPDATE pdf2md.financial_income_statement_items
SET filing_id = %(filing_id)s
WHERE task_id = %(task_id)s
  AND filing_id IS DISTINCT FROM %(filing_id)s;
UPDATE pdf2md.financial_cash_flow_statement_items
SET filing_id = %(filing_id)s
WHERE task_id = %(task_id)s
  AND filing_id IS DISTINCT FROM %(filing_id)s;
UPDATE pdf2md.financial_all_metrics_wide
SET filing_id = %(filing_id)s
WHERE task_id = %(task_id)s
  AND filing_id IS DISTINCT FROM %(filing_id)s;
UPDATE pdf2md.generated_reports
SET filing_id = %(filing_id)s
WHERE task_id = %(task_id)s
  AND filing_id IS DISTINCT FROM %(filing_id)s;

-- name: upsert_company_filing
INSERT INTO pdf2md.company_filings (
    filing_id, company_id, task_id, report_year, report_period, report_type, title,
    announcement_date, source_url, pdf_path, pdf_sha256, is_latest, raw, updated_at
) VALUES (
    %(filing_id)s, %(company_id)s, %(task_id)s, %(report_year)s, %(report_period)s,
    %(report_type)s, %(title)s, %(announcement_date)s, %(source_url)s, %(pdf_path)s,
    %(pdf_sha256)s, %(is_latest)s, %(raw)s, now()
) ON CONFLICT (filing_id) DO UPDATE SET
    company_id = EXCLUDED.company_id,
    task_id = EXCLUDED.task_id,
    report_year = EXCLUDED.report_year,
    report_period = EXCLUDED.report_period,
    report_type = EXCLUDED.report_type,
    title = EXCLUDED.title,
    announcement_date = EXCLUDED.announcement_date,
    source_url = EXCLUDED.source_url,
    pdf_path = EXCLUDED.pdf_path,
    pdf_sha256 = EXCLUDED.pdf_sha256,
    is_latest = EXCLUDED.is_latest,
    raw = pdf2md.company_filings.raw || EXCLUDED.raw,
    updated_at = now();

-- name: upsert_non_a_share_company_filing
INSERT INTO pdf2md.non_a_share_company_filings (
    non_a_share_filing_id, non_a_share_company_id, filing_id, task_id, report_year,
    report_period, report_type, title, source_url, pdf_path, is_latest, raw, updated_at
) VALUES (
    %(non_a_share_filing_id)s, %(non_a_share_company_id)s, %(filing_id)s,
    %(task_id)s, %(report_year)s, %(report_period)s, %(report_type)s, %(title)s,
    %(source_url)s, %(pdf_path)s, %(is_latest)s, %(raw)s, now()
) ON CONFLICT (non_a_share_filing_id) DO UPDATE SET
    non_a_share_company_id = EXCLUDED.non_a_share_company_id,
    filing_id = EXCLUDED.filing_id,
    task_id = EXCLUDED.task_id,
    report_year = EXCLUDED.report_year,
    report_period = EXCLUDED.report_period,
    report_type = EXCLUDED.report_type,
    title = EXCLUDED.title,
    source_url = EXCLUDED.source_url,
    pdf_path = EXCLUDED.pdf_path,
    is_latest = EXCLUDED.is_latest,
    raw = pdf2md.non_a_share_company_filings.raw || EXCLUDED.raw,
    updated_at = now();

-- name: upsert_parse_run
INSERT INTO pdf2md.parse_runs (
    parse_run_id, task_id, filing_id, mineru_task_id, parser_name, parser_version,
    schema_version, rule_version, status, started_at, completed_at, quality_score,
    quality_summary, raw, updated_at
) VALUES (
    %(parse_run_id)s, %(task_id)s, %(filing_id)s, %(mineru_task_id)s, %(parser_name)s,
    %(parser_version)s, %(schema_version)s, %(rule_version)s, %(status)s,
    %(started_at)s, %(completed_at)s, %(quality_score)s, %(quality_summary)s, %(raw)s, now()
) ON CONFLICT (task_id) DO UPDATE SET
    parse_run_id = EXCLUDED.parse_run_id,
    filing_id = EXCLUDED.filing_id,
    mineru_task_id = EXCLUDED.mineru_task_id,
    parser_name = EXCLUDED.parser_name,
    parser_version = EXCLUDED.parser_version,
    schema_version = EXCLUDED.schema_version,
    rule_version = EXCLUDED.rule_version,
    status = EXCLUDED.status,
    started_at = EXCLUDED.started_at,
    completed_at = EXCLUDED.completed_at,
    quality_score = EXCLUDED.quality_score,
    quality_summary = EXCLUDED.quality_summary,
    raw = pdf2md.parse_runs.raw || EXCLUDED.raw,
    updated_at = now();

-- name: update_document_links
UPDATE pdf2md.documents
SET company_id = %(company_id)s,
    filing_id = %(filing_id)s,
    parse_run_id = %(parse_run_id)s,
    updated_at = now()
WHERE task_id = %(task_id)s;

-- name: update_financial_statement_item_links
UPDATE pdf2md.financial_statement_items
SET company_id = %(company_id)s,
    filing_id = %(filing_id)s,
    parse_run_id = %(parse_run_id)s,
    report_year = %(report_year)s,
    report_period = %(report_period)s,
    source_page_number = NULLIF((source->>'pdf_page_number'), '')::integer,
    source_table_index = NULLIF((source->>'table_index'), '')::integer,
    source_bbox = CASE WHEN source ? 'bbox' THEN source->'bbox' ELSE NULL END,
    normalized_unit = %(normalized_unit)s,
    value_scale = %(value_scale)s
WHERE task_id = %(task_id)s;

-- name: update_financial_key_metric_links
UPDATE pdf2md.financial_key_metrics
SET company_id = %(company_id)s,
    filing_id = %(filing_id)s,
    parse_run_id = %(parse_run_id)s,
    report_year = %(report_year)s,
    report_period = %(report_period)s,
    source_page_number = NULLIF((source->>'pdf_page_number'), '')::integer,
    source_table_index = NULLIF((source->>'table_index'), '')::integer,
    source_bbox = CASE WHEN source ? 'bbox' THEN source->'bbox' ELSE NULL END,
    normalized_unit = COALESCE(unit, %(normalized_unit)s),
    value_scale = %(value_scale)s
WHERE task_id = %(task_id)s;

-- name: insert_document_chunk
INSERT INTO pdf2md.document_chunks (
    chunk_id, task_id, parse_run_id, chunk_index, chunk_type, page_number, title,
    content, token_count, source_block_ids, source_table_ids, source, embedding, raw
) VALUES (
    %(chunk_id)s, %(task_id)s, %(parse_run_id)s, %(chunk_index)s, %(chunk_type)s,
    %(page_number)s, %(title)s, %(content)s, %(token_count)s, %(source_block_ids)s,
    %(source_table_ids)s, %(source)s, %(embedding)s, %(raw)s
);

-- name: insert_evidence_citation
INSERT INTO pdf2md.evidence_citations (
    citation_id, task_id, parse_run_id, source_type, source_id, page_number, bbox,
    quote_text, path, url, raw
) VALUES (
    %(citation_id)s, %(task_id)s, %(parse_run_id)s, %(source_type)s, %(source_id)s,
    %(page_number)s, %(bbox)s, %(quote_text)s, %(path)s, %(url)s, %(raw)s
);
