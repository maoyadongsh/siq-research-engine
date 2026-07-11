create schema if not exists pdf2md_hk;

drop view if exists pdf2md_hk.v_latest_company_reports cascade;
drop view if exists pdf2md_hk.v_agent_financial_facts cascade;
drop view if exists pdf2md_hk.v_latest_parse_runs cascade;

create table if not exists pdf2md_hk.companies (
    company_id text primary key,
    ticker text not null,
    company_name text,
    raw jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

alter table pdf2md_hk.companies add column if not exists stock_code text;
alter table pdf2md_hk.companies add column if not exists hkex_stock_code text;
alter table pdf2md_hk.companies add column if not exists short_name text;
alter table pdf2md_hk.companies add column if not exists company_name_en text;
alter table pdf2md_hk.companies add column if not exists company_name_zh text;
alter table pdf2md_hk.companies add column if not exists aliases jsonb not null default '[]'::jsonb;

create index if not exists idx_pdf2md_hk_companies_ticker on pdf2md_hk.companies (ticker);

create table if not exists pdf2md_hk.filings (
    filing_id text primary key,
    company_id text not null references pdf2md_hk.companies(company_id),
    ticker text not null,
    stock_code text,
    form text,
    report_type text,
    fiscal_year integer,
    fiscal_period text,
    period_end date,
    published_at date,
    source_id text,
    source_url text,
    local_path text,
    accounting_standard text,
    quality_status text,
    raw jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

alter table pdf2md_hk.filings add column if not exists stock_code text;

create index if not exists idx_pdf2md_hk_filings_ticker_year on pdf2md_hk.filings (ticker, fiscal_year, report_type);
create index if not exists idx_pdf2md_hk_filings_period_end on pdf2md_hk.filings (period_end);

create table if not exists pdf2md_hk.parse_runs (
    parse_run_id text primary key,
    filing_id text not null references pdf2md_hk.filings(filing_id) on delete cascade,
    parser_version text not null,
    rules_version text not null,
    wiki_package_path text not null,
    status text not null,
    started_at timestamptz not null default now(),
    completed_at timestamptz,
    warnings jsonb not null default '[]'::jsonb,
    artifact_hashes jsonb not null default '{}'::jsonb,
    raw jsonb not null default '{}'::jsonb
);

create index if not exists idx_pdf2md_hk_parse_runs_filing on pdf2md_hk.parse_runs (filing_id, completed_at desc);
create index if not exists idx_pdf2md_hk_parse_runs_status on pdf2md_hk.parse_runs (status);

create table if not exists pdf2md_hk.artifacts (
    parse_run_id text not null references pdf2md_hk.parse_runs(parse_run_id) on delete cascade,
    artifact_type text not null,
    local_path text not null,
    sha256 text,
    size_bytes bigint,
    raw jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    primary key (parse_run_id, artifact_type)
);

create table if not exists pdf2md_hk.raw_payload_refs (
    payload_ref_id text primary key,
    filing_id text not null references pdf2md_hk.filings(filing_id) on delete cascade,
    parse_run_id text not null references pdf2md_hk.parse_runs(parse_run_id) on delete cascade,
    payload_name text not null,
    local_path text,
    sha256 text,
    size_bytes bigint,
    summary jsonb not null default '{}'::jsonb,
    raw jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

create index if not exists idx_pdf2md_hk_raw_payload_refs_parse_run on pdf2md_hk.raw_payload_refs (parse_run_id);
create index if not exists idx_pdf2md_hk_raw_payload_refs_sha256 on pdf2md_hk.raw_payload_refs (sha256);

create table if not exists pdf2md_hk.filing_sections (
    parse_run_id text not null references pdf2md_hk.parse_runs(parse_run_id) on delete cascade,
    filing_id text not null references pdf2md_hk.filings(filing_id) on delete cascade,
    section_id text not null,
    section_title text,
    section_order integer,
    markdown_path text,
    raw jsonb not null default '{}'::jsonb,
    primary key (parse_run_id, section_id)
);

create table if not exists pdf2md_hk.pdf_pages (
    parse_run_id text not null references pdf2md_hk.parse_runs(parse_run_id) on delete cascade,
    filing_id text not null references pdf2md_hk.filings(filing_id) on delete cascade,
    page_number integer not null,
    markdown_path text,
    image_path text,
    raw jsonb not null default '{}'::jsonb,
    primary key (parse_run_id, page_number)
);

create table if not exists pdf2md_hk.pdf_tables (
    parse_run_id text not null references pdf2md_hk.parse_runs(parse_run_id) on delete cascade,
    filing_id text not null references pdf2md_hk.filings(filing_id) on delete cascade,
    table_id text not null,
    page_number integer,
    table_index integer,
    title text,
    row_count integer,
    column_count integer,
    table_json_path text,
    raw jsonb not null default '{}'::jsonb,
    primary key (parse_run_id, table_id)
);

create index if not exists idx_pdf2md_hk_pdf_tables_page_table on pdf2md_hk.pdf_tables (filing_id, page_number, table_index);

create table if not exists pdf2md_hk.evidence_citations (
    evidence_id text primary key,
    filing_id text not null references pdf2md_hk.filings(filing_id) on delete cascade,
    parse_run_id text not null references pdf2md_hk.parse_runs(parse_run_id) on delete cascade,
    source_type text not null,
    source_id text,
    page_number integer,
    table_index integer,
    row_index integer,
    column_index integer,
    quote_text text,
    local_path text,
    source_url text,
    target text,
    raw jsonb not null default '{}'::jsonb
);

create index if not exists idx_pdf2md_hk_evidence_location on pdf2md_hk.evidence_citations (filing_id, page_number, table_index);

create table if not exists pdf2md_hk.financial_facts (
    metric_id text primary key,
    filing_id text not null references pdf2md_hk.filings(filing_id) on delete cascade,
    parse_run_id text not null references pdf2md_hk.parse_runs(parse_run_id) on delete cascade,
    ticker text not null,
    statement_type text,
    canonical_name text not null,
    local_name text,
    value numeric,
    raw_value text,
    unit text,
    currency text,
    period_key text,
    period_start date,
    period_end date,
    fiscal_year integer,
    fiscal_period text,
    confidence numeric,
    evidence_id text references pdf2md_hk.evidence_citations(evidence_id),
    raw jsonb not null default '{}'::jsonb
);

create index if not exists idx_pdf2md_hk_financial_facts_ticker_metric_period on pdf2md_hk.financial_facts (ticker, canonical_name, period_key);
create index if not exists idx_pdf2md_hk_financial_facts_filing_statement on pdf2md_hk.financial_facts (filing_id, statement_type);


create table if not exists pdf2md_hk.financial_statements (
    parse_run_id text not null references pdf2md_hk.parse_runs(parse_run_id) on delete cascade,
    filing_id text not null references pdf2md_hk.filings(filing_id) on delete cascade,
    statement_id text not null,
    statement_type text,
    statement_name text,
    scope text,
    scope_name text,
    title text,
    unit text,
    scale numeric,
    currency text,
    table_indexes jsonb not null default '[]'::jsonb,
    columns jsonb not null default '[]'::jsonb,
    raw jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    primary key (parse_run_id, statement_id)
);

create index if not exists idx_pdf2md_hk_financial_statements_filing on pdf2md_hk.financial_statements (filing_id, statement_type);

create table if not exists pdf2md_hk.financial_statement_items (
    item_uid text primary key,
    filing_id text not null references pdf2md_hk.filings(filing_id) on delete cascade,
    parse_run_id text not null references pdf2md_hk.parse_runs(parse_run_id) on delete cascade,
    company_id text,
    ticker text not null,
    stock_code text,
    company_name text,
    exchange text,
    statement_id text,
    statement_type text,
    statement_name text,
    scope text,
    scope_name text,
    item_index integer,
    period_key text not null,
    item_name text,
    canonical_name text,
    value numeric,
    raw_value text,
    unit text,
    currency text,
    scale numeric,
    period_start date,
    period_end date,
    fiscal_year integer,
    fiscal_period text,
    accounting_standard text,
    industry_profile text,
    confidence numeric,
    source_page_number integer,
    source_table_index integer,
    source_row_index integer,
    source_column_index integer,
    source_bbox jsonb,
    evidence_id text references pdf2md_hk.evidence_citations(evidence_id),
    raw jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

create index if not exists idx_pdf2md_hk_statement_items_lookup on pdf2md_hk.financial_statement_items (ticker, statement_type, canonical_name, period_key);
create index if not exists idx_pdf2md_hk_statement_items_source on pdf2md_hk.financial_statement_items (filing_id, source_page_number, source_table_index);

create table if not exists pdf2md_hk.financial_key_metrics (
    like pdf2md_hk.financial_statement_items including defaults including constraints including indexes
);

create table if not exists pdf2md_hk.financial_balance_sheet_items (
    like pdf2md_hk.financial_statement_items including defaults including constraints including indexes
);

create table if not exists pdf2md_hk.financial_income_statement_items (
    like pdf2md_hk.financial_statement_items including defaults including constraints including indexes
);

create table if not exists pdf2md_hk.financial_cash_flow_statement_items (
    like pdf2md_hk.financial_statement_items including defaults including constraints including indexes
);

create table if not exists pdf2md_hk.financial_all_metrics_wide (
    parse_run_id text not null references pdf2md_hk.parse_runs(parse_run_id) on delete cascade,
    filing_id text not null references pdf2md_hk.filings(filing_id) on delete cascade,
    company_id text,
    ticker text not null,
    stock_code text,
    company_name text,
    exchange text,
    period_key text not null,
    fiscal_year integer,
    fiscal_period text,
    balance_sheet jsonb not null default '{}'::jsonb,
    income_statement jsonb not null default '{}'::jsonb,
    cash_flow_statement jsonb not null default '{}'::jsonb,
    key_metrics jsonb not null default '{}'::jsonb,
    all_metrics jsonb not null default '{}'::jsonb,
    raw jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    primary key (parse_run_id, period_key)
);

create index if not exists idx_pdf2md_hk_all_metrics_wide_lookup on pdf2md_hk.financial_all_metrics_wide (ticker, fiscal_year, fiscal_period, period_key);
create index if not exists idx_pdf2md_hk_all_metrics_wide_gin on pdf2md_hk.financial_all_metrics_wide using gin (all_metrics);

create table if not exists pdf2md_hk.operating_metric_facts (
    metric_id text primary key,
    filing_id text not null references pdf2md_hk.filings(filing_id) on delete cascade,
    parse_run_id text not null references pdf2md_hk.parse_runs(parse_run_id) on delete cascade,
    ticker text not null,
    canonical_name text not null,
    value numeric,
    raw_value text,
    unit text,
    period_key text,
    confidence numeric,
    evidence_id text references pdf2md_hk.evidence_citations(evidence_id),
    raw jsonb not null default '{}'::jsonb
);

create table if not exists pdf2md_hk.financial_checks (
    check_id text primary key,
    filing_id text not null references pdf2md_hk.filings(filing_id) on delete cascade,
    parse_run_id text not null references pdf2md_hk.parse_runs(parse_run_id) on delete cascade,
    rule_id text,
    rule_name text,
    statement_type text,
    period_key text,
    status text,
    diff numeric,
    tolerance numeric,
    raw jsonb not null default '{}'::jsonb
);

create table if not exists pdf2md_hk.quality_reports (
    parse_run_id text primary key references pdf2md_hk.parse_runs(parse_run_id) on delete cascade,
    filing_id text not null references pdf2md_hk.filings(filing_id) on delete cascade,
    overall_status text not null,
    parser_status text,
    rule_status text,
    section_count integer,
    table_count integer,
    statement_table_count integer,
    raw_cell_count integer,
    normalized_metric_count integer,
    evidence_coverage_ratio numeric,
    required_statement_status jsonb not null default '{}'::jsonb,
    critical_warnings jsonb not null default '[]'::jsonb,
    parser_warnings jsonb not null default '[]'::jsonb,
    rule_warnings jsonb not null default '[]'::jsonb,
    raw jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists idx_pdf2md_hk_quality_reports_filing on pdf2md_hk.quality_reports (filing_id, overall_status);

create table if not exists pdf2md_hk.financial_normalization_rules (
    rule_id text primary key,
    rule_type text not null,
    rule_version text not null default 'weak-v1-20260709',
    description text not null,
    preserves_raw_value boolean not null default true,
    confidence_default text,
    notes text,
    created_at timestamptz not null default now()
);

insert into pdf2md_hk.financial_normalization_rules (
    rule_id, rule_type, rule_version, description, preserves_raw_value, confidence_default, notes
) values
    ('canonical_source_xbrl', 'canonical', 'weak-v1-20260709', 'canonical label sourced from XBRL or parser mapping.', true, 'high', 'Keeps local item name and XBRL tag unchanged.'),
    ('canonical_import_fallback', 'canonical', 'weak-v1-20260709', 'canonical label sourced from import fallback mapping.', true, 'medium', 'Weak semantic label only.'),
    ('canonical_unmapped', 'canonical', 'weak-v1-20260709', 'canonical label is missing.', true, 'none', 'Use local_name/item_name for citation display.'),
    ('period_context_identity', 'period', 'weak-v1-20260709', 'period dates copied from filing context or parsed source period.', true, 'high', 'Original period_key is preserved.'),
    ('period_unparsed', 'period', 'weak-v1-20260709', 'period could not be normalized by current rules.', true, 'low', 'Review source context.'),
    ('unit_identity', 'unit', 'weak-v1-20260709', 'unit is retained without conversion.', true, 'medium', 'No currency scaling is implied.'),
    ('unit_scaled_numeric', 'unit', 'weak-v1-20260709', 'value_standardized applies the explicit numeric scale.', true, 'medium', 'Scale must be sourced from parser/XBRL metadata.'),
    ('unit_unmapped', 'unit', 'weak-v1-20260709', 'unit could not be normalized by current rules.', true, 'low', 'Avoid cross-company arithmetic until reviewed.')
on conflict (rule_id) do update set
    rule_type = excluded.rule_type,
    rule_version = excluded.rule_version,
    description = excluded.description,
    preserves_raw_value = excluded.preserves_raw_value,
    confidence_default = excluded.confidence_default,
    notes = excluded.notes;

create table if not exists pdf2md_hk.financial_items_enriched (
    enriched_id text primary key,
    source_table text not null,
    source_uid text not null,
    filing_id text not null references pdf2md_hk.filings(filing_id) on delete cascade,
    parse_run_id text not null references pdf2md_hk.parse_runs(parse_run_id) on delete cascade,
    company_id text,
    ticker text not null,
    market text not null default 'HK',
    stock_code text,
    hkex_stock_code text,
    company_name text,
    exchange text,
    industry_profile text,
    statement_id text,
    statement_type text not null,
    statement_name text,
    scope text,
    scope_name text,
    item_index integer,
    period_key_raw text not null,
    item_name_raw text,
    canonical_label text,
    canonical_source text not null default 'unmapped',
    canonical_rule_id text references pdf2md_hk.financial_normalization_rules(rule_id),
    metric_family text,
    metric_family_rule_id text,
    value_extracted numeric,
    raw_value text,
    unit_raw text,
    currency text,
    unit_standardized text,
    unit_scale numeric,
    unit_rule_id text references pdf2md_hk.financial_normalization_rules(rule_id),
    value_standardized numeric,
    period_type text,
    period_start_date date,
    period_end_date date,
    period_rule_id text references pdf2md_hk.financial_normalization_rules(rule_id),
    source_page_number integer,
    source_table_index integer,
    source_row_index integer,
    source_column_index integer,
    source_bbox jsonb,
    evidence_id text references pdf2md_hk.evidence_citations(evidence_id),
    raw_item jsonb not null default '{}'::jsonb,
    normalization_confidence text not null default 'low',
    quality_flags jsonb not null default '[]'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (source_table, source_uid)
);

create index if not exists idx_pdf2md_hk_items_enriched_lookup
    on pdf2md_hk.financial_items_enriched (ticker, statement_type, canonical_label, period_end_date);
create index if not exists idx_pdf2md_hk_items_enriched_raw_name
    on pdf2md_hk.financial_items_enriched (ticker, statement_type, item_name_raw);
create index if not exists idx_pdf2md_hk_items_enriched_source
    on pdf2md_hk.financial_items_enriched (filing_id, source_page_number, source_table_index);
create index if not exists idx_pdf2md_hk_items_enriched_flags_gin
    on pdf2md_hk.financial_items_enriched using gin (quality_flags);

create table if not exists pdf2md_hk.retrieval_chunks (
    chunk_uid text primary key,
    filing_id text not null references pdf2md_hk.filings(filing_id) on delete cascade,
    parse_run_id text references pdf2md_hk.parse_runs(parse_run_id) on delete set null,
    ticker text not null,
    collection_name text not null default 'siq_hk_reports',
    doc_type text not null,
    evidence_id text references pdf2md_hk.evidence_citations(evidence_id),
    canonical_name text,
    period_key text,
    wiki_path text,
    source_url text,
    metadata jsonb not null default '{}'::jsonb,
    text_hash text,
    embedded boolean not null default false,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists pdf2md_hk.parser_artifacts (
    parse_run_id text not null references pdf2md_hk.parse_runs(parse_run_id) on delete cascade,
    filing_id text not null references pdf2md_hk.filings(filing_id) on delete cascade,
    artifact_key text not null,
    local_path text not null,
    page_number integer,
    table_index integer,
    target text,
    schema_version text,
    raw jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    primary key (parse_run_id, artifact_key)
);

create index if not exists idx_pdf2md_hk_parser_artifacts_parse_run on pdf2md_hk.parser_artifacts (parse_run_id);
create index if not exists idx_pdf2md_hk_parser_artifacts_filing on pdf2md_hk.parser_artifacts (filing_id);
create index if not exists idx_pdf2md_hk_parser_artifacts_page_table on pdf2md_hk.parser_artifacts (page_number, table_index);

create table if not exists pdf2md_hk.content_blocks (
    block_id text primary key,
    filing_id text not null references pdf2md_hk.filings(filing_id) on delete cascade,
    parse_run_id text not null references pdf2md_hk.parse_runs(parse_run_id) on delete cascade,
    page_number integer,
    table_index integer,
    target text,
    block_type text,
    block_order integer,
    markdown_path text,
    raw jsonb not null default '{}'::jsonb
);

create index if not exists idx_pdf2md_hk_content_blocks_parse_run on pdf2md_hk.content_blocks (parse_run_id);
create index if not exists idx_pdf2md_hk_content_blocks_filing_page on pdf2md_hk.content_blocks (filing_id, page_number);
create index if not exists idx_pdf2md_hk_content_blocks_table_index on pdf2md_hk.content_blocks (table_index);

create table if not exists pdf2md_hk.footnotes (
    footnote_id text primary key,
    filing_id text not null references pdf2md_hk.filings(filing_id) on delete cascade,
    parse_run_id text not null references pdf2md_hk.parse_runs(parse_run_id) on delete cascade,
    page_number integer,
    table_index integer,
    target text,
    footnote_key text,
    content text,
    raw jsonb not null default '{}'::jsonb
);

create index if not exists idx_pdf2md_hk_footnotes_parse_run on pdf2md_hk.footnotes (parse_run_id);
create index if not exists idx_pdf2md_hk_footnotes_filing_page on pdf2md_hk.footnotes (filing_id, page_number);
create index if not exists idx_pdf2md_hk_footnotes_table_index on pdf2md_hk.footnotes (table_index);

create table if not exists pdf2md_hk.toc_entries (
    toc_entry_id text primary key,
    filing_id text not null references pdf2md_hk.filings(filing_id) on delete cascade,
    parse_run_id text not null references pdf2md_hk.parse_runs(parse_run_id) on delete cascade,
    page_number integer,
    table_index integer,
    target text,
    title text,
    level integer,
    destination_page_number integer,
    raw jsonb not null default '{}'::jsonb
);

create index if not exists idx_pdf2md_hk_toc_entries_parse_run on pdf2md_hk.toc_entries (parse_run_id);
create index if not exists idx_pdf2md_hk_toc_entries_filing_page on pdf2md_hk.toc_entries (filing_id, page_number);
create index if not exists idx_pdf2md_hk_toc_entries_table_index on pdf2md_hk.toc_entries (table_index);

create table if not exists pdf2md_hk.financial_note_links (
    link_id text primary key,
    filing_id text not null references pdf2md_hk.filings(filing_id) on delete cascade,
    parse_run_id text not null references pdf2md_hk.parse_runs(parse_run_id) on delete cascade,
    page_number integer,
    table_index integer,
    target text,
    note_key text,
    note_target text,
    raw jsonb not null default '{}'::jsonb
);

create index if not exists idx_pdf2md_hk_financial_note_links_parse_run on pdf2md_hk.financial_note_links (parse_run_id);
create index if not exists idx_pdf2md_hk_financial_note_links_filing_page on pdf2md_hk.financial_note_links (filing_id, page_number);
create index if not exists idx_pdf2md_hk_financial_note_links_table_index on pdf2md_hk.financial_note_links (table_index);

create table if not exists pdf2md_hk.table_relations (
    relation_id text primary key,
    filing_id text not null references pdf2md_hk.filings(filing_id) on delete cascade,
    parse_run_id text not null references pdf2md_hk.parse_runs(parse_run_id) on delete cascade,
    page_number integer,
    table_index integer,
    target text,
    related_table_id text,
    relation_type text,
    raw jsonb not null default '{}'::jsonb
);

create index if not exists idx_pdf2md_hk_table_relations_parse_run on pdf2md_hk.table_relations (parse_run_id);
create index if not exists idx_pdf2md_hk_table_relations_filing_page on pdf2md_hk.table_relations (filing_id, page_number);
create index if not exists idx_pdf2md_hk_table_relations_table_index on pdf2md_hk.table_relations (table_index);

create table if not exists pdf2md_hk.table_quality_signals (
    signal_id text primary key,
    filing_id text not null references pdf2md_hk.filings(filing_id) on delete cascade,
    parse_run_id text not null references pdf2md_hk.parse_runs(parse_run_id) on delete cascade,
    page_number integer,
    table_index integer,
    target text,
    signal_type text,
    signal_value text,
    raw jsonb not null default '{}'::jsonb
);

create index if not exists idx_pdf2md_hk_table_quality_signals_parse_run on pdf2md_hk.table_quality_signals (parse_run_id);
create index if not exists idx_pdf2md_hk_table_quality_signals_filing_page on pdf2md_hk.table_quality_signals (filing_id, page_number);
create index if not exists idx_pdf2md_hk_table_quality_signals_table_index on pdf2md_hk.table_quality_signals (table_index);

create or replace view pdf2md_hk.v_latest_parse_runs as
select distinct on (f.filing_id)
    f.*,
    pr.parse_run_id,
    pr.completed_at,
    pr.status,
    pr.status as parse_status,
    pr.wiki_package_path
from pdf2md_hk.filings f
join pdf2md_hk.parse_runs pr on pr.filing_id = f.filing_id
where pr.status in ('pass', 'warning', 'completed', 'success')
order by f.filing_id, pr.completed_at desc nulls last, pr.parse_run_id desc;

alter table pdf2md_hk.companies add column if not exists stock_code text;
alter table pdf2md_hk.companies add column if not exists hkex_stock_code text;
alter table pdf2md_hk.companies add column if not exists exchange text;
alter table pdf2md_hk.companies add column if not exists short_name text;
alter table pdf2md_hk.companies add column if not exists company_short_name text;
alter table pdf2md_hk.companies add column if not exists company_name_en text;
alter table pdf2md_hk.companies add column if not exists company_name_zh text;
alter table pdf2md_hk.companies add column if not exists aliases jsonb not null default '[]'::jsonb;
alter table pdf2md_hk.companies add column if not exists industry_profile text default 'general';
alter table pdf2md_hk.filings add column if not exists stock_code text;
alter table pdf2md_hk.filings add column if not exists report_id text;
alter table pdf2md_hk.filings add column if not exists accession_number text;
alter table pdf2md_hk.pdf_tables add column if not exists bbox jsonb;
alter table pdf2md_hk.pdf_tables add column if not exists source_format text;
alter table pdf2md_hk.pdf_tables add column if not exists document_format text;
alter table pdf2md_hk.evidence_citations add column if not exists bbox jsonb;
alter table pdf2md_hk.retrieval_chunks add column if not exists company_id text;
alter table pdf2md_hk.retrieval_chunks add column if not exists section_title text;
alter table pdf2md_hk.retrieval_chunks add column if not exists statement_type text;
alter table pdf2md_hk.retrieval_chunks add column if not exists page_number integer;
alter table pdf2md_hk.retrieval_chunks add column if not exists table_index integer;
alter table pdf2md_hk.retrieval_chunks add column if not exists text text;

create unique index if not exists uq_pdf2md_hk_companies_hkex_stock_code on pdf2md_hk.companies (hkex_stock_code) where hkex_stock_code is not null and hkex_stock_code <> '';
create index if not exists idx_pdf2md_hk_companies_aliases_gin on pdf2md_hk.companies using gin (aliases);
create index if not exists idx_pdf2md_hk_filings_company_year on pdf2md_hk.filings (company_id, fiscal_year desc, report_type);
create index if not exists idx_pdf2md_hk_retrieval_chunks_agent on pdf2md_hk.retrieval_chunks (company_id, doc_type, canonical_name, period_key);
create index if not exists idx_pdf2md_hk_stmt_items_company_year on pdf2md_hk.financial_statement_items (company_id, fiscal_year, canonical_name, period_key);

alter table pdf2md_hk.financial_facts add column if not exists fact_currency text;
alter table pdf2md_hk.financial_facts add column if not exists reporting_currency text;
alter table pdf2md_hk.financial_facts add column if not exists presentation_currency text;
alter table pdf2md_hk.financial_facts add column if not exists converted_currency text;
alter table pdf2md_hk.financial_facts add column if not exists converted_value numeric;
alter table pdf2md_hk.financial_facts add column if not exists fx_rate_date date;
alter table pdf2md_hk.financial_facts add column if not exists fx_rate_source text;
alter table pdf2md_hk.financial_statement_items add column if not exists fact_currency text;
alter table pdf2md_hk.financial_statement_items add column if not exists reporting_currency text;
alter table pdf2md_hk.financial_statement_items add column if not exists presentation_currency text;
alter table pdf2md_hk.financial_statement_items add column if not exists converted_currency text;
alter table pdf2md_hk.financial_statement_items add column if not exists converted_value numeric;
alter table pdf2md_hk.financial_statement_items add column if not exists fx_rate_date date;
alter table pdf2md_hk.financial_statement_items add column if not exists fx_rate_source text;
alter table pdf2md_hk.financial_key_metrics add column if not exists fact_currency text;
alter table pdf2md_hk.financial_key_metrics add column if not exists reporting_currency text;
alter table pdf2md_hk.financial_key_metrics add column if not exists presentation_currency text;
alter table pdf2md_hk.financial_key_metrics add column if not exists converted_currency text;
alter table pdf2md_hk.financial_key_metrics add column if not exists converted_value numeric;
alter table pdf2md_hk.financial_key_metrics add column if not exists fx_rate_date date;
alter table pdf2md_hk.financial_key_metrics add column if not exists fx_rate_source text;
alter table pdf2md_hk.financial_balance_sheet_items add column if not exists fact_currency text;
alter table pdf2md_hk.financial_balance_sheet_items add column if not exists reporting_currency text;
alter table pdf2md_hk.financial_balance_sheet_items add column if not exists presentation_currency text;
alter table pdf2md_hk.financial_balance_sheet_items add column if not exists converted_currency text;
alter table pdf2md_hk.financial_balance_sheet_items add column if not exists converted_value numeric;
alter table pdf2md_hk.financial_balance_sheet_items add column if not exists fx_rate_date date;
alter table pdf2md_hk.financial_balance_sheet_items add column if not exists fx_rate_source text;
alter table pdf2md_hk.financial_income_statement_items add column if not exists fact_currency text;
alter table pdf2md_hk.financial_income_statement_items add column if not exists reporting_currency text;
alter table pdf2md_hk.financial_income_statement_items add column if not exists presentation_currency text;
alter table pdf2md_hk.financial_income_statement_items add column if not exists converted_currency text;
alter table pdf2md_hk.financial_income_statement_items add column if not exists converted_value numeric;
alter table pdf2md_hk.financial_income_statement_items add column if not exists fx_rate_date date;
alter table pdf2md_hk.financial_income_statement_items add column if not exists fx_rate_source text;
alter table pdf2md_hk.financial_cash_flow_statement_items add column if not exists fact_currency text;
alter table pdf2md_hk.financial_cash_flow_statement_items add column if not exists reporting_currency text;
alter table pdf2md_hk.financial_cash_flow_statement_items add column if not exists presentation_currency text;
alter table pdf2md_hk.financial_cash_flow_statement_items add column if not exists converted_currency text;
alter table pdf2md_hk.financial_cash_flow_statement_items add column if not exists converted_value numeric;
alter table pdf2md_hk.financial_cash_flow_statement_items add column if not exists fx_rate_date date;
alter table pdf2md_hk.financial_cash_flow_statement_items add column if not exists fx_rate_source text;
alter table pdf2md_hk.financial_items_enriched add column if not exists fact_currency text;
alter table pdf2md_hk.financial_items_enriched add column if not exists reporting_currency text;
alter table pdf2md_hk.financial_items_enriched add column if not exists presentation_currency text;
alter table pdf2md_hk.financial_items_enriched add column if not exists converted_currency text;
alter table pdf2md_hk.financial_items_enriched add column if not exists converted_value numeric;
alter table pdf2md_hk.financial_items_enriched add column if not exists fx_rate_date date;
alter table pdf2md_hk.financial_items_enriched add column if not exists fx_rate_source text;

drop view if exists pdf2md_hk.v_agent_financial_facts cascade;
drop view if exists pdf2md_hk.v_agent_financial_facts cascade;
create or replace view pdf2md_hk.v_agent_financial_facts as
select
    c.company_id,
    c.ticker as company_ticker,
    c.stock_code,
    c.hkex_stock_code,
    c.company_name,
    c.company_short_name,
    c.company_name_en,
    c.company_name_zh,
    f.filing_id,
    f.report_id,
    f.accession_number,
    f.report_type,
    f.fiscal_year,
    f.fiscal_period,
    f.period_end as filing_period_end,
    f.published_at,
    pr.parse_run_id,
    pr.completed_at as parse_completed_at,
    pr.wiki_package_path,
    fsi.item_uid,
    fsi.statement_id,
    fsi.statement_type,
    fsi.statement_name,
    fsi.item_index,
    fsi.canonical_name,
    fsi.item_name,
    fsi.period_key,
    fsi.period_start,
    fsi.period_end,
    fsi.value,
    fsi.raw_value,
    fsi.unit,
    fsi.currency,
    fsi.scale,
    fsi.confidence,
    coalesce(ec.evidence_id, fsi.evidence_id) as evidence_id,
    coalesce(ec.page_number, fsi.source_page_number) as evidence_page_number,
    coalesce(ec.table_index, fsi.source_table_index) as evidence_table_index,
    coalesce(ec.row_index, fsi.source_row_index) as evidence_row_index,
    coalesce(ec.column_index, fsi.source_column_index) as evidence_column_index,
    coalesce(ec.bbox, fsi.source_bbox) as evidence_bbox,
    ec.quote_text,
    coalesce(ec.source_url, f.source_url) as source_url,
    fsi.raw
from pdf2md_hk.financial_statement_items fsi
join pdf2md_hk.filings f on f.filing_id = fsi.filing_id
join pdf2md_hk.companies c on c.company_id = f.company_id
join pdf2md_hk.v_latest_parse_runs pr on pr.parse_run_id = fsi.parse_run_id
left join pdf2md_hk.evidence_citations ec on ec.evidence_id = fsi.evidence_id;

create or replace view pdf2md_hk.v_latest_company_reports as
select distinct on (f.company_id, f.report_type)
    c.company_id,
    c.ticker as company_ticker,
    c.stock_code,
    c.hkex_stock_code,
    c.company_name,
    c.company_short_name,
    c.company_name_en,
    c.company_name_zh,
    f.filing_id,
    f.report_id,
    f.accession_number,
    f.report_type,
    f.fiscal_year,
    f.fiscal_period,
    f.period_end,
    f.published_at,
    f.source_url,
    f.local_path,
    f.quality_status,
    pr.parse_run_id,
    pr.parser_version,
    pr.rules_version,
    pr.status as parse_status,
    pr.completed_at,
    pr.wiki_package_path
from pdf2md_hk.filings f
join pdf2md_hk.companies c on c.company_id = f.company_id
join pdf2md_hk.parse_runs pr on pr.filing_id = f.filing_id
order by f.company_id, f.report_type, f.period_end desc nulls last, f.fiscal_year desc nulls last, pr.completed_at desc nulls last, pr.parse_run_id desc;

create index if not exists idx_pdf2md_hk_evidence_citations_parse_run on pdf2md_hk.evidence_citations (parse_run_id);
create index if not exists idx_pdf2md_hk_financial_facts_parse_run on pdf2md_hk.financial_facts (parse_run_id);
create index if not exists idx_pdf2md_hk_operating_metric_facts_parse_run on pdf2md_hk.operating_metric_facts (parse_run_id);
create index if not exists idx_pdf2md_hk_retrieval_chunks_parse_run on pdf2md_hk.retrieval_chunks (parse_run_id);
create index if not exists idx_pdf2md_hk_financial_statement_items_parse_run on pdf2md_hk.financial_statement_items (parse_run_id);
create index if not exists idx_pdf2md_hk_financial_key_metrics_parse_run on pdf2md_hk.financial_key_metrics (parse_run_id);
create index if not exists idx_pdf2md_hk_financial_balance_sheet_items_parse_run on pdf2md_hk.financial_balance_sheet_items (parse_run_id);
create index if not exists idx_pdf2md_hk_financial_income_statement_items_parse_run on pdf2md_hk.financial_income_statement_items (parse_run_id);
create index if not exists idx_pdf2md_hk_financial_cash_flow_statement_items_parse_run on pdf2md_hk.financial_cash_flow_statement_items (parse_run_id);
create index if not exists idx_pdf2md_hk_financial_checks_parse_run on pdf2md_hk.financial_checks (parse_run_id);
create index if not exists idx_pdf2md_hk_financial_items_enriched_parse_run on pdf2md_hk.financial_items_enriched (parse_run_id);

drop view if exists pdf2md_hk.v_agent_financial_facts cascade;
create or replace view pdf2md_hk.v_agent_financial_facts as
with agent_items as (
    select
        item_uid, filing_id, parse_run_id, statement_id, statement_type, statement_name,
        item_index, period_key, item_name, canonical_name, value, raw_value, unit, currency,
        fact_currency, reporting_currency, presentation_currency, converted_currency,
        converted_value, fx_rate_date, fx_rate_source, scale, period_start, period_end,
        confidence, source_page_number, source_table_index, source_row_index,
        source_column_index, source_bbox, evidence_id, raw
    from pdf2md_hk.financial_statement_items
    union all
    select
        item_uid, filing_id, parse_run_id, statement_id, statement_type, statement_name,
        item_index, period_key, item_name, canonical_name, value, raw_value, unit, currency,
        fact_currency, reporting_currency, presentation_currency, converted_currency,
        converted_value, fx_rate_date, fx_rate_source, scale, period_start, period_end,
        confidence, source_page_number, source_table_index, source_row_index,
        source_column_index, source_bbox, evidence_id, raw
    from pdf2md_hk.financial_key_metrics
)
select
    c.company_id,
    c.ticker as company_ticker,
    c.stock_code,
    c.hkex_stock_code,
    c.company_name,
    c.company_short_name,
    c.company_name_en,
    c.company_name_zh,
    f.filing_id,
    f.report_id,
    f.accession_number,
    f.report_type,
    f.fiscal_year,
    f.fiscal_period,
    f.period_end as filing_period_end,
    f.published_at,
    pr.parse_run_id,
    pr.completed_at as parse_completed_at,
    pr.wiki_package_path,
    fsi.item_uid,
    fsi.statement_id,
    fsi.statement_type,
    fsi.statement_name,
    fsi.item_index,
    fsi.canonical_name,
    fsi.canonical_name as canonical_label,
    fsi.item_name,
    fsi.item_name as item_name_raw,
    null::text as local_name,
    null::text as metric_name,
    null::text as metric_name_raw,
    null::text as label,
    null::text as concept,
    null::text as xbrl_tag,
    null::text as taxonomy_tag,
    null::text as context_ref,
    fsi.period_key,
    fsi.period_start,
    fsi.period_end,
    fsi.value,
    fsi.raw_value,
    fsi.unit,
    fsi.currency,
    fsi.fact_currency,
    fsi.reporting_currency,
    fsi.presentation_currency,
    fsi.converted_currency,
    fsi.converted_value,
    fsi.fx_rate_date,
    fsi.fx_rate_source,
    fsi.scale,
    fsi.confidence,
    coalesce(ec.evidence_id, fsi.evidence_id) as evidence_id,
    coalesce(ec.page_number, fsi.source_page_number) as evidence_page_number,
    coalesce(ec.table_index, fsi.source_table_index) as evidence_table_index,
    coalesce(ec.row_index, fsi.source_row_index) as evidence_row_index,
    coalesce(ec.column_index, fsi.source_column_index) as evidence_column_index,
    coalesce(ec.bbox, fsi.source_bbox) as evidence_bbox,
    ec.quote_text,
    coalesce(ec.source_url, f.source_url) as source_url,
    fsi.raw
from agent_items fsi
join pdf2md_hk.filings f on f.filing_id = fsi.filing_id
join pdf2md_hk.companies c on c.company_id = f.company_id
join pdf2md_hk.v_latest_parse_runs pr on pr.parse_run_id = fsi.parse_run_id
left join pdf2md_hk.evidence_citations ec on ec.evidence_id = fsi.evidence_id;
