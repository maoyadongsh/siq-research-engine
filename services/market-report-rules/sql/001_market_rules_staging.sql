-- Independent market-local schema for US/HK parsed report rules.
-- Run this DDL inside the market-specific database:
--   US/HK/CN: siq database with market-specific schemas
-- The schema name intentionally mirrors the A-share pdf2md shape, but the
-- physical database is market-isolated.

CREATE SCHEMA IF NOT EXISTS pdf2md;

CREATE TABLE IF NOT EXISTS pdf2md.financial_data_artifacts (
    artifact_id TEXT PRIMARY KEY,
    market TEXT NOT NULL CHECK (market IN ('US', 'HK')),
    company_id TEXT NOT NULL,
    ticker TEXT NOT NULL,
    report_id TEXT,
    rule_version TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    industry_profile TEXT NOT NULL DEFAULT 'general',
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS pdf2md.financial_checks_artifacts (
    artifact_id TEXT PRIMARY KEY,
    market TEXT NOT NULL CHECK (market IN ('US', 'HK')),
    company_id TEXT NOT NULL,
    ticker TEXT NOT NULL,
    rule_version TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    overall_status TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS pdf2md.financial_statements (
    artifact_id TEXT NOT NULL,
    market TEXT NOT NULL CHECK (market IN ('US', 'HK')),
    statement_id TEXT NOT NULL,
    statement_type TEXT NOT NULL,
    statement_name TEXT,
    scope TEXT NOT NULL DEFAULT 'consolidated',
    unit TEXT,
    currency TEXT,
    scale NUMERIC,
    raw JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (artifact_id, statement_id)
);

CREATE TABLE IF NOT EXISTS pdf2md.financial_facts (
    artifact_id TEXT NOT NULL,
    market TEXT NOT NULL CHECK (market IN ('US', 'HK')),
    fact_index INTEGER NOT NULL,
    statement_type TEXT NOT NULL,
    canonical_name TEXT NOT NULL,
    local_name TEXT,
    period_key TEXT NOT NULL,
    period_start DATE,
    period_end DATE,
    duration_days INTEGER,
    frame TEXT,
    qtd_ytd_type TEXT,
    value NUMERIC,
    raw_value TEXT,
    unit TEXT,
    currency TEXT,
    scale NUMERIC,
    accounting_standard TEXT,
    taxonomy TEXT,
    is_extension BOOLEAN NOT NULL DEFAULT false,
    gaap_status TEXT NOT NULL DEFAULT 'reported_gaap',
    source_accession TEXT,
    confidence NUMERIC,
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    evidence_target JSONB NOT NULL DEFAULT '{}'::jsonb,
    raw JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (artifact_id, statement_type, canonical_name, period_key, fact_index)
);

CREATE TABLE IF NOT EXISTS pdf2md.operating_metric_facts (
    artifact_id TEXT NOT NULL,
    market TEXT NOT NULL CHECK (market IN ('US', 'HK')),
    fact_index INTEGER NOT NULL,
    statement_type TEXT NOT NULL DEFAULT 'operating_metrics',
    canonical_name TEXT NOT NULL,
    local_name TEXT,
    period_key TEXT NOT NULL,
    period_start DATE,
    period_end DATE,
    duration_days INTEGER,
    frame TEXT,
    qtd_ytd_type TEXT,
    value NUMERIC,
    raw_value TEXT,
    unit TEXT,
    currency TEXT,
    scale NUMERIC,
    accounting_standard TEXT,
    taxonomy TEXT,
    is_extension BOOLEAN NOT NULL DEFAULT false,
    gaap_status TEXT NOT NULL DEFAULT 'operating_kpi',
    source_accession TEXT,
    confidence NUMERIC,
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    evidence_target JSONB NOT NULL DEFAULT '{}'::jsonb,
    raw JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (artifact_id, canonical_name, period_key, fact_index)
);

CREATE TABLE IF NOT EXISTS pdf2md.validation_checks (
    artifact_id TEXT NOT NULL,
    market TEXT NOT NULL CHECK (market IN ('US', 'HK')),
    check_index INTEGER NOT NULL,
    rule_id TEXT NOT NULL,
    rule_name TEXT,
    statement_type TEXT,
    status TEXT NOT NULL,
    period TEXT,
    diff NUMERIC,
    tolerance NUMERIC,
    inputs JSONB NOT NULL DEFAULT '[]'::jsonb,
    left_side JSONB NOT NULL DEFAULT '{}'::jsonb,
    right_side JSONB NOT NULL DEFAULT '{}'::jsonb,
    evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
    raw JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (artifact_id, check_index)
);

CREATE TABLE IF NOT EXISTS pdf2md.evidence_citations (
    citation_id TEXT PRIMARY KEY,
    artifact_id TEXT NOT NULL,
    parse_run_id TEXT NOT NULL,
    market TEXT NOT NULL CHECK (market IN ('US', 'HK')),
    source_type TEXT NOT NULL,
    source_id TEXT,
    page_number INTEGER,
    rendered_page_number INTEGER,
    section TEXT,
    anchor TEXT,
    xpath TEXT,
    xbrl_tag TEXT,
    accession_number TEXT,
    quote_text TEXT,
    url TEXT,
    path TEXT,
    target JSONB NOT NULL DEFAULT '{}'::jsonb,
    raw JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_financial_facts_lookup
    ON pdf2md.financial_facts (market, canonical_name, period_key);

CREATE INDEX IF NOT EXISTS idx_operating_metric_facts_lookup
    ON pdf2md.operating_metric_facts (market, canonical_name, period_key);

CREATE INDEX IF NOT EXISTS idx_validation_checks_status
    ON pdf2md.validation_checks (market, status, rule_id);

CREATE INDEX IF NOT EXISTS idx_evidence_artifact
    ON pdf2md.evidence_citations (market, artifact_id, source_type);
