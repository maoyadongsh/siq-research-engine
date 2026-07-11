create schema if not exists dart_kr;

drop view if exists dart_kr.v_latest_company_reports cascade;
drop view if exists dart_kr.v_agent_financial_facts cascade;
drop view if exists dart_kr.v_latest_parse_runs cascade;

create table if not exists dart_kr.companies (
    company_id text primary key,
    corp_code text,
    stock_code text,
    ticker text not null,
    company_name text,
    raw jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists idx_dart_kr_companies_ticker on dart_kr.companies (ticker);
create index if not exists idx_dart_kr_companies_corp_code on dart_kr.companies (corp_code);

create table if not exists dart_kr.filings (
    filing_id text primary key,
    company_id text not null references dart_kr.companies(company_id),
    ticker text not null,
    rcp_no text,
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

create index if not exists idx_dart_kr_filings_ticker_year on dart_kr.filings (ticker, fiscal_year, report_type);
create index if not exists idx_dart_kr_filings_rcp_no on dart_kr.filings (rcp_no);

create table if not exists dart_kr.parse_runs (
    parse_run_id text primary key,
    filing_id text not null references dart_kr.filings(filing_id) on delete cascade,
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

create table if not exists dart_kr.artifacts (
    parse_run_id text not null references dart_kr.parse_runs(parse_run_id) on delete cascade,
    artifact_type text not null,
    local_path text not null,
    sha256 text,
    size_bytes bigint,
    raw jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    primary key (parse_run_id, artifact_type)
);

create table if not exists dart_kr.raw_payload_refs (
    payload_ref_id text primary key,
    filing_id text not null references dart_kr.filings(filing_id) on delete cascade,
    parse_run_id text not null references dart_kr.parse_runs(parse_run_id) on delete cascade,
    payload_name text not null,
    local_path text,
    sha256 text,
    size_bytes bigint,
    summary jsonb not null default '{}'::jsonb,
    raw jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

create index if not exists idx_dart_kr_raw_payload_refs_parse_run on dart_kr.raw_payload_refs (parse_run_id);
create index if not exists idx_dart_kr_raw_payload_refs_sha256 on dart_kr.raw_payload_refs (sha256);

create table if not exists dart_kr.xbrl_facts_raw (
    fact_id text primary key,
    parse_run_id text not null references dart_kr.parse_runs(parse_run_id) on delete cascade,
    filing_id text not null references dart_kr.filings(filing_id) on delete cascade,
    concept text not null,
    value_text text,
    value_numeric numeric,
    unit text,
    context_ref text,
    period_start date,
    period_end date,
    instant date,
    duration_days integer,
    dimensions jsonb not null default '{}'::jsonb,
    source_type text,
    source_file text,
    raw jsonb not null default '{}'::jsonb
);

create index if not exists idx_dart_kr_xbrl_facts_filing_concept on dart_kr.xbrl_facts_raw (filing_id, concept);

create table if not exists dart_kr.pdf_tables (
    parse_run_id text not null references dart_kr.parse_runs(parse_run_id) on delete cascade,
    filing_id text not null references dart_kr.filings(filing_id) on delete cascade,
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

create table if not exists dart_kr.evidence_citations (
    evidence_id text primary key,
    filing_id text not null references dart_kr.filings(filing_id) on delete cascade,
    parse_run_id text not null references dart_kr.parse_runs(parse_run_id) on delete cascade,
    source_type text not null,
    source_id text,
    xbrl_tag text,
    context_ref text,
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

create table if not exists dart_kr.financial_facts (
    metric_id text primary key,
    filing_id text not null references dart_kr.filings(filing_id) on delete cascade,
    parse_run_id text not null references dart_kr.parse_runs(parse_run_id) on delete cascade,
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
    evidence_id text references dart_kr.evidence_citations(evidence_id),
    raw_fact_id text references dart_kr.xbrl_facts_raw(fact_id),
    xbrl_tag text,
    context_ref text,
    raw jsonb not null default '{}'::jsonb
);

create index if not exists idx_dart_kr_financial_facts_ticker_metric_period on dart_kr.financial_facts (ticker, canonical_name, period_key);

create table if not exists dart_kr.operating_metric_facts (
    metric_id text primary key,
    filing_id text not null references dart_kr.filings(filing_id) on delete cascade,
    parse_run_id text not null references dart_kr.parse_runs(parse_run_id) on delete cascade,
    ticker text not null,
    canonical_name text not null,
    value numeric,
    unit text,
    period_key text,
    confidence numeric,
    evidence_id text references dart_kr.evidence_citations(evidence_id),
    raw jsonb not null default '{}'::jsonb
);

create table if not exists dart_kr.financial_checks (
    check_id text primary key,
    filing_id text not null references dart_kr.filings(filing_id) on delete cascade,
    parse_run_id text not null references dart_kr.parse_runs(parse_run_id) on delete cascade,
    rule_id text,
    rule_name text,
    statement_type text,
    period_key text,
    status text,
    diff numeric,
    tolerance numeric,
    raw jsonb not null default '{}'::jsonb
);

create table if not exists dart_kr.retrieval_chunks (
    chunk_uid text primary key,
    filing_id text not null references dart_kr.filings(filing_id) on delete cascade,
    parse_run_id text references dart_kr.parse_runs(parse_run_id) on delete set null,
    ticker text not null,
    collection_name text not null default 'siq_kr_reports',
    doc_type text not null,
    evidence_id text references dart_kr.evidence_citations(evidence_id),
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

create index if not exists idx_dart_kr_parse_runs_filing on dart_kr.parse_runs (filing_id, completed_at desc);
create index if not exists idx_dart_kr_parse_runs_status on dart_kr.parse_runs (status);
create index if not exists idx_dart_kr_artifacts_sha256 on dart_kr.artifacts (sha256);
create index if not exists idx_dart_kr_artifacts_local_path on dart_kr.artifacts (local_path);
create index if not exists idx_dart_kr_xbrl_facts_context on dart_kr.xbrl_facts_raw (context_ref);
create index if not exists idx_dart_kr_xbrl_facts_period_end on dart_kr.xbrl_facts_raw (period_end);
create index if not exists idx_dart_kr_xbrl_facts_dimensions on dart_kr.xbrl_facts_raw using gin (dimensions);
create index if not exists idx_dart_kr_pdf_tables_location on dart_kr.pdf_tables (filing_id, page_number, table_index);
create index if not exists idx_dart_kr_evidence_location on dart_kr.evidence_citations (filing_id, source_type, page_number, table_index);
create index if not exists idx_dart_kr_evidence_xbrl on dart_kr.evidence_citations (filing_id, xbrl_tag, context_ref);
create index if not exists idx_dart_kr_financial_facts_filing_statement on dart_kr.financial_facts (filing_id, statement_type);
create index if not exists idx_dart_kr_financial_facts_evidence on dart_kr.financial_facts (evidence_id);
create index if not exists idx_dart_kr_retrieval_chunks_filing on dart_kr.retrieval_chunks (filing_id, doc_type);
create index if not exists idx_dart_kr_retrieval_chunks_collection on dart_kr.retrieval_chunks (collection_name, embedded);

alter table dart_kr.companies add column if not exists company_name_en text;
alter table dart_kr.companies add column if not exists company_name_ko text;
alter table dart_kr.companies add column if not exists short_name text;
alter table dart_kr.companies add column if not exists exchange text;
alter table dart_kr.companies add column if not exists industry_profile text default 'general';
alter table dart_kr.companies add column if not exists aliases jsonb not null default '[]'::jsonb;

alter table dart_kr.filings add column if not exists accession_number text;
alter table dart_kr.filings add column if not exists landing_url text;
alter table dart_kr.filings add column if not exists document_format text;

alter table dart_kr.pdf_tables add column if not exists unit text;
alter table dart_kr.pdf_tables add column if not exists currency text;
alter table dart_kr.pdf_tables add column if not exists bbox jsonb;
alter table dart_kr.pdf_tables add column if not exists source_format text;
alter table dart_kr.pdf_tables add column if not exists document_format text;

alter table dart_kr.evidence_citations add column if not exists unit_ref text;
alter table dart_kr.evidence_citations add column if not exists fact_id text;
alter table dart_kr.evidence_citations add column if not exists html_anchor text;
alter table dart_kr.evidence_citations add column if not exists xpath text;
alter table dart_kr.evidence_citations add column if not exists bbox jsonb;

alter table dart_kr.xbrl_facts_raw add column if not exists taxonomy text;
alter table dart_kr.xbrl_facts_raw add column if not exists label text;
alter table dart_kr.xbrl_facts_raw add column if not exists unit_ref text;
alter table dart_kr.xbrl_facts_raw add column if not exists decimals text;
alter table dart_kr.xbrl_facts_raw add column if not exists scale text;
alter table dart_kr.xbrl_facts_raw add column if not exists fiscal_year integer;
alter table dart_kr.xbrl_facts_raw add column if not exists fiscal_period text;
alter table dart_kr.xbrl_facts_raw add column if not exists frame text;
alter table dart_kr.xbrl_facts_raw add column if not exists is_extension boolean default false;
alter table dart_kr.xbrl_facts_raw add column if not exists html_anchor text;
alter table dart_kr.xbrl_facts_raw add column if not exists xpath text;

alter table dart_kr.financial_facts add column if not exists duration_days integer;
alter table dart_kr.financial_facts add column if not exists qtd_ytd_type text;
alter table dart_kr.financial_facts add column if not exists segment_key text;
alter table dart_kr.financial_facts add column if not exists dimensions jsonb not null default '{}'::jsonb;
alter table dart_kr.financial_facts add column if not exists scale text;
alter table dart_kr.financial_facts add column if not exists source_type text;

alter table dart_kr.operating_metric_facts add column if not exists raw_value text;
alter table dart_kr.operating_metric_facts add column if not exists period_start date;
alter table dart_kr.operating_metric_facts add column if not exists period_end date;
alter table dart_kr.operating_metric_facts add column if not exists fiscal_year integer;
alter table dart_kr.operating_metric_facts add column if not exists fiscal_period text;
alter table dart_kr.operating_metric_facts add column if not exists source_type text;

alter table dart_kr.retrieval_chunks add column if not exists company_id text;
alter table dart_kr.retrieval_chunks add column if not exists batch_tag text;
alter table dart_kr.retrieval_chunks add column if not exists evidence_level text;
alter table dart_kr.retrieval_chunks add column if not exists section_id text;
alter table dart_kr.retrieval_chunks add column if not exists section_title text;
alter table dart_kr.retrieval_chunks add column if not exists table_id text;
alter table dart_kr.retrieval_chunks add column if not exists statement_type text;
alter table dart_kr.retrieval_chunks add column if not exists page_number integer;
alter table dart_kr.retrieval_chunks add column if not exists table_index integer;
alter table dart_kr.retrieval_chunks add column if not exists concept text;
alter table dart_kr.retrieval_chunks add column if not exists segment_key text;
alter table dart_kr.retrieval_chunks add column if not exists dimensions jsonb not null default '{}'::jsonb;
alter table dart_kr.retrieval_chunks add column if not exists raw_fact_id text;
alter table dart_kr.retrieval_chunks add column if not exists text text;

create index if not exists idx_dart_kr_companies_aliases_gin on dart_kr.companies using gin (aliases);
create index if not exists idx_dart_kr_retrieval_chunks_dimensions on dart_kr.retrieval_chunks using gin (dimensions);

create table if not exists dart_kr.filing_sections (
    parse_run_id text not null references dart_kr.parse_runs(parse_run_id) on delete cascade,
    filing_id text not null references dart_kr.filings(filing_id) on delete cascade,
    section_id text not null,
    section_title text,
    section_order integer,
    markdown_path text,
    html_anchor text,
    xpath text,
    line_start integer,
    line_end integer,
    char_start integer,
    char_end integer,
    text_hash text,
    raw jsonb not null default '{}'::jsonb,
    primary key (parse_run_id, section_id)
);

create index if not exists idx_dart_kr_filing_sections_filing on dart_kr.filing_sections (filing_id, section_order);

create table if not exists dart_kr.pdf_pages (
    parse_run_id text not null references dart_kr.parse_runs(parse_run_id) on delete cascade,
    filing_id text not null references dart_kr.filings(filing_id) on delete cascade,
    page_number integer not null,
    markdown_path text,
    image_path text,
    raw jsonb not null default '{}'::jsonb,
    primary key (parse_run_id, page_number)
);

create table if not exists dart_kr.html_tables (
    parse_run_id text not null references dart_kr.parse_runs(parse_run_id) on delete cascade,
    filing_id text not null references dart_kr.filings(filing_id) on delete cascade,
    table_id text not null,
    section_id text,
    html_anchor text,
    xpath text,
    table_index integer,
    title text,
    row_count integer,
    column_count integer,
    table_json_path text,
    unit text,
    currency text,
    is_financial_statement_candidate boolean default false,
    raw jsonb not null default '{}'::jsonb,
    primary key (parse_run_id, table_id)
);

create index if not exists idx_dart_kr_html_tables_anchor on dart_kr.html_tables (filing_id, html_anchor);
alter table dart_kr.html_tables add column if not exists source_format text;
alter table dart_kr.html_tables add column if not exists document_format text;

create table if not exists dart_kr.xbrl_contexts (
    context_uid text primary key,
    parse_run_id text not null references dart_kr.parse_runs(parse_run_id) on delete cascade,
    filing_id text not null references dart_kr.filings(filing_id) on delete cascade,
    context_ref text not null,
    entity_identifier text,
    period_start date,
    period_end date,
    instant date,
    duration_days integer,
    dimensions jsonb not null default '{}'::jsonb,
    raw jsonb not null default '{}'::jsonb,
    unique (parse_run_id, context_ref)
);

create index if not exists idx_dart_kr_xbrl_contexts_filing_ref on dart_kr.xbrl_contexts (filing_id, context_ref);
create index if not exists idx_dart_kr_xbrl_contexts_dimensions on dart_kr.xbrl_contexts using gin (dimensions);

create table if not exists dart_kr.xbrl_units (
    unit_uid text primary key,
    parse_run_id text not null references dart_kr.parse_runs(parse_run_id) on delete cascade,
    filing_id text not null references dart_kr.filings(filing_id) on delete cascade,
    unit_ref text not null,
    unit text,
    measure text,
    numerator jsonb not null default '[]'::jsonb,
    denominator jsonb not null default '[]'::jsonb,
    raw jsonb not null default '{}'::jsonb,
    unique (parse_run_id, unit_ref)
);

create index if not exists idx_dart_kr_xbrl_units_filing_ref on dart_kr.xbrl_units (filing_id, unit_ref);

create table if not exists dart_kr.financial_statements (
    parse_run_id text not null references dart_kr.parse_runs(parse_run_id) on delete cascade,
    filing_id text not null references dart_kr.filings(filing_id) on delete cascade,
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

create index if not exists idx_dart_kr_financial_statements_filing on dart_kr.financial_statements (filing_id, statement_type);

create table if not exists dart_kr.financial_statement_items (
    item_uid text primary key,
    filing_id text not null references dart_kr.filings(filing_id) on delete cascade,
    parse_run_id text not null references dart_kr.parse_runs(parse_run_id) on delete cascade,
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
    local_name text,
    xbrl_tag text,
    context_ref text,
    value numeric,
    raw_value text,
    unit text,
    currency text,
    scale numeric,
    period_start date,
    period_end date,
    duration_days integer,
    fiscal_year integer,
    fiscal_period text,
    accounting_standard text,
    industry_profile text,
    segment_key text,
    dimensions jsonb not null default '{}'::jsonb,
    confidence numeric,
    source_page_number integer,
    source_table_index integer,
    source_row_index integer,
    source_column_index integer,
    source_bbox jsonb,
    source_type text,
    evidence_id text references dart_kr.evidence_citations(evidence_id),
    raw_fact_id text references dart_kr.xbrl_facts_raw(fact_id),
    raw jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

create index if not exists idx_dart_kr_statement_items_lookup on dart_kr.financial_statement_items (ticker, statement_type, canonical_name, period_key);
create index if not exists idx_dart_kr_statement_items_source on dart_kr.financial_statement_items (filing_id, source_page_number, source_table_index);
create index if not exists idx_dart_kr_statement_items_dimensions on dart_kr.financial_statement_items using gin (dimensions);

create table if not exists dart_kr.financial_key_metrics (
    like dart_kr.financial_statement_items including defaults including constraints including indexes
);

create table if not exists dart_kr.financial_balance_sheet_items (
    like dart_kr.financial_statement_items including defaults including constraints including indexes
);

create table if not exists dart_kr.financial_income_statement_items (
    like dart_kr.financial_statement_items including defaults including constraints including indexes
);

create table if not exists dart_kr.financial_cash_flow_statement_items (
    like dart_kr.financial_statement_items including defaults including constraints including indexes
);

create table if not exists dart_kr.financial_all_metrics_wide (
    parse_run_id text not null references dart_kr.parse_runs(parse_run_id) on delete cascade,
    filing_id text not null references dart_kr.filings(filing_id) on delete cascade,
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

create index if not exists idx_dart_kr_all_metrics_wide_lookup on dart_kr.financial_all_metrics_wide (ticker, fiscal_year, fiscal_period, period_key);
create index if not exists idx_dart_kr_all_metrics_wide_gin on dart_kr.financial_all_metrics_wide using gin (all_metrics);

create table if not exists dart_kr.quality_reports (
    parse_run_id text primary key references dart_kr.parse_runs(parse_run_id) on delete cascade,
    filing_id text not null references dart_kr.filings(filing_id) on delete cascade,
    overall_status text not null,
    parser_status text,
    rule_status text,
    section_count integer,
    table_count integer,
    statement_table_count integer,
    raw_cell_count integer,
    raw_fact_count integer,
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

create index if not exists idx_dart_kr_quality_reports_filing on dart_kr.quality_reports (filing_id, overall_status);

create table if not exists dart_kr.financial_normalization_rules (
    rule_id text primary key,
    rule_type text not null,
    rule_version text not null default 'weak-v1-20260709',
    description text not null,
    preserves_raw_value boolean not null default true,
    confidence_default text,
    notes text,
    created_at timestamptz not null default now()
);

insert into dart_kr.financial_normalization_rules (
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

create table if not exists dart_kr.financial_items_enriched (
    enriched_id text primary key,
    source_table text not null,
    source_uid text not null,
    filing_id text not null references dart_kr.filings(filing_id) on delete cascade,
    parse_run_id text not null references dart_kr.parse_runs(parse_run_id) on delete cascade,
    company_id text,
    ticker text not null,
    market text not null default 'KR',
    stock_code text,
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
    canonical_rule_id text references dart_kr.financial_normalization_rules(rule_id),
    metric_family text,
    metric_family_rule_id text,
    value_extracted numeric,
    raw_value text,
    unit_raw text,
    currency text,
    unit_standardized text,
    unit_scale numeric,
    unit_rule_id text references dart_kr.financial_normalization_rules(rule_id),
    value_standardized numeric,
    period_type text,
    period_start_date date,
    period_end_date date,
    period_rule_id text references dart_kr.financial_normalization_rules(rule_id),
    source_page_number integer,
    source_table_index integer,
    source_row_index integer,
    source_column_index integer,
    source_bbox jsonb,
    evidence_id text references dart_kr.evidence_citations(evidence_id),
    xbrl_tag text,
    context_ref text,
    raw_fact_id text references dart_kr.xbrl_facts_raw(fact_id),
    raw_item jsonb not null default '{}'::jsonb,
    normalization_confidence text not null default 'low',
    quality_flags jsonb not null default '[]'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (source_table, source_uid)
);

create index if not exists idx_dart_kr_items_enriched_lookup
    on dart_kr.financial_items_enriched (ticker, statement_type, canonical_label, period_end_date);
create index if not exists idx_dart_kr_items_enriched_raw_name
    on dart_kr.financial_items_enriched (ticker, statement_type, item_name_raw);
create index if not exists idx_dart_kr_items_enriched_source
    on dart_kr.financial_items_enriched (filing_id, source_page_number, source_table_index);
create index if not exists idx_dart_kr_items_enriched_flags_gin
    on dart_kr.financial_items_enriched using gin (quality_flags);

alter table dart_kr.financial_facts add column if not exists fact_currency text;
alter table dart_kr.financial_facts add column if not exists reporting_currency text;
alter table dart_kr.financial_facts add column if not exists presentation_currency text;
alter table dart_kr.financial_facts add column if not exists converted_currency text;
alter table dart_kr.financial_facts add column if not exists converted_value numeric;
alter table dart_kr.financial_facts add column if not exists fx_rate_date date;
alter table dart_kr.financial_facts add column if not exists fx_rate_source text;
alter table dart_kr.financial_statement_items add column if not exists fact_currency text;
alter table dart_kr.financial_statement_items add column if not exists reporting_currency text;
alter table dart_kr.financial_statement_items add column if not exists presentation_currency text;
alter table dart_kr.financial_statement_items add column if not exists converted_currency text;
alter table dart_kr.financial_statement_items add column if not exists converted_value numeric;
alter table dart_kr.financial_statement_items add column if not exists fx_rate_date date;
alter table dart_kr.financial_statement_items add column if not exists fx_rate_source text;
alter table dart_kr.financial_key_metrics add column if not exists fact_currency text;
alter table dart_kr.financial_key_metrics add column if not exists reporting_currency text;
alter table dart_kr.financial_key_metrics add column if not exists presentation_currency text;
alter table dart_kr.financial_key_metrics add column if not exists converted_currency text;
alter table dart_kr.financial_key_metrics add column if not exists converted_value numeric;
alter table dart_kr.financial_key_metrics add column if not exists fx_rate_date date;
alter table dart_kr.financial_key_metrics add column if not exists fx_rate_source text;
alter table dart_kr.financial_balance_sheet_items add column if not exists fact_currency text;
alter table dart_kr.financial_balance_sheet_items add column if not exists reporting_currency text;
alter table dart_kr.financial_balance_sheet_items add column if not exists presentation_currency text;
alter table dart_kr.financial_balance_sheet_items add column if not exists converted_currency text;
alter table dart_kr.financial_balance_sheet_items add column if not exists converted_value numeric;
alter table dart_kr.financial_balance_sheet_items add column if not exists fx_rate_date date;
alter table dart_kr.financial_balance_sheet_items add column if not exists fx_rate_source text;
alter table dart_kr.financial_income_statement_items add column if not exists fact_currency text;
alter table dart_kr.financial_income_statement_items add column if not exists reporting_currency text;
alter table dart_kr.financial_income_statement_items add column if not exists presentation_currency text;
alter table dart_kr.financial_income_statement_items add column if not exists converted_currency text;
alter table dart_kr.financial_income_statement_items add column if not exists converted_value numeric;
alter table dart_kr.financial_income_statement_items add column if not exists fx_rate_date date;
alter table dart_kr.financial_income_statement_items add column if not exists fx_rate_source text;
alter table dart_kr.financial_cash_flow_statement_items add column if not exists fact_currency text;
alter table dart_kr.financial_cash_flow_statement_items add column if not exists reporting_currency text;
alter table dart_kr.financial_cash_flow_statement_items add column if not exists presentation_currency text;
alter table dart_kr.financial_cash_flow_statement_items add column if not exists converted_currency text;
alter table dart_kr.financial_cash_flow_statement_items add column if not exists converted_value numeric;
alter table dart_kr.financial_cash_flow_statement_items add column if not exists fx_rate_date date;
alter table dart_kr.financial_cash_flow_statement_items add column if not exists fx_rate_source text;
alter table dart_kr.financial_items_enriched add column if not exists fact_currency text;
alter table dart_kr.financial_items_enriched add column if not exists reporting_currency text;
alter table dart_kr.financial_items_enriched add column if not exists presentation_currency text;
alter table dart_kr.financial_items_enriched add column if not exists converted_currency text;
alter table dart_kr.financial_items_enriched add column if not exists converted_value numeric;
alter table dart_kr.financial_items_enriched add column if not exists fx_rate_date date;
alter table dart_kr.financial_items_enriched add column if not exists fx_rate_source text;

create or replace view dart_kr.v_latest_parse_runs as
select distinct on (f.filing_id)
    f.*,
    pr.parse_run_id,
    pr.completed_at,
    pr.status,
    pr.status as parse_status,
    pr.parser_version,
    pr.rules_version,
    pr.wiki_package_path
from dart_kr.filings f
join dart_kr.parse_runs pr on pr.filing_id = f.filing_id
where pr.status in ('pass', 'warning', 'completed', 'success')
order by f.filing_id, pr.completed_at desc nulls last, pr.parse_run_id desc;

drop view if exists dart_kr.v_agent_financial_facts cascade;
drop view if exists dart_kr.v_agent_financial_facts cascade;
create or replace view dart_kr.v_agent_financial_facts as
select
    c.company_id,
    c.ticker as company_ticker,
    c.stock_code,
    c.corp_code,
    c.company_name,
    f.filing_id,
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
    fsi.local_name,
    fsi.xbrl_tag,
    fsi.context_ref,
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
from dart_kr.financial_statement_items fsi
join dart_kr.filings f on f.filing_id = fsi.filing_id
join dart_kr.companies c on c.company_id = f.company_id
join dart_kr.v_latest_parse_runs pr on pr.parse_run_id = fsi.parse_run_id
left join dart_kr.evidence_citations ec on ec.evidence_id = fsi.evidence_id;

create or replace view dart_kr.v_latest_company_reports as
select distinct on (f.company_id, f.report_type)
    c.company_id,
    c.ticker as company_ticker,
    c.stock_code,
    c.corp_code,
    c.company_name,
    f.filing_id,
    f.rcp_no,
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
    pr.completed_at as parse_completed_at,
    pr.status as parse_status,
    pr.wiki_package_path
from dart_kr.filings f
join dart_kr.companies c on c.company_id = f.company_id
left join dart_kr.parse_runs pr on pr.parse_run_id = (
    select pr2.parse_run_id
    from dart_kr.parse_runs pr2
    where pr2.filing_id = f.filing_id
    order by pr2.completed_at desc nulls last, pr2.parse_run_id desc
    limit 1
)
order by f.company_id, f.report_type, f.period_end desc nulls last, f.published_at desc nulls last, f.filing_id desc;

create index if not exists idx_dart_kr_xbrl_facts_raw_parse_run on dart_kr.xbrl_facts_raw (parse_run_id);
create index if not exists idx_dart_kr_evidence_citations_parse_run on dart_kr.evidence_citations (parse_run_id);
create index if not exists idx_dart_kr_financial_facts_parse_run on dart_kr.financial_facts (parse_run_id);
create index if not exists idx_dart_kr_operating_metric_facts_parse_run on dart_kr.operating_metric_facts (parse_run_id);
create index if not exists idx_dart_kr_retrieval_chunks_parse_run on dart_kr.retrieval_chunks (parse_run_id);
create index if not exists idx_dart_kr_financial_statement_items_parse_run on dart_kr.financial_statement_items (parse_run_id);
create index if not exists idx_dart_kr_financial_key_metrics_parse_run on dart_kr.financial_key_metrics (parse_run_id);
create index if not exists idx_dart_kr_financial_balance_sheet_items_parse_run on dart_kr.financial_balance_sheet_items (parse_run_id);
create index if not exists idx_dart_kr_financial_income_statement_items_parse_run on dart_kr.financial_income_statement_items (parse_run_id);
create index if not exists idx_dart_kr_financial_cash_flow_statement_items_parse_run on dart_kr.financial_cash_flow_statement_items (parse_run_id);
create index if not exists idx_dart_kr_financial_checks_parse_run on dart_kr.financial_checks (parse_run_id);
create index if not exists idx_dart_kr_financial_items_enriched_parse_run on dart_kr.financial_items_enriched (parse_run_id);

drop view if exists dart_kr.v_agent_financial_facts cascade;
create or replace view dart_kr.v_agent_financial_facts as
with agent_items as (
    select
        item_uid, filing_id, parse_run_id, statement_id, statement_type, statement_name,
        item_index, period_key, item_name, canonical_name, local_name, xbrl_tag,
        context_ref, value, raw_value, unit, currency, fact_currency, reporting_currency,
        presentation_currency, converted_currency, converted_value, fx_rate_date,
        fx_rate_source, scale, period_start, period_end, confidence, source_page_number,
        source_table_index, source_row_index, source_column_index, source_bbox,
        evidence_id, raw
    from dart_kr.financial_statement_items
    union all
    select
        item_uid, filing_id, parse_run_id, statement_id, statement_type, statement_name,
        item_index, period_key, item_name, canonical_name, local_name, xbrl_tag,
        context_ref, value, raw_value, unit, currency, fact_currency, reporting_currency,
        presentation_currency, converted_currency, converted_value, fx_rate_date,
        fx_rate_source, scale, period_start, period_end, confidence, source_page_number,
        source_table_index, source_row_index, source_column_index, source_bbox,
        evidence_id, raw
    from dart_kr.financial_key_metrics
)
select
    c.company_id,
    c.ticker as company_ticker,
    c.stock_code,
    c.corp_code,
    c.company_name,
    f.filing_id,
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
    fsi.local_name,
    null::text as metric_name,
    null::text as metric_name_raw,
    null::text as label,
    null::text as concept,
    fsi.xbrl_tag,
    fsi.xbrl_tag as taxonomy_tag,
    fsi.context_ref,
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
join dart_kr.filings f on f.filing_id = fsi.filing_id
join dart_kr.companies c on c.company_id = f.company_id
join dart_kr.v_latest_parse_runs pr on pr.parse_run_id = fsi.parse_run_id
left join dart_kr.evidence_citations ec on ec.evidence_id = fsi.evidence_id;
