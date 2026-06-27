create schema if not exists sec_us;

create table if not exists sec_us.companies (
    company_id text primary key,
    cik text not null unique,
    ticker text not null,
    company_name text,
    sic text,
    sic_description text,
    industry_profile text default 'general',
    exchange text,
    raw jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists idx_sec_us_companies_ticker on sec_us.companies (ticker);
create index if not exists idx_sec_us_companies_industry_profile on sec_us.companies (industry_profile);

create table if not exists sec_us.filings (
    filing_id text primary key,
    company_id text not null references sec_us.companies(company_id),
    ticker text not null,
    form text not null,
    accession_number text not null unique,
    fiscal_year integer,
    fiscal_period text,
    period_end date,
    filing_date date,
    accepted_at timestamptz,
    source_url text,
    local_path text,
    accounting_standard text,
    quality_status text,
    raw jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists idx_sec_us_filings_ticker_year_form on sec_us.filings (ticker, fiscal_year, form);
create index if not exists idx_sec_us_filings_period_end on sec_us.filings (period_end);
create index if not exists idx_sec_us_filings_filing_date on sec_us.filings (filing_date);

create table if not exists sec_us.parse_runs (
    parse_run_id text primary key,
    filing_id text not null references sec_us.filings(filing_id),
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

create index if not exists idx_sec_us_parse_runs_filing_completed on sec_us.parse_runs (filing_id, completed_at desc);
create index if not exists idx_sec_us_parse_runs_status on sec_us.parse_runs (status);

create table if not exists sec_us.artifacts (
    parse_run_id text not null references sec_us.parse_runs(parse_run_id) on delete cascade,
    artifact_type text not null,
    local_path text not null,
    sha256 text,
    size_bytes bigint,
    raw jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    primary key (parse_run_id, artifact_type)
);

create index if not exists idx_sec_us_artifacts_sha256 on sec_us.artifacts (sha256);
create index if not exists idx_sec_us_artifacts_local_path on sec_us.artifacts (local_path);

create table if not exists sec_us.filing_sections (
    parse_run_id text not null references sec_us.parse_runs(parse_run_id) on delete cascade,
    filing_id text not null references sec_us.filings(filing_id),
    section_id text not null,
    section_title text,
    section_order integer,
    markdown_path text,
    html_anchor text,
    xpath text,
    text_hash text,
    raw jsonb not null default '{}'::jsonb,
    primary key (parse_run_id, section_id)
);

create table if not exists sec_us.html_tables (
    parse_run_id text not null references sec_us.parse_runs(parse_run_id) on delete cascade,
    filing_id text not null references sec_us.filings(filing_id),
    table_id text not null,
    section_id text,
    title text,
    row_count integer,
    column_count integer,
    table_json_path text,
    html_anchor text,
    xpath text,
    is_financial_statement_candidate boolean default false,
    raw jsonb not null default '{}'::jsonb,
    primary key (parse_run_id, table_id)
);

create table if not exists sec_us.xbrl_contexts (
    parse_run_id text not null references sec_us.parse_runs(parse_run_id) on delete cascade,
    filing_id text not null references sec_us.filings(filing_id),
    context_ref text not null,
    period_start date,
    period_end date,
    instant date,
    duration_days integer,
    dimensions jsonb not null default '{}'::jsonb,
    raw jsonb not null default '{}'::jsonb,
    primary key (parse_run_id, context_ref)
);

create table if not exists sec_us.xbrl_units (
    parse_run_id text not null references sec_us.parse_runs(parse_run_id) on delete cascade,
    filing_id text not null references sec_us.filings(filing_id),
    unit_ref text not null,
    unit text,
    raw jsonb not null default '{}'::jsonb,
    primary key (parse_run_id, unit_ref)
);

create table if not exists sec_us.xbrl_facts_raw (
    fact_id text primary key,
    parse_run_id text not null references sec_us.parse_runs(parse_run_id) on delete cascade,
    filing_id text not null references sec_us.filings(filing_id),
    concept text not null,
    taxonomy text,
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
    duration_days integer,
    instant date,
    fiscal_year integer,
    fiscal_period text,
    frame text,
    dimensions jsonb not null default '{}'::jsonb,
    is_extension boolean default false,
    html_anchor text,
    xpath text,
    raw jsonb not null default '{}'::jsonb
);

create index if not exists idx_sec_us_xbrl_facts_filing_concept on sec_us.xbrl_facts_raw (filing_id, concept);
create index if not exists idx_sec_us_xbrl_facts_context_ref on sec_us.xbrl_facts_raw (context_ref);
create index if not exists idx_sec_us_xbrl_facts_period_end on sec_us.xbrl_facts_raw (period_end);
create index if not exists idx_sec_us_xbrl_facts_dimensions on sec_us.xbrl_facts_raw using gin (dimensions);

create table if not exists sec_us.evidence_citations (
    evidence_id text primary key,
    filing_id text not null references sec_us.filings(filing_id),
    parse_run_id text not null references sec_us.parse_runs(parse_run_id) on delete cascade,
    source_type text not null,
    section_id text,
    xbrl_tag text,
    html_anchor text,
    xpath text,
    source_url text,
    local_path text,
    quote_text text,
    target text,
    raw jsonb not null default '{}'::jsonb
);

create index if not exists idx_sec_us_evidence_filing_source on sec_us.evidence_citations (filing_id, source_type);
create index if not exists idx_sec_us_evidence_section_tag on sec_us.evidence_citations (section_id, xbrl_tag);

create table if not exists sec_us.financial_facts (
    metric_id text primary key,
    filing_id text not null references sec_us.filings(filing_id),
    parse_run_id text not null references sec_us.parse_runs(parse_run_id) on delete cascade,
    ticker text not null,
    statement_type text not null,
    canonical_name text not null,
    concept text,
    label text,
    value numeric,
    unit text,
    currency text,
    period_key text,
    period_start date,
    period_end date,
    duration_days integer,
    qtd_ytd_type text,
    fiscal_year integer,
    fiscal_period text,
    segment_key text,
    dimensions jsonb not null default '{}'::jsonb,
    confidence numeric,
    evidence_id text references sec_us.evidence_citations(evidence_id),
    raw_fact_id text references sec_us.xbrl_facts_raw(fact_id),
    raw jsonb not null default '{}'::jsonb
);

create index if not exists idx_sec_us_financial_facts_ticker_metric_period on sec_us.financial_facts (ticker, canonical_name, period_key);
create index if not exists idx_sec_us_financial_facts_filing_statement on sec_us.financial_facts (filing_id, statement_type);
create index if not exists idx_sec_us_financial_facts_raw_fact on sec_us.financial_facts (raw_fact_id);

create table if not exists sec_us.operating_metric_facts (
    metric_id text primary key,
    filing_id text not null references sec_us.filings(filing_id),
    parse_run_id text not null references sec_us.parse_runs(parse_run_id) on delete cascade,
    metric_name text,
    canonical_name text,
    industry_profile text,
    value numeric,
    unit text,
    period_key text,
    source_type text,
    evidence_id text references sec_us.evidence_citations(evidence_id),
    confidence numeric,
    raw jsonb not null default '{}'::jsonb
);

create table if not exists sec_us.retrieval_chunks (
    chunk_uid text primary key,
    filing_id text not null references sec_us.filings(filing_id),
    parse_run_id text references sec_us.parse_runs(parse_run_id) on delete set null,
    ticker text not null,
    collection_name text not null default 'siq_us_sec_filings',
    batch_tag text,
    doc_type text not null,
    evidence_level text,
    section_id text,
    section_title text,
    table_id text,
    canonical_name text,
    concept text,
    period_key text,
    segment_key text,
    dimensions jsonb not null default '{}'::jsonb,
    evidence_id text references sec_us.evidence_citations(evidence_id),
    raw_fact_id text references sec_us.xbrl_facts_raw(fact_id),
    wiki_path text,
    source_url text,
    metadata jsonb not null default '{}'::jsonb,
    text_hash text,
    embedded boolean not null default false,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists idx_sec_us_retrieval_chunks_filing_doc_type on sec_us.retrieval_chunks (filing_id, doc_type);
create index if not exists idx_sec_us_retrieval_chunks_ticker_metric on sec_us.retrieval_chunks (ticker, canonical_name, period_key);
create index if not exists idx_sec_us_retrieval_chunks_section on sec_us.retrieval_chunks (section_id);
create index if not exists idx_sec_us_retrieval_chunks_evidence on sec_us.retrieval_chunks (evidence_id);
create index if not exists idx_sec_us_retrieval_chunks_dimensions on sec_us.retrieval_chunks using gin (dimensions);

create or replace view sec_us.v_latest_parse_runs as
select distinct on (filing_id)
    parse_run_id,
    filing_id,
    parser_version,
    rules_version,
    wiki_package_path,
    status,
    completed_at
from sec_us.parse_runs
where status in ('pass', 'warning')
order by filing_id, completed_at desc nulls last, parse_run_id desc;

create or replace view sec_us.financial_balance_sheet_items as
select f.*
from sec_us.financial_facts f
join sec_us.v_latest_parse_runs r using (parse_run_id)
where f.statement_type = 'balance_sheet';

create or replace view sec_us.financial_income_statement_items as
select f.*
from sec_us.financial_facts f
join sec_us.v_latest_parse_runs r using (parse_run_id)
where f.statement_type = 'income_statement';

create or replace view sec_us.financial_cash_flow_statement_items as
select f.*
from sec_us.financial_facts f
join sec_us.v_latest_parse_runs r using (parse_run_id)
where f.statement_type = 'cash_flow_statement';

create or replace view sec_us.financial_all_metrics_wide as
select
    f.filing_id,
    f.parse_run_id,
    f.ticker,
    f.fiscal_year,
    f.fiscal_period,
    f.period_key,
    jsonb_object_agg(f.canonical_name, f.value order by f.canonical_name) as metrics
from sec_us.financial_facts f
join sec_us.v_latest_parse_runs r using (parse_run_id)
group by f.filing_id, f.parse_run_id, f.ticker, f.fiscal_year, f.fiscal_period, f.period_key;
