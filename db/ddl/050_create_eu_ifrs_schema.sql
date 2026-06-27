create schema if not exists eu_ifrs;

create table if not exists eu_ifrs.companies (
    company_id text primary key,
    country text not null,
    ticker text not null,
    isin text,
    lei text,
    company_name text,
    exchange text,
    industry_profile text,
    raw jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists idx_eu_ifrs_companies_country_ticker on eu_ifrs.companies (country, ticker);
create index if not exists idx_eu_ifrs_companies_isin on eu_ifrs.companies (isin);
create index if not exists idx_eu_ifrs_companies_lei on eu_ifrs.companies (lei);

create table if not exists eu_ifrs.filings (
    filing_id text primary key,
    company_id text not null references eu_ifrs.companies(company_id),
    country text not null,
    ticker text not null,
    form text,
    report_type text,
    fiscal_year integer,
    fiscal_period text,
    period_end date,
    published_at date,
    source_id text,
    source_tier text,
    source_url text,
    landing_url text,
    local_path text,
    document_format text,
    accounting_standard text,
    quality_status text,
    raw jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists idx_eu_ifrs_filings_country_ticker_year on eu_ifrs.filings (country, ticker, fiscal_year, report_type);
create index if not exists idx_eu_ifrs_filings_period_end on eu_ifrs.filings (period_end);
create index if not exists idx_eu_ifrs_filings_source on eu_ifrs.filings (source_id, source_tier);
create index if not exists idx_eu_ifrs_filings_document_format on eu_ifrs.filings (document_format);

create table if not exists eu_ifrs.parse_runs (
    parse_run_id text primary key,
    filing_id text not null references eu_ifrs.filings(filing_id) on delete cascade,
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

create index if not exists idx_eu_ifrs_parse_runs_filing on eu_ifrs.parse_runs (filing_id, completed_at desc);
create index if not exists idx_eu_ifrs_parse_runs_status on eu_ifrs.parse_runs (status);

create table if not exists eu_ifrs.artifacts (
    parse_run_id text not null references eu_ifrs.parse_runs(parse_run_id) on delete cascade,
    artifact_type text not null,
    local_path text not null,
    sha256 text,
    size_bytes bigint,
    raw jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    primary key (parse_run_id, artifact_type)
);

create table if not exists eu_ifrs.filing_sections (
    parse_run_id text not null references eu_ifrs.parse_runs(parse_run_id) on delete cascade,
    filing_id text not null references eu_ifrs.filings(filing_id) on delete cascade,
    section_id text not null,
    section_title text,
    section_order integer,
    markdown_path text,
    line_start integer,
    line_end integer,
    char_start integer,
    char_end integer,
    raw jsonb not null default '{}'::jsonb,
    primary key (parse_run_id, section_id)
);

create table if not exists eu_ifrs.pdf_pages (
    parse_run_id text not null references eu_ifrs.parse_runs(parse_run_id) on delete cascade,
    filing_id text not null references eu_ifrs.filings(filing_id) on delete cascade,
    page_number integer not null,
    markdown_path text,
    image_path text,
    raw jsonb not null default '{}'::jsonb,
    primary key (parse_run_id, page_number)
);

create table if not exists eu_ifrs.pdf_tables (
    parse_run_id text not null references eu_ifrs.parse_runs(parse_run_id) on delete cascade,
    filing_id text not null references eu_ifrs.filings(filing_id) on delete cascade,
    table_id text not null,
    page_number integer,
    table_index integer,
    title text,
    row_count integer,
    column_count integer,
    table_json_path text,
    unit text,
    currency text,
    raw jsonb not null default '{}'::jsonb,
    primary key (parse_run_id, table_id)
);

create index if not exists idx_eu_ifrs_pdf_tables_location on eu_ifrs.pdf_tables (filing_id, page_number, table_index);

create table if not exists eu_ifrs.html_tables (
    parse_run_id text not null references eu_ifrs.parse_runs(parse_run_id) on delete cascade,
    filing_id text not null references eu_ifrs.filings(filing_id) on delete cascade,
    table_id text not null,
    html_anchor text,
    xpath text,
    table_index integer,
    title text,
    row_count integer,
    column_count integer,
    table_json_path text,
    unit text,
    currency text,
    raw jsonb not null default '{}'::jsonb,
    primary key (parse_run_id, table_id)
);

create index if not exists idx_eu_ifrs_html_tables_anchor on eu_ifrs.html_tables (filing_id, html_anchor);

create table if not exists eu_ifrs.xbrl_contexts (
    context_uid text primary key,
    parse_run_id text not null references eu_ifrs.parse_runs(parse_run_id) on delete cascade,
    filing_id text not null references eu_ifrs.filings(filing_id) on delete cascade,
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

create index if not exists idx_eu_ifrs_xbrl_contexts_filing_ref on eu_ifrs.xbrl_contexts (filing_id, context_ref);

create table if not exists eu_ifrs.xbrl_units (
    unit_uid text primary key,
    parse_run_id text not null references eu_ifrs.parse_runs(parse_run_id) on delete cascade,
    filing_id text not null references eu_ifrs.filings(filing_id) on delete cascade,
    unit_ref text not null,
    measure text,
    numerator jsonb not null default '[]'::jsonb,
    denominator jsonb not null default '[]'::jsonb,
    raw jsonb not null default '{}'::jsonb,
    unique (parse_run_id, unit_ref)
);

create index if not exists idx_eu_ifrs_xbrl_units_filing_ref on eu_ifrs.xbrl_units (filing_id, unit_ref);

create table if not exists eu_ifrs.xbrl_facts_raw (
    raw_fact_id text primary key,
    fact_id text,
    parse_run_id text not null references eu_ifrs.parse_runs(parse_run_id) on delete cascade,
    filing_id text not null references eu_ifrs.filings(filing_id) on delete cascade,
    concept text not null,
    label text,
    value_text text,
    value_numeric numeric,
    unit_ref text,
    unit text,
    decimals text,
    scale text,
    context_ref text,
    period_start date,
    period_end date,
    instant date,
    duration_days integer,
    dimensions jsonb not null default '{}'::jsonb,
    is_extension boolean,
    source_type text,
    source_file text,
    html_anchor text,
    xpath text,
    raw jsonb not null default '{}'::jsonb,
    unique (parse_run_id, fact_id)
);

create index if not exists idx_eu_ifrs_xbrl_facts_filing_concept on eu_ifrs.xbrl_facts_raw (filing_id, concept);
create index if not exists idx_eu_ifrs_xbrl_facts_context on eu_ifrs.xbrl_facts_raw (context_ref);
create index if not exists idx_eu_ifrs_xbrl_facts_unit on eu_ifrs.xbrl_facts_raw (unit_ref);

create table if not exists eu_ifrs.evidence_citations (
    evidence_id text primary key,
    filing_id text not null references eu_ifrs.filings(filing_id) on delete cascade,
    parse_run_id text not null references eu_ifrs.parse_runs(parse_run_id) on delete cascade,
    country text,
    source_type text not null,
    source_id text,
    xbrl_tag text,
    context_ref text,
    unit_ref text,
    fact_id text,
    html_anchor text,
    xpath text,
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

create index if not exists idx_eu_ifrs_evidence_location on eu_ifrs.evidence_citations (filing_id, source_type, page_number, table_index);
create index if not exists idx_eu_ifrs_evidence_xbrl on eu_ifrs.evidence_citations (filing_id, xbrl_tag, context_ref);

create table if not exists eu_ifrs.financial_facts (
    metric_id text primary key,
    filing_id text not null references eu_ifrs.filings(filing_id) on delete cascade,
    parse_run_id text not null references eu_ifrs.parse_runs(parse_run_id) on delete cascade,
    country text not null,
    ticker text not null,
    statement_type text,
    canonical_name text not null,
    local_name text,
    value numeric,
    raw_value text,
    unit text,
    currency text,
    scale text,
    period_key text,
    period_start date,
    period_end date,
    fiscal_year integer,
    fiscal_period text,
    confidence numeric,
    evidence_id text references eu_ifrs.evidence_citations(evidence_id),
    raw_fact_id text references eu_ifrs.xbrl_facts_raw(raw_fact_id),
    xbrl_tag text,
    context_ref text,
    source_type text,
    raw jsonb not null default '{}'::jsonb
);

create index if not exists idx_eu_ifrs_financial_facts_country_ticker_metric_period on eu_ifrs.financial_facts (country, ticker, canonical_name, period_key);
create index if not exists idx_eu_ifrs_financial_facts_filing_statement on eu_ifrs.financial_facts (filing_id, statement_type);
create index if not exists idx_eu_ifrs_financial_facts_evidence on eu_ifrs.financial_facts (evidence_id);

create table if not exists eu_ifrs.operating_metric_facts (
    metric_id text primary key,
    filing_id text not null references eu_ifrs.filings(filing_id) on delete cascade,
    parse_run_id text not null references eu_ifrs.parse_runs(parse_run_id) on delete cascade,
    country text not null,
    ticker text not null,
    canonical_name text not null,
    value numeric,
    raw_value text,
    unit text,
    period_key text,
    period_start date,
    period_end date,
    fiscal_year integer,
    fiscal_period text,
    confidence numeric,
    evidence_id text references eu_ifrs.evidence_citations(evidence_id),
    source_type text,
    raw jsonb not null default '{}'::jsonb
);

create index if not exists idx_eu_ifrs_operating_metric_country_ticker on eu_ifrs.operating_metric_facts (country, ticker, canonical_name, period_key);

create table if not exists eu_ifrs.quality_checks (
    check_id text primary key,
    filing_id text not null references eu_ifrs.filings(filing_id) on delete cascade,
    parse_run_id text not null references eu_ifrs.parse_runs(parse_run_id) on delete cascade,
    rule_id text,
    rule_name text,
    statement_type text,
    period_key text,
    status text,
    diff numeric,
    tolerance numeric,
    raw jsonb not null default '{}'::jsonb
);

create index if not exists idx_eu_ifrs_quality_checks_status on eu_ifrs.quality_checks (status);

create table if not exists eu_ifrs.quality_reports (
    parse_run_id text primary key references eu_ifrs.parse_runs(parse_run_id) on delete cascade,
    filing_id text not null references eu_ifrs.filings(filing_id) on delete cascade,
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

create index if not exists idx_eu_ifrs_quality_reports_filing on eu_ifrs.quality_reports (filing_id, overall_status);

create table if not exists eu_ifrs.retrieval_chunks (
    chunk_uid text primary key,
    filing_id text not null references eu_ifrs.filings(filing_id) on delete cascade,
    parse_run_id text references eu_ifrs.parse_runs(parse_run_id) on delete set null,
    country text not null,
    ticker text not null,
    collection_name text not null default 'siq_eu_reports',
    doc_type text not null,
    evidence_id text references eu_ifrs.evidence_citations(evidence_id),
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

create index if not exists idx_eu_ifrs_retrieval_chunks_filing on eu_ifrs.retrieval_chunks (filing_id, doc_type);
create index if not exists idx_eu_ifrs_retrieval_chunks_collection on eu_ifrs.retrieval_chunks (collection_name, embedded);

create or replace view eu_ifrs.v_latest_parse_runs as
select distinct on (f.filing_id)
    f.*,
    pr.parse_run_id,
    pr.completed_at,
    pr.status as parse_status,
    pr.wiki_package_path
from eu_ifrs.filings f
join eu_ifrs.parse_runs pr on pr.filing_id = f.filing_id
order by f.filing_id, pr.completed_at desc nulls last, pr.parse_run_id desc;
