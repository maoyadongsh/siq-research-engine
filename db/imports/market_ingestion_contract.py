from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit


DESIGN_VERSION = "market-postgres-v2-20260709"


@dataclass(frozen=True)
class MarketPostgresTarget:
    market: str
    database: str
    schema: str
    database_env: str
    schema_env: str
    database_url_env: str | None = None
    default_collection: str = ""
    fact_source_priority: tuple[str, ...] = ("xbrl", "normalized_metrics", "pdf_table")


MARKET_TARGETS: dict[str, MarketPostgresTarget] = {
    "HK": MarketPostgresTarget(
        market="HK",
        database="siq_hk",
        schema="pdf2md_hk",
        database_env="SIQ_HK_PGDATABASE",
        schema_env="SIQ_HK_SCHEMA",
        default_collection="siq_hk_reports",
        fact_source_priority=("normalized_metrics", "pdf_table", "xbrl"),
    ),
    "JP": MarketPostgresTarget(
        market="JP",
        database="siq_jp",
        schema="edinet_jp",
        database_env="SIQ_JP_PGDATABASE",
        schema_env="SIQ_JP_SCHEMA",
        default_collection="siq_jp_reports",
        fact_source_priority=("xbrl", "normalized_metrics", "pdf_table"),
    ),
    "KR": MarketPostgresTarget(
        market="KR",
        database="siq_kr",
        schema="dart_kr",
        database_env="SIQ_KR_PGDATABASE",
        schema_env="SIQ_KR_SCHEMA",
        default_collection="siq_kr_reports",
        fact_source_priority=("xbrl", "normalized_metrics", "pdf_table"),
    ),
    "EU": MarketPostgresTarget(
        market="EU",
        database="siq_eu",
        schema="eu_ifrs",
        database_env="SIQ_EU_PGDATABASE",
        schema_env="SIQ_EU_SCHEMA",
        default_collection="siq_eu_reports",
        fact_source_priority=("xbrl", "normalized_metrics", "html_table", "pdf_table"),
    ),
    "US": MarketPostgresTarget(
        market="US",
        database="siq_us",
        schema="sec_us",
        database_env="SIQ_US_PGDATABASE",
        schema_env="SIQ_US_SEC_SCHEMA",
        database_url_env="SIQ_US_DATABASE_URL",
        default_collection="siq_us_sec_filings",
        fact_source_priority=("xbrl", "ixbrl_html", "normalized_metrics", "html_table"),
    ),
}


_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def target_for_market(market: str) -> MarketPostgresTarget:
    key = str(market or "").upper()
    if key not in MARKET_TARGETS:
        raise SystemExit(f"Unsupported market for PostgreSQL import: {market}")
    return MARKET_TARGETS[key]


def quote_ident(identifier: str) -> str:
    if not _IDENTIFIER_RE.match(identifier):
        raise ValueError(f"Unsafe SQL identifier: {identifier!r}")
    return identifier


def _db_from_url(url: str) -> str:
    parsed = urlsplit(url)
    return parsed.path.rsplit("/", 1)[-1]


def _replace_db(url: str, database: str) -> str:
    parsed = urlsplit(url)
    path = parsed.path.rsplit("/", 1)[0] + f"/{database}" if "/" in parsed.path else f"/{database}"
    return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment))


def validate_database_name(database: str, market: str) -> None:
    target = target_for_market(market)
    if database != target.database:
        raise SystemExit(
            f"{target.market} imports must target database {target.database}; got {database}. "
            "Only the database names siq_hk/siq_jp/siq_kr/siq_eu/siq_us are fixed for non-A-share markets."
        )


def validate_schema(schema: str, market: str) -> None:
    target = target_for_market(market)
    if schema != target.schema:
        raise SystemExit(f"{target.market} imports must target schema {target.schema}; got {schema}")


