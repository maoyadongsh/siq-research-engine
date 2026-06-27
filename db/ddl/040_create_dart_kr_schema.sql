create schema if not exists dart_kr;

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
