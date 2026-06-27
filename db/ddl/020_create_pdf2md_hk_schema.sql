create schema if not exists pdf2md_hk;

create table if not exists pdf2md_hk.companies (
    company_id text primary key,
    ticker text not null,
    company_name text,
    raw jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

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

create or replace view pdf2md_hk.v_latest_parse_runs as
select distinct on (f.filing_id)
    f.*,
    pr.parse_run_id,
    pr.completed_at,
    pr.status as parse_status,
    pr.wiki_package_path
from pdf2md_hk.filings f
join pdf2md_hk.parse_runs pr on pr.filing_id = f.filing_id
order by f.filing_id, pr.completed_at desc nulls last, pr.parse_run_id desc;