def database_url(explicit: str | None, market: str) -> str:
    """Build a connection URL that always lands in the fixed market database.

    Explicit URLs remain useful for host/user/password overrides, but their path is
    rewritten to the market database. Generic DATABASE_URL is intentionally ignored
    unless SIQ_ALLOW_GENERIC_MARKET_DATABASE_URL=1; this prevents accidental writes
    to siq/pdf2md or another legacy database.
    """

    target = target_for_market(market)
    raw_url = explicit or (os.environ.get(target.database_url_env) if target.database_url_env else None)
    allow_generic = os.environ.get("SIQ_ALLOW_GENERIC_MARKET_DATABASE_URL") == "1"
    if not raw_url and allow_generic:
        raw_url = os.environ.get("DATABASE_URL")
    if raw_url:
        normalized = raw_url.replace("postgresql+psycopg://", "postgresql://")
        return _replace_db(normalized, target.database)

    configured_db = os.environ.get(target.database_env)
    if configured_db:
        validate_database_name(configured_db, market)
    host = os.environ.get("SIQ_PGHOST") or os.environ.get("PGHOST") or "127.0.0.1"
    port = os.environ.get("SIQ_PGPORT") or os.environ.get("PGPORT") or "15432"
    user = os.environ.get("SIQ_PGUSER") or os.environ.get("PGUSER") or "postgres"
    password = os.environ.get("SIQ_PGPASSWORD") or os.environ.get("PGPASSWORD") or ""
    auth = f"{user}:{password}" if password else user
    return f"postgresql://{auth}@{host}:{port}/{target.database}"


def validate_connection_database(conn: Any, market: str) -> None:
    target = target_for_market(market)
    with conn.cursor() as cur:
        cur.execute("select current_database()")
        row = cur.fetchone()
    current = row[0] if not isinstance(row, dict) else row.get("current_database")
    validate_database_name(str(current), target.market)


def run_market_ddl(conn: Any, market: str) -> None:
    validate_connection_database(conn, market)
    conn.execute(build_market_schema_sql(market))


