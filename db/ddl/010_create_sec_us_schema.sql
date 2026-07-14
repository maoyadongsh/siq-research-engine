create schema if not exists sec_us;

drop view if exists sec_us.v_latest_company_reports cascade;
drop view if exists sec_us.v_agent_financial_facts cascade;
drop view if exists sec_us.v_financial_statement_items_cash_flow_statement cascade;
drop view if exists sec_us.v_financial_statement_items_income_statement cascade;
drop view if exists sec_us.v_financial_statement_items_balance_sheet cascade;
drop view if exists sec_us.v_latest_parse_run_filings cascade;
drop view if exists sec_us.v_latest_parse_runs cascade;

do $$
begin
    if exists (select 1 from information_schema.views where table_schema = 'sec_us' and table_name = 'financial_all_metrics_wide') then
        drop view sec_us.financial_all_metrics_wide cascade;
    end if;
    if exists (select 1 from information_schema.views where table_schema = 'sec_us' and table_name = 'financial_cash_flow_statement_items') then
        drop view sec_us.financial_cash_flow_statement_items cascade;
    end if;
    if exists (select 1 from information_schema.views where table_schema = 'sec_us' and table_name = 'financial_income_statement_items') then
        drop view sec_us.financial_income_statement_items cascade;
    end if;
    if exists (select 1 from information_schema.views where table_schema = 'sec_us' and table_name = 'financial_balance_sheet_items') then
        drop view sec_us.financial_balance_sheet_items cascade;
    end if;
end $$;

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