def build_market_schema_sql(market: str) -> str:
    target = target_for_market(market)
    schema = quote_ident(target.schema)
    market_literal = target.market.replace("'", "''")
    database_literal = target.database.replace("'", "''")
    collection_literal = target.default_collection.replace("'", "''")
    priority_literal = ", ".join(f"'{item}'" for item in target.fact_source_priority)

    return f"""
-- {target.market} market PostgreSQL reset DDL.
-- Database name is fixed externally as {target.database}; this script only resets schema {target.schema}.
drop schema if exists {schema} cascade;
create schema {schema};

create table {schema}.market_metadata (
    market text primary key,
    database_name text not null,
    schema_name text not null,
    design_version text not null,
    fact_source_priority jsonb not null,
    created_at timestamptz not null default now()
);

insert into {schema}.market_metadata (
    market, database_name, schema_name, design_version, fact_source_priority
) values (
    '{market_literal}', '{database_literal}', '{schema}', '{DESIGN_VERSION}', jsonb_build_array({priority_literal})
);

create table {schema}.companies (
    company_id text primary key,
    market text not null default '{market_literal}',
    country text,
    ticker text,
    stock_code text,
    hkex_stock_code text,
    security_code text,
    synthetic_code text,
    exchange text,
    company_name text,
    company_short_name text,
    company_name_en text,
    company_name_zh text,
    legal_name text,
    cik text,
    corp_code text,
    edinet_code text,
    isin text,
    lei text,
    aliases jsonb not null default '[]'::jsonb,
    industry_profile text default 'general',
    raw jsonb not null default '{{}}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index idx_{schema}_companies_ticker on {schema}.companies (ticker);
create index idx_{schema}_companies_security_code on {schema}.companies (security_code);
create index idx_{schema}_companies_aliases_gin on {schema}.companies using gin (aliases);

create table {schema}.filings (
    filing_id text primary key,
    company_id text not null references {schema}.companies(company_id) on delete cascade,
    market text not null default '{market_literal}',
    country text,
    ticker text,
    stock_code text,
    report_id text,
    accession_number text,
    doc_id text,
    rcp_no text,
    form text,
    report_type text,
    fiscal_year integer,
    fiscal_period text,
    period_start date,
    period_end date,
    published_at date,
    filing_date date,
    accepted_at timestamptz,
    source_id text,
    source_tier text,
    source_url text,
    landing_url text,
    local_path text,
    document_format text,
    accounting_standard text,
    quality_status text,
    raw jsonb not null default '{{}}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index idx_{schema}_filings_company_year on {schema}.filings (company_id, fiscal_year desc, report_type);
create index idx_{schema}_filings_ticker_year on {schema}.filings (ticker, fiscal_year desc, report_type);
create index idx_{schema}_filings_period_end on {schema}.filings (period_end);

create table {schema}.parse_runs (
    parse_run_id text primary key,
    filing_id text not null references {schema}.filings(filing_id) on delete cascade,
    parser_name text,
    parser_version text,
    rules_version text,
    schema_version text,
    wiki_package_path text,
    package_path text,
    status text not null,
    started_at timestamptz not null default now(),
    completed_at timestamptz,
    warnings jsonb not null default '[]'::jsonb,
    artifact_hashes jsonb not null default '{{}}'::jsonb,
    quality_summary jsonb not null default '{{}}'::jsonb,
    raw jsonb not null default '{{}}'::jsonb
);

create index idx_{schema}_parse_runs_filing_completed on {schema}.parse_runs (filing_id, completed_at desc);
create index idx_{schema}_parse_runs_status on {schema}.parse_runs (status);

create table {schema}.artifacts (
    parse_run_id text not null references {schema}.parse_runs(parse_run_id) on delete cascade,
    artifact_type text not null,
    local_path text not null,
    sha256 text,
    size_bytes bigint,
    raw jsonb not null default '{{}}'::jsonb,
    created_at timestamptz not null default now(),
    primary key (parse_run_id, artifact_type)
);

create table {schema}.filing_sections (
    parse_run_id text not null references {schema}.parse_runs(parse_run_id) on delete cascade,
    filing_id text not null references {schema}.filings(filing_id) on delete cascade,
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
    raw jsonb not null default '{{}}'::jsonb,
    primary key (parse_run_id, section_id)
);

create table {schema}.pdf_pages (
    parse_run_id text not null references {schema}.parse_runs(parse_run_id) on delete cascade,
    filing_id text not null references {schema}.filings(filing_id) on delete cascade,
    page_number integer not null,
    markdown_path text,
    image_path text,
    raw jsonb not null default '{{}}'::jsonb,
    primary key (parse_run_id, page_number)
);

create table {schema}.document_tables (
    parse_run_id text not null references {schema}.parse_runs(parse_run_id) on delete cascade,
    filing_id text not null references {schema}.filings(filing_id) on delete cascade,
    table_id text not null,
    source_type text not null default 'pdf_table',
    section_id text,
    page_number integer,
    table_index integer,
    title text,
    html_anchor text,
    xpath text,
    row_count integer,
    column_count integer,
    table_json_path text,
    unit text,
    currency text,
    bbox jsonb,
    is_financial_statement_candidate boolean default false,
    raw jsonb not null default '{{}}'::jsonb,
    primary key (parse_run_id, table_id)
);

create table {schema}.pdf_tables (
    like {schema}.document_tables including defaults including constraints including indexes
);

create table {schema}.html_tables (
    like {schema}.document_tables including defaults including constraints including indexes
);

create index idx_{schema}_pdf_tables_location on {schema}.pdf_tables (filing_id, page_number, table_index);
create index idx_{schema}_html_tables_anchor on {schema}.html_tables (filing_id, html_anchor);

create table {schema}.xbrl_contexts (
    context_uid text,
    parse_run_id text not null references {schema}.parse_runs(parse_run_id) on delete cascade,
    filing_id text not null references {schema}.filings(filing_id) on delete cascade,
    context_ref text not null,
    entity_identifier text,
    period_start date,
    period_end date,
    instant date,
    duration_days integer,
    dimensions jsonb not null default '{{}}'::jsonb,
    raw jsonb not null default '{{}}'::jsonb,
    primary key (parse_run_id, context_ref),
    unique (context_uid)
);

create table {schema}.xbrl_units (
    unit_uid text,
    parse_run_id text not null references {schema}.parse_runs(parse_run_id) on delete cascade,
    filing_id text not null references {schema}.filings(filing_id) on delete cascade,
    unit_ref text not null,
    unit text,
    measure text,
    numerator jsonb not null default '[]'::jsonb,
    denominator jsonb not null default '[]'::jsonb,
    raw jsonb not null default '{{}}'::jsonb,
    primary key (parse_run_id, unit_ref),
    unique (unit_uid)
);

create table {schema}.xbrl_facts_raw (
    raw_fact_id text,
    fact_id text not null,
    parse_run_id text not null references {schema}.parse_runs(parse_run_id) on delete cascade,
    filing_id text not null references {schema}.filings(filing_id) on delete cascade,
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
    instant date,
    duration_days integer,
    fiscal_year integer,
    fiscal_period text,
    frame text,
    dimensions jsonb not null default '{{}}'::jsonb,
    is_extension boolean,
    source_type text,
    source_file text,
    html_anchor text,
    xpath text,
    raw jsonb not null default '{{}}'::jsonb,
    unique (fact_id),
    unique (raw_fact_id)
);

create index idx_{schema}_xbrl_facts_filing_concept on {schema}.xbrl_facts_raw (filing_id, concept);
create index idx_{schema}_xbrl_facts_context on {schema}.xbrl_facts_raw (context_ref);

create table {schema}.evidence_citations (
    evidence_id text primary key,
    filing_id text not null references {schema}.filings(filing_id) on delete cascade,
    parse_run_id text not null references {schema}.parse_runs(parse_run_id) on delete cascade,
    country text,
    source_type text not null,
    source_id text,
    section_id text,
    xbrl_tag text,
    concept text,
    context_ref text,
    unit_ref text,
    fact_id text,
    html_anchor text,
    xpath text,
    page_number integer,
    table_index integer,
    row_index integer,
    column_index integer,
    bbox jsonb,
    quote_text text,
    local_path text,
    source_url text,
    target text,
    raw jsonb not null default '{{}}'::jsonb
);

create index idx_{schema}_evidence_location on {schema}.evidence_citations (filing_id, source_type, page_number, table_index);
create index idx_{schema}_evidence_xbrl on {schema}.evidence_citations (filing_id, xbrl_tag, context_ref);

create table {schema}.financial_facts (
    metric_id text primary key,
    filing_id text not null references {schema}.filings(filing_id) on delete cascade,
    parse_run_id text not null references {schema}.parse_runs(parse_run_id) on delete cascade,
    company_id text,
    market text not null default '{market_literal}',
    country text,
    ticker text,
    stock_code text,
    company_name text,
    exchange text,
    statement_id text,
    statement_type text,
    statement_name text,
    scope text,
    scope_name text,
    item_index integer,
    canonical_name text,
    local_name text,
    item_name text,
    concept text,
    xbrl_tag text,
    taxonomy text,
    label text,
    value numeric,
    raw_value text,
    unit text,
    currency text,
    scale text,
    period_key text,
    period_start date,
    period_end date,
    instant date,
    duration_days integer,
    qtd_ytd_type text,
    fiscal_year integer,
    fiscal_period text,
    segment_key text,
    dimensions jsonb not null default '{{}}'::jsonb,
    confidence numeric,
    source_type text,
    evidence_id text references {schema}.evidence_citations(evidence_id),
    raw_fact_id text,
    context_ref text,
    unit_ref text,
    source_ref jsonb not null default '{{}}'::jsonb,
    raw jsonb not null default '{{}}'::jsonb,
    created_at timestamptz not null default now()
);

create index idx_{schema}_financial_facts_lookup on {schema}.financial_facts (ticker, statement_type, canonical_name, period_key);
create index idx_{schema}_financial_facts_source on {schema}.financial_facts (filing_id, evidence_id, raw_fact_id);
create index idx_{schema}_financial_facts_source_ref_gin on {schema}.financial_facts using gin (source_ref);

create table {schema}.financial_statement_items (
    item_uid text primary key,
    filing_id text not null references {schema}.filings(filing_id) on delete cascade,
    parse_run_id text not null references {schema}.parse_runs(parse_run_id) on delete cascade,
    company_id text,
    ticker text,
    stock_code text,
    company_name text,
    exchange text,
    statement_id text,
    statement_type text,
    statement_name text,
    scope text,
    scope_name text,
    item_index integer,
    period_key text,
    item_name text,
    canonical_name text,
    value numeric,
    raw_value text,
    unit text,
    currency text,
    scale text,
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
    evidence_id text references {schema}.evidence_citations(evidence_id),
    raw jsonb not null default '{{}}'::jsonb,
    created_at timestamptz not null default now()
);

create index idx_{schema}_stmt_items_lookup on {schema}.financial_statement_items (ticker, statement_type, canonical_name, period_key);
create index idx_{schema}_stmt_items_source on {schema}.financial_statement_items (filing_id, source_page_number, source_table_index);

create table {schema}.operating_metric_facts (
    metric_id text primary key,
    filing_id text not null references {schema}.filings(filing_id) on delete cascade,
    parse_run_id text not null references {schema}.parse_runs(parse_run_id) on delete cascade,
    country text,
    ticker text,
    metric_name text,
    canonical_name text,
    industry_profile text,
    value numeric,
    raw_value text,
    unit text,
    period_key text,
    period_start date,
    period_end date,
    fiscal_year integer,
    fiscal_period text,
    source_type text,
    confidence numeric,
    evidence_id text references {schema}.evidence_citations(evidence_id),
    raw jsonb not null default '{{}}'::jsonb
);

create table {schema}.financial_checks (
    check_id text primary key,
    filing_id text not null references {schema}.filings(filing_id) on delete cascade,
    parse_run_id text not null references {schema}.parse_runs(parse_run_id) on delete cascade,
    rule_id text,
    rule_name text,
    statement_type text,
    period_key text,
    status text,
    diff numeric,
    tolerance numeric,
    raw jsonb not null default '{{}}'::jsonb
);

create table {schema}.quality_checks (like {schema}.financial_checks including defaults including constraints including indexes);

create table {schema}.quality_reports (
    parse_run_id text primary key references {schema}.parse_runs(parse_run_id) on delete cascade,
    filing_id text not null references {schema}.filings(filing_id) on delete cascade,
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
    required_statement_status jsonb not null default '{{}}'::jsonb,
    critical_warnings jsonb not null default '[]'::jsonb,
    parser_warnings jsonb not null default '[]'::jsonb,
    rule_warnings jsonb not null default '[]'::jsonb,
    raw jsonb not null default '{{}}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table {schema}.retrieval_chunks (
    chunk_uid text primary key,
    filing_id text not null references {schema}.filings(filing_id) on delete cascade,
    parse_run_id text references {schema}.parse_runs(parse_run_id) on delete set null,
    company_id text,
    country text,
    ticker text,
    collection_name text not null default '{collection_literal}',
    batch_tag text,
    doc_type text not null,
    evidence_level text,
    section_id text,
    section_title text,
    statement_type text,
    table_id text,
    canonical_name text,
    concept text,
    period_key text,
    segment_key text,
    dimensions jsonb not null default '{{}}'::jsonb,
    evidence_id text references {schema}.evidence_citations(evidence_id),
    raw_fact_id text,
    page_number integer,
    table_index integer,
    wiki_path text,
    source_url text,
    text text,
    metadata jsonb not null default '{{}}'::jsonb,
    text_hash text,
    embedded boolean not null default false,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create or replace view {schema}.v_latest_parse_runs as
select distinct on (f.filing_id)
    f.*,
    pr.parse_run_id,
    pr.completed_at,
    pr.status as parse_status,
    pr.wiki_package_path
from {schema}.filings f
join {schema}.parse_runs pr on pr.filing_id = f.filing_id
order by f.filing_id, pr.completed_at desc nulls last, pr.parse_run_id desc;

create or replace view {schema}.financial_items_enriched as
select
    ff.metric_id as enriched_id,
    'financial_facts'::text as source_table,
    ff.parse_run_id,
    ff.filing_id,
    coalesce(ff.company_id, f.company_id) as company_id,
    '{market_literal}'::text as market,
    ff.country,
    coalesce(ff.ticker, f.ticker) as ticker,
    ff.stock_code,
    ff.company_name,
    ff.exchange,
    ff.statement_type,
    ff.statement_name,
    ff.scope,
    ff.scope_name,
    ff.period_key as period_key_raw,
    case
      when ff.instant is not null or (ff.statement_type = 'balance_sheet' and ff.period_end is not null) then 'instant'
      when ff.period_start is not null and ff.period_end is not null then 'duration'
      when ff.period_key ~ '^\\d{{4}}$' then 'annual'
      else null
    end as period_type,
    ff.period_start,
    coalesce(ff.period_end, ff.instant) as period_end_date,
    coalesce(ff.local_name, ff.item_name, ff.label, ff.concept, ff.xbrl_tag, ff.canonical_name) as item_name_raw,
    ff.canonical_name as canonical_label,
    case
      when ff.canonical_name is not null then 'source_payload'
      else 'unmapped'
    end as canonical_source,
    case
      when ff.canonical_name is not null then 'canonical_source_payload'
      else 'canonical_unmapped'
    end as canonical_rule_id,
    ff.value as value_extracted,
    coalesce(ff.raw_value, ff.value::text) as raw_value,
    ff.unit as unit_raw,
    ff.currency,
    coalesce(ff.currency, ff.unit) as unit_standardized,
    case
      when nullif(ff.scale, '') ~ '^-?\\d+(\\.\\d+)?$' then (ff.scale)::numeric
      else 1::numeric
    end as unit_scale,
    case
      when ff.unit is null and ff.currency is null then 'unit_raw_missing'
      else 'unit_identity'
    end as unit_rule_id,
    ff.value as value_standardized,
    ff.confidence as normalization_confidence,
    jsonb_build_object(
      'source_type', coalesce(ff.source_type, case when ff.raw_fact_id is not null then 'xbrl_fact' else 'normalized_metric' end),
      'evidence_id', ff.evidence_id,
      'raw_fact_id', ff.raw_fact_id,
      'concept', coalesce(ff.concept, ff.xbrl_tag),
      'context_ref', ff.context_ref,
      'unit_ref', ff.unit_ref,
      'page_number', ev.page_number,
      'table_index', ev.table_index,
      'bbox', ev.bbox,
      'local_path', ev.local_path,
      'source_url', coalesce(ev.source_url, f.source_url)
    ) || coalesce(ff.source_ref, '{{}}'::jsonb) as source_ref,
    to_jsonb(array_remove(array[
      case when ff.canonical_name is null then 'canonical_unmapped' end,
      case when ff.unit is null and ff.currency is null then 'unit_missing' end,
      case when ff.period_key is null then 'period_unparsed' end,
      case when ff.evidence_id is null and ff.raw_fact_id is null then 'source_missing' end
    ], null)) as quality_flags,
    ff.raw
from {schema}.financial_facts ff
join {schema}.filings f on f.filing_id = ff.filing_id
left join {schema}.evidence_citations ev on ev.evidence_id = ff.evidence_id
union all
select
    item.item_uid,
    'financial_statement_items',
    item.parse_run_id,
    item.filing_id,
    item.company_id,
    '{market_literal}',
    f.country,
    item.ticker,
    item.stock_code,
    item.company_name,
    item.exchange,
    item.statement_type,
    item.statement_name,
    item.scope,
    item.scope_name,
    item.period_key,
    case when item.statement_type = 'balance_sheet' then 'instant' else 'duration' end,
    item.period_start,
    item.period_end,
    item.item_name,
    item.canonical_name,
    case when item.canonical_name is not null then 'source_payload' else 'unmapped' end,
    case when item.canonical_name is not null then 'canonical_source_payload' else 'canonical_unmapped' end,
    item.value,
    item.raw_value,
    item.unit,
    item.currency,
    coalesce(item.currency, item.unit),
    case when nullif(item.scale, '') ~ '^-?\\d+(\\.\\d+)?$' then item.scale::numeric else 1::numeric end,
    case when item.unit is null and item.currency is null then 'unit_raw_missing' else 'unit_identity' end,
    item.value,
    item.confidence,
    jsonb_build_object(
      'source_type', 'pdf_table',
      'evidence_id', item.evidence_id,
      'page_number', coalesce(ev.page_number, item.source_page_number),
      'table_index', coalesce(ev.table_index, item.source_table_index),
      'row_index', coalesce(ev.row_index, item.source_row_index),
      'column_index', coalesce(ev.column_index, item.source_column_index),
      'bbox', coalesce(ev.bbox, item.source_bbox),
      'local_path', ev.local_path,
      'source_url', coalesce(ev.source_url, f.source_url)
    ),
    to_jsonb(array_remove(array[
      case when item.canonical_name is null then 'canonical_unmapped' end,
      case when item.unit is null and item.currency is null then 'unit_missing' end,
      case when item.period_key is null then 'period_unparsed' end,
      case when item.evidence_id is null then 'source_missing' end
    ], null)),
    item.raw
from {schema}.financial_statement_items item
join {schema}.filings f on f.filing_id = item.filing_id
left join {schema}.evidence_citations ev on ev.evidence_id = item.evidence_id;

create or replace view {schema}.financial_balance_sheet_items as
select * from {schema}.financial_items_enriched where statement_type = 'balance_sheet';

create or replace view {schema}.financial_income_statement_items as
select * from {schema}.financial_items_enriched where statement_type = 'income_statement';

create or replace view {schema}.financial_cash_flow_statement_items as
select * from {schema}.financial_items_enriched where statement_type = 'cash_flow_statement';

create or replace view {schema}.financial_all_metrics_wide as
select
    filing_id,
    parse_run_id,
    company_id,
    market,
    ticker,
    period_key_raw as period_key,
    jsonb_object_agg(coalesce(canonical_label, item_name_raw, enriched_id), value_standardized order by canonical_label) as all_metrics
from {schema}.financial_items_enriched
group by filing_id, parse_run_id, company_id, market, ticker, period_key_raw;

create or replace view {schema}.v_agent_financial_facts as
select
    e.*,
    f.report_id,
    f.report_type,
    f.fiscal_year,
    f.fiscal_period,
    f.period_end as filing_period_end,
    pr.status as parse_status,
    pr.wiki_package_path
from {schema}.financial_items_enriched e
join {schema}.filings f on f.filing_id = e.filing_id
join {schema}.parse_runs pr on pr.parse_run_id = e.parse_run_id;
"""