create table if not exists sec_us.raw_payload_refs (
    payload_ref_id text primary key,
    filing_id text not null references sec_us.filings(filing_id) on delete cascade,
    parse_run_id text not null references sec_us.parse_runs(parse_run_id) on delete cascade,
    payload_name text not null,
    local_path text,
    sha256 text,
    size_bytes bigint,
    summary jsonb not null default '{}'::jsonb,
    raw jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

create index if not exists idx_sec_us_raw_payload_refs_parse_run on sec_us.raw_payload_refs (parse_run_id);
create index if not exists idx_sec_us_raw_payload_refs_sha256 on sec_us.raw_payload_refs (sha256);

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

create or replace view sec_us.v_financial_facts_wide_legacy as
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

alter table sec_us.companies add column if not exists company_name_en text;
alter table sec_us.companies add column if not exists short_name text;
alter table sec_us.companies add column if not exists aliases jsonb not null default '[]'::jsonb;

alter table sec_us.filings add column if not exists report_type text;
alter table sec_us.filings add column if not exists published_at date;
alter table sec_us.filings add column if not exists landing_url text;
alter table sec_us.filings add column if not exists document_format text;

alter table sec_us.html_tables add column if not exists table_index integer;
alter table sec_us.html_tables add column if not exists unit text;
alter table sec_us.html_tables add column if not exists currency text;
alter table sec_us.html_tables add column if not exists source_format text;
alter table sec_us.html_tables add column if not exists document_format text;

alter table sec_us.evidence_citations add column if not exists unit_ref text;
alter table sec_us.evidence_citations add column if not exists fact_id text;
alter table sec_us.evidence_citations add column if not exists page_number integer;
alter table sec_us.evidence_citations add column if not exists table_index integer;
alter table sec_us.evidence_citations add column if not exists row_index integer;
alter table sec_us.evidence_citations add column if not exists column_index integer;
alter table sec_us.evidence_citations add column if not exists bbox jsonb;

-- The raw-XBRL Agent view picks one citation per fact via a lateral lookup.
-- Keep that lookup bounded to the fact instead of rescanning a whole parse run.
create index if not exists idx_sec_us_evidence_citations_parse_fact
    on sec_us.evidence_citations (parse_run_id, fact_id, evidence_id)
    where fact_id is not null;

alter table sec_us.financial_facts add column if not exists raw_value text;
alter table sec_us.financial_facts add column if not exists scale numeric;
alter table sec_us.financial_facts add column if not exists source_type text;

alter table sec_us.operating_metric_facts add column if not exists ticker text;
alter table sec_us.operating_metric_facts add column if not exists raw_value text;
alter table sec_us.operating_metric_facts add column if not exists currency text;
alter table sec_us.operating_metric_facts add column if not exists period_start date;
alter table sec_us.operating_metric_facts add column if not exists period_end date;
alter table sec_us.operating_metric_facts add column if not exists fiscal_year integer;
alter table sec_us.operating_metric_facts add column if not exists fiscal_period text;
alter table sec_us.operating_metric_facts add column if not exists segment_key text;
alter table sec_us.operating_metric_facts add column if not exists dimensions jsonb not null default '{}'::jsonb;

alter table sec_us.retrieval_chunks add column if not exists company_id text;
alter table sec_us.retrieval_chunks add column if not exists statement_type text;
alter table sec_us.retrieval_chunks add column if not exists page_number integer;
alter table sec_us.retrieval_chunks add column if not exists table_index integer;
alter table sec_us.retrieval_chunks add column if not exists text text;

create index if not exists idx_sec_us_companies_aliases_gin on sec_us.companies using gin (aliases);
create index if not exists idx_sec_us_financial_facts_dimensions on sec_us.financial_facts using gin (dimensions);
create index if not exists idx_sec_us_operating_metrics_dimensions on sec_us.operating_metric_facts using gin (dimensions);

create table if not exists sec_us.pdf_pages (
    parse_run_id text not null references sec_us.parse_runs(parse_run_id) on delete cascade,
    filing_id text not null references sec_us.filings(filing_id) on delete cascade,
    page_number integer not null,
    markdown_path text,
    image_path text,
    raw jsonb not null default '{}'::jsonb,
    primary key (parse_run_id, page_number)
);

create table if not exists sec_us.financial_statements (
    parse_run_id text not null references sec_us.parse_runs(parse_run_id) on delete cascade,
    filing_id text not null references sec_us.filings(filing_id) on delete cascade,
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

create index if not exists idx_sec_us_financial_statements_filing on sec_us.financial_statements (filing_id, statement_type);

create table if not exists sec_us.financial_statement_items (
    item_uid text primary key,
    filing_id text not null references sec_us.filings(filing_id) on delete cascade,
    parse_run_id text not null references sec_us.parse_runs(parse_run_id) on delete cascade,
    company_id text,
    ticker text not null,
    cik text,
    accession_number text,
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
    concept text,
    taxonomy text,
    label text,
    context_ref text,
    value numeric,
    raw_value text,
    unit text,
    currency text,
    scale numeric,
    period_start date,
    period_end date,
    duration_days integer,
    qtd_ytd_type text,
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
    evidence_id text references sec_us.evidence_citations(evidence_id),
    raw_fact_id text references sec_us.xbrl_facts_raw(fact_id),
    raw jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

create index if not exists idx_sec_us_statement_items_lookup on sec_us.financial_statement_items (ticker, statement_type, canonical_name, period_key);
create index if not exists idx_sec_us_statement_items_concept on sec_us.financial_statement_items (filing_id, concept, context_ref);
create index if not exists idx_sec_us_statement_items_source on sec_us.financial_statement_items (filing_id, source_page_number, source_table_index);
create index if not exists idx_sec_us_statement_items_dimensions on sec_us.financial_statement_items using gin (dimensions);

create table if not exists sec_us.financial_key_metrics (
    like sec_us.financial_statement_items including defaults including constraints including indexes
);

create table if not exists sec_us.financial_balance_sheet_items (
    like sec_us.financial_statement_items including defaults including constraints including indexes
);

create table if not exists sec_us.financial_income_statement_items (
    like sec_us.financial_statement_items including defaults including constraints including indexes
);

create table if not exists sec_us.financial_cash_flow_statement_items (
    like sec_us.financial_statement_items including defaults including constraints including indexes
);

create table if not exists sec_us.financial_all_metrics_wide_detail (
    parse_run_id text not null references sec_us.parse_runs(parse_run_id) on delete cascade,
    filing_id text not null references sec_us.filings(filing_id) on delete cascade,
    company_id text,
    ticker text not null,
    cik text,
    accession_number text,
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

create index if not exists idx_sec_us_all_metrics_wide_detail_lookup on sec_us.financial_all_metrics_wide_detail (ticker, fiscal_year, fiscal_period, period_key);
create index if not exists idx_sec_us_all_metrics_wide_detail_gin on sec_us.financial_all_metrics_wide_detail using gin (all_metrics);

create table if not exists sec_us.quality_checks (
    check_id text primary key,
    filing_id text not null references sec_us.filings(filing_id) on delete cascade,
    parse_run_id text not null references sec_us.parse_runs(parse_run_id) on delete cascade,
    rule_id text,
    rule_name text,
    statement_type text,
    period_key text,
    status text,
    diff numeric,
    tolerance numeric,
    raw jsonb not null default '{}'::jsonb
);

create index if not exists idx_sec_us_quality_checks_status on sec_us.quality_checks (status);

create table if not exists sec_us.quality_reports (
    parse_run_id text primary key references sec_us.parse_runs(parse_run_id) on delete cascade,
    filing_id text not null references sec_us.filings(filing_id) on delete cascade,
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

create index if not exists idx_sec_us_quality_reports_filing on sec_us.quality_reports (filing_id, overall_status);

create table if not exists sec_us.financial_normalization_rules (
    rule_id text primary key,
    rule_type text not null,
    rule_version text not null default 'weak-v1-20260709',
    description text not null,
    preserves_raw_value boolean not null default true,
    confidence_default text,
    notes text,
    created_at timestamptz not null default now()
);

insert into sec_us.financial_normalization_rules (
    rule_id, rule_type, rule_version, description, preserves_raw_value, confidence_default, notes
) values
    ('canonical_source_xbrl', 'canonical', 'weak-v1-20260709', 'canonical label sourced from US GAAP/IFRS XBRL or parser mapping.', true, 'high', 'Keeps original concept, label, and XBRL fact unchanged.'),
    ('canonical_import_fallback', 'canonical', 'weak-v1-20260709', 'canonical label sourced from import fallback mapping.', true, 'medium', 'Weak semantic label only.'),
    ('canonical_unmapped', 'canonical', 'weak-v1-20260709', 'canonical label is missing.', true, 'none', 'Use label/concept for citation display.'),
    ('period_context_identity', 'period', 'weak-v1-20260709', 'period dates copied from XBRL context or parsed source period.', true, 'high', 'Original period_key is preserved.'),
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

create table if not exists sec_us.financial_items_enriched (
    enriched_id text primary key,
    source_table text not null,
    source_uid text not null,
    filing_id text not null references sec_us.filings(filing_id) on delete cascade,
    parse_run_id text not null references sec_us.parse_runs(parse_run_id) on delete cascade,
    company_id text,
    ticker text not null,
    market text not null default 'US',
    cik text,
    accession_number text,
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
    canonical_rule_id text references sec_us.financial_normalization_rules(rule_id),
    metric_family text,
    metric_family_rule_id text,
    value_extracted numeric,
    raw_value text,
    unit_raw text,
    currency text,
    unit_standardized text,
    unit_scale numeric,
    unit_rule_id text references sec_us.financial_normalization_rules(rule_id),
    value_standardized numeric,
    period_type text,
    period_start_date date,
    period_end_date date,
    period_rule_id text references sec_us.financial_normalization_rules(rule_id),
    source_page_number integer,
    source_table_index integer,
    source_row_index integer,
    source_column_index integer,
    source_bbox jsonb,
    evidence_id text references sec_us.evidence_citations(evidence_id),
    concept text,
    context_ref text,
    raw_fact_id text references sec_us.xbrl_facts_raw(fact_id),
    raw_item jsonb not null default '{}'::jsonb,
    normalization_confidence text not null default 'low',
    quality_flags jsonb not null default '[]'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (source_table, source_uid)
);

create index if not exists idx_sec_us_items_enriched_lookup
    on sec_us.financial_items_enriched (ticker, statement_type, canonical_label, period_end_date);
create index if not exists idx_sec_us_items_enriched_raw_name
    on sec_us.financial_items_enriched (ticker, statement_type, item_name_raw);
create index if not exists idx_sec_us_items_enriched_source
    on sec_us.financial_items_enriched (filing_id, source_page_number, source_table_index);
create index if not exists idx_sec_us_items_enriched_flags_gin
    on sec_us.financial_items_enriched using gin (quality_flags);

alter table sec_us.financial_facts add column if not exists fact_currency text;
alter table sec_us.financial_facts add column if not exists reporting_currency text;
alter table sec_us.financial_facts add column if not exists presentation_currency text;
alter table sec_us.financial_facts add column if not exists converted_currency text;
alter table sec_us.financial_facts add column if not exists converted_value numeric;
alter table sec_us.financial_facts add column if not exists fx_rate_date date;
alter table sec_us.financial_facts add column if not exists fx_rate_source text;
alter table sec_us.financial_statement_items add column if not exists fact_currency text;
alter table sec_us.financial_statement_items add column if not exists reporting_currency text;
alter table sec_us.financial_statement_items add column if not exists presentation_currency text;
alter table sec_us.financial_statement_items add column if not exists converted_currency text;
alter table sec_us.financial_statement_items add column if not exists converted_value numeric;
alter table sec_us.financial_statement_items add column if not exists fx_rate_date date;
alter table sec_us.financial_statement_items add column if not exists fx_rate_source text;
alter table sec_us.financial_key_metrics add column if not exists fact_currency text;
alter table sec_us.financial_key_metrics add column if not exists reporting_currency text;
alter table sec_us.financial_key_metrics add column if not exists presentation_currency text;
alter table sec_us.financial_key_metrics add column if not exists converted_currency text;
alter table sec_us.financial_key_metrics add column if not exists converted_value numeric;
alter table sec_us.financial_key_metrics add column if not exists fx_rate_date date;
alter table sec_us.financial_key_metrics add column if not exists fx_rate_source text;
alter table sec_us.financial_items_enriched add column if not exists fact_currency text;
alter table sec_us.financial_items_enriched add column if not exists reporting_currency text;
alter table sec_us.financial_items_enriched add column if not exists presentation_currency text;
alter table sec_us.financial_items_enriched add column if not exists converted_currency text;
alter table sec_us.financial_items_enriched add column if not exists converted_value numeric;
alter table sec_us.financial_items_enriched add column if not exists fx_rate_date date;
alter table sec_us.financial_items_enriched add column if not exists fx_rate_source text;

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
where status in ('pass', 'warning', 'completed', 'success')
order by filing_id, completed_at desc nulls last, parse_run_id desc;

create or replace view sec_us.v_latest_parse_run_filings as
select distinct on (f.filing_id)
    f.*,
    pr.parse_run_id,
    pr.completed_at,
    pr.status as parse_status,
    pr.parser_version,
    pr.rules_version,
    pr.wiki_package_path
from sec_us.filings f
join sec_us.parse_runs pr on pr.filing_id = f.filing_id
where pr.status in ('pass', 'warning', 'completed', 'success')
order by f.filing_id, pr.completed_at desc nulls last, pr.parse_run_id desc;

create or replace view sec_us.v_financial_statement_items_balance_sheet as
select f.*
from sec_us.financial_statement_items f
join sec_us.v_latest_parse_runs r using (parse_run_id)
where f.statement_type = 'balance_sheet';

create or replace view sec_us.v_financial_statement_items_income_statement as
select f.*
from sec_us.financial_statement_items f
join sec_us.v_latest_parse_runs r using (parse_run_id)
where f.statement_type = 'income_statement';

create or replace view sec_us.v_financial_statement_items_cash_flow_statement as
select f.*
from sec_us.financial_statement_items f
join sec_us.v_latest_parse_runs r using (parse_run_id)
where f.statement_type = 'cash_flow_statement';

drop view if exists sec_us.v_agent_financial_facts cascade;
drop view if exists sec_us.v_agent_financial_facts cascade;
create or replace view sec_us.v_agent_financial_facts as
select
    c.company_id,
    c.ticker as company_ticker,
    c.cik,
    c.company_name,
    f.filing_id,
    f.accession_number,
    coalesce(f.report_type, f.form) as report_type,
    f.form,
    f.fiscal_year,
    f.fiscal_period,
    f.period_end as filing_period_end,
    f.filing_date,
    f.accepted_at,
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
    fsi.concept,
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
from sec_us.financial_statement_items fsi
join sec_us.filings f on f.filing_id = fsi.filing_id
join sec_us.companies c on c.company_id = f.company_id
join sec_us.v_latest_parse_runs pr on pr.parse_run_id = fsi.parse_run_id
left join sec_us.evidence_citations ec on ec.evidence_id = fsi.evidence_id;

create or replace view sec_us.v_latest_company_reports as
select distinct on (f.company_id, coalesce(f.report_type, f.form))
    c.company_id,
    c.ticker as company_ticker,
    c.cik,
    c.company_name,
    f.filing_id,
    f.accession_number,
    coalesce(f.report_type, f.form) as report_type,
    f.form,
    f.fiscal_year,
    f.fiscal_period,
    f.period_end,
    f.filing_date,
    f.accepted_at,
    f.source_url,
    f.local_path,
    f.quality_status,
    pr.parse_run_id,
    pr.parser_version,
    pr.rules_version,
    pr.completed_at as parse_completed_at,
    pr.status as parse_status,
    pr.wiki_package_path
from sec_us.filings f
join sec_us.companies c on c.company_id = f.company_id
left join sec_us.parse_runs pr on pr.parse_run_id = (
    select pr2.parse_run_id
    from sec_us.parse_runs pr2
    where pr2.filing_id = f.filing_id
    order by pr2.completed_at desc nulls last, pr2.parse_run_id desc
    limit 1
)
order by f.company_id, coalesce(f.report_type, f.form), f.period_end desc nulls last, f.filing_date desc nulls last, f.filing_id desc;

create index if not exists idx_sec_us_xbrl_facts_raw_parse_run on sec_us.xbrl_facts_raw (parse_run_id);
create index if not exists idx_sec_us_evidence_citations_parse_run on sec_us.evidence_citations (parse_run_id);
create index if not exists idx_sec_us_financial_facts_parse_run on sec_us.financial_facts (parse_run_id);
create index if not exists idx_sec_us_operating_metric_facts_parse_run on sec_us.operating_metric_facts (parse_run_id);
create index if not exists idx_sec_us_retrieval_chunks_parse_run on sec_us.retrieval_chunks (parse_run_id);
create index if not exists idx_sec_us_financial_statement_items_parse_run on sec_us.financial_statement_items (parse_run_id);
create index if not exists idx_sec_us_financial_key_metrics_parse_run on sec_us.financial_key_metrics (parse_run_id);
create index if not exists idx_sec_us_financial_balance_sheet_items_parse_run on sec_us.financial_balance_sheet_items (parse_run_id);
create index if not exists idx_sec_us_financial_income_statement_items_parse_run on sec_us.financial_income_statement_items (parse_run_id);
create index if not exists idx_sec_us_financial_cash_flow_statement_items_parse_run on sec_us.financial_cash_flow_statement_items (parse_run_id);
create index if not exists idx_sec_us_quality_checks_parse_run on sec_us.quality_checks (parse_run_id);
create index if not exists idx_sec_us_financial_items_enriched_parse_run on sec_us.financial_items_enriched (parse_run_id);

drop view if exists sec_us.v_agent_financial_facts cascade;
create or replace view sec_us.v_agent_financial_facts as
with agent_items as (
    select
        item_uid, filing_id, parse_run_id, statement_id, statement_type, statement_name,
        item_index, period_key, item_name, canonical_name, local_name, concept, taxonomy,
        label, context_ref, value, raw_value, unit, currency, fact_currency,
        reporting_currency, presentation_currency, converted_currency, converted_value,
        fx_rate_date, fx_rate_source, scale, period_start, period_end, confidence,
        source_page_number, source_table_index, source_row_index, source_column_index,
        source_bbox, evidence_id, raw
    from sec_us.financial_statement_items
    union all
    select
        item_uid, filing_id, parse_run_id, statement_id, statement_type, statement_name,
        item_index, period_key, item_name, canonical_name, local_name, concept, taxonomy,
        label, context_ref, value, raw_value, unit, currency, fact_currency,
        reporting_currency, presentation_currency, converted_currency, converted_value,
        fx_rate_date, fx_rate_source, scale, period_start, period_end, confidence,
        source_page_number, source_table_index, source_row_index, source_column_index,
        source_bbox, evidence_id, raw
    from sec_us.financial_key_metrics
)
select
    c.company_id,
    c.ticker as company_ticker,
    c.cik,
    c.company_name,
    f.filing_id,
    f.accession_number,
    coalesce(f.report_type, f.form) as report_type,
    f.form,
    f.fiscal_year,
    f.fiscal_period,
    f.period_end as filing_period_end,
    f.filing_date,
    f.accepted_at,
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
    fsi.label,
    fsi.concept,
    fsi.concept as xbrl_tag,
    fsi.taxonomy as taxonomy_tag,
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
join sec_us.filings f on f.filing_id = fsi.filing_id
join sec_us.companies c on c.company_id = f.company_id
join sec_us.v_latest_parse_runs pr on pr.parse_run_id = fsi.parse_run_id
left join sec_us.evidence_citations ec on ec.evidence_id = fsi.evidence_id
union all
select
    c.company_id,
    c.ticker as company_ticker,
    c.cik,
    c.company_name,
    f.filing_id,
    f.accession_number,
    coalesce(f.report_type, f.form) as report_type,
    f.form,
    coalesce(x.fiscal_year, f.fiscal_year) as fiscal_year,
    coalesce(x.fiscal_period, f.fiscal_period) as fiscal_period,
    f.period_end as filing_period_end,
    f.filing_date,
    f.accepted_at,
    pr.parse_run_id,
    pr.completed_at as parse_completed_at,
    pr.wiki_package_path,
    x.fact_id as item_uid,
    null::text as statement_id,
    'xbrl_fact'::text as statement_type,
    'XBRL facts'::text as statement_name,
    null::integer as item_index,
    null::text as canonical_name,
    null::text as canonical_label,
    x.label as item_name,
    x.label as item_name_raw,
    x.label as local_name,
    null::text as metric_name,
    null::text as metric_name_raw,
    x.label,
    x.concept,
    x.concept as xbrl_tag,
    x.taxonomy as taxonomy_tag,
    x.context_ref,
    coalesce(x.period_end::text, x.context_ref) as period_key,
    x.period_start,
    x.period_end,
    x.value_numeric as value,
    x.value_text as raw_value,
    coalesce(x.unit, x.unit_ref) as unit,
    null::text as currency,
    null::text as fact_currency,
    null::text as reporting_currency,
    null::text as presentation_currency,
    null::text as converted_currency,
    null::numeric as converted_value,
    null::date as fx_rate_date,
    null::text as fx_rate_source,
    null::numeric as scale,
    null::numeric as confidence,
    ec.evidence_id,
    ec.page_number as evidence_page_number,
    ec.table_index as evidence_table_index,
    ec.row_index as evidence_row_index,
    ec.column_index as evidence_column_index,
    ec.bbox as evidence_bbox,
    ec.quote_text,
    coalesce(ec.source_url, f.source_url) as source_url,
    x.raw
from sec_us.xbrl_facts_raw x
join sec_us.filings f on f.filing_id = x.filing_id
join sec_us.companies c on c.company_id = f.company_id
join sec_us.v_latest_parse_runs pr on pr.parse_run_id = x.parse_run_id
left join lateral (
    select candidate.*
    from sec_us.evidence_citations candidate
    where candidate.parse_run_id = x.parse_run_id
      and candidate.fact_id = x.fact_id
    order by candidate.evidence_id
    limit 1
) ec on true;
