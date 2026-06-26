-- Build the weak, additive enriched financial-item layer.
--
-- Design principle:
--   raw statement tables stay faithful to the PDF/parser output;
--   this table only adds reversible labels and derived analysis fields.

CREATE OR REPLACE FUNCTION pdf2md.financial_parse_raw_numeric(raw_text TEXT)
RETURNS NUMERIC
LANGUAGE SQL
IMMUTABLE
AS $$
    WITH cleaned AS (
        SELECT regexp_replace(
                   regexp_replace(
                       regexp_replace(btrim(raw_text), '^\((.*)\)$', '-\1'),
                       '[,，]',
                       '',
                       'g'
                   ),
                   '[[:space:]]',
                   '',
                   'g'
               ) AS value_text
    )
    SELECT CASE
        WHEN (SELECT value_text FROM cleaned) ~ '^-?[0-9]+(\.[0-9]+)?$'
        THEN (SELECT value_text FROM cleaned)::numeric
        ELSE NULL::numeric
    END;
$$;

COMMENT ON FUNCTION pdf2md.financial_parse_raw_numeric(TEXT)
IS 'Parse a single raw financial cell into numeric form. Used by the enriched layer so standardized values are derived from raw_value rather than from possibly pre-scaled extracted value.';

INSERT INTO pdf2md.financial_normalization_rules (
    rule_id, rule_type, rule_version, description, preserves_raw_value, confidence_default, notes
) VALUES
    ('canonical_import_fallback', 'canonical', 'weak-v1-20260521', 'canonical_name 由入库脚本 fallback 映射得到。', true, 'medium', '作为弱语义标签，不视为 PDF 原文。'),
    ('canonical_source_json', 'canonical', 'weak-v1-20260521', 'canonical_name 来自原始 raw_item JSON。', true, 'high', '作为语义标签，不替换 item_name_raw。'),
    ('canonical_unmapped', 'canonical', 'weak-v1-20260521', '未找到 canonical_name。', true, 'none', '跨公司分析时需谨慎。'),
    ('period_annual_year_to_range', 'period', 'weak-v1-20260521', '利润表/现金流量表 period_key_raw 为 YYYY，且 report_period=FY，派生年度期间 YYYY-01-01 至 YYYY-12-31。', true, 'high', '原始 period_key_raw 保留。'),
    ('period_balance_date_identity', 'period', 'weak-v1-20260521', '资产负债表 period_key_raw 为 YYYY-MM-DD，作为时点 period_end_date。', true, 'high', '原始 period_key_raw 保留。'),
    ('period_unparsed', 'period', 'weak-v1-20260521', '期间无法按当前规则解析，只保留原始 period_key_raw。', true, 'low', '后续可补充季度/半年报规则。'),
    ('unit_cny_thousand_to_cny', 'unit', 'weak-v1-20260521', '原始单位包含人民币千元，标准单位为元，scale=1000；标准化值优先由 raw_value 解析后换算，避免 value 已预缩放时二次放大。', true, 'high', '保留 unit_raw、raw_value 和 value_extracted；value_standardized 是派生值。'),
    ('unit_cny_yuan_identity', 'unit', 'weak-v1-20260521', '原始单位为元，标准单位仍为元，scale=1。', true, 'high', '保留 unit_raw 和 value_extracted。'),
    ('unit_per_share_identity', 'unit', 'weak-v1-20260521', '原始单位为元/股，标准单位仍为元/股，scale=1。', true, 'high', '每股指标不与金额指标混合比较。'),
    ('unit_raw_missing', 'unit', 'weak-v1-20260521', '原始单位为空，暂不生成标准化金额。', true, 'none', '需要回看 PDF 或表格上下文后才能确认。'),
    ('unit_unmapped', 'unit', 'weak-v1-20260521', '原始单位非空但未纳入当前规则，暂不生成标准化金额。', true, 'low', '避免误换算。')
ON CONFLICT (rule_id) DO UPDATE SET
    rule_type = EXCLUDED.rule_type,
    rule_version = EXCLUDED.rule_version,
    description = EXCLUDED.description,
    preserves_raw_value = EXCLUDED.preserves_raw_value,
    confidence_default = EXCLUDED.confidence_default,
    notes = EXCLUDED.notes;

DELETE FROM pdf2md.financial_items_enriched e
WHERE (
    e.source_table = 'financial_balance_sheet_items'
    AND NOT EXISTS (
        SELECT 1
        FROM pdf2md.financial_balance_sheet_items s
        WHERE s.task_id = e.task_id
          AND s.statement_id = e.statement_id
          AND s.item_index = e.item_index
          AND s.period_key = e.period_key_raw
    )
)
OR (
    e.source_table = 'financial_income_statement_items'
    AND NOT EXISTS (
        SELECT 1
        FROM pdf2md.financial_income_statement_items s
        WHERE s.task_id = e.task_id
          AND s.statement_id = e.statement_id
          AND s.item_index = e.item_index
          AND s.period_key = e.period_key_raw
    )
)
OR (
    e.source_table = 'financial_cash_flow_statement_items'
    AND NOT EXISTS (
        SELECT 1
        FROM pdf2md.financial_cash_flow_statement_items s
        WHERE s.task_id = e.task_id
          AND s.statement_id = e.statement_id
          AND s.item_index = e.item_index
          AND s.period_key = e.period_key_raw
    )
)
OR e.source_table NOT IN (
    'financial_balance_sheet_items',
    'financial_income_statement_items',
    'financial_cash_flow_statement_items'
);

WITH source_items AS (
    SELECT
        'financial_balance_sheet_items'::text AS source_table,
        'balance_sheet'::text AS statement_type,
        f.*
    FROM pdf2md.financial_balance_sheet_items f
    UNION ALL
    SELECT
        'financial_income_statement_items',
        'income_statement',
        f.*
    FROM pdf2md.financial_income_statement_items f
    UNION ALL
    SELECT
        'financial_cash_flow_statement_items',
        'cash_flow_statement',
        f.*
    FROM pdf2md.financial_cash_flow_statement_items f
),
enriched AS (
    SELECT
        md5(concat_ws('|', s.source_table, s.task_id, s.statement_id, s.item_index::text, s.period_key)) AS enriched_id,
        s.source_table,
        s.task_id,
        s.statement_id,
        s.item_index,
        s.period_key AS period_key_raw,
        s.company_id,
        s.stock_code,
        s.stock_name,
        s.exchange,
        c.industry,
        coalesce(nc.identity_kind, CASE WHEN nullif(s.stock_code, '') IS NOT NULL THEN 'a_share' ELSE NULL END) AS identity_kind,
        nc.market,
        nc.security_code,
        nc.synthetic_code,
        s.filing_id,
        s.parse_run_id,
        s.report_year,
        s.report_period,
        s.statement_type,
        s.statement_name,
        s.scope,
        s.scope_name,
        s.item_name AS item_name_raw,
        s.canonical_name AS canonical_label,
        CASE
            WHEN coalesce(s.raw_item->>'canonical_name', '') <> '' THEN 'source_json'
            WHEN s.canonical_name IS NOT NULL THEN 'import_fallback'
            ELSE 'unmapped'
        END AS canonical_source,
        CASE
            WHEN coalesce(s.raw_item->>'canonical_name', '') <> '' THEN 'canonical_source_json'
            WHEN s.canonical_name IS NOT NULL THEN 'canonical_import_fallback'
            ELSE 'canonical_unmapped'
        END AS canonical_rule_id,
        CASE
            WHEN s.statement_type = 'balance_sheet' AND (s.canonical_name ILIKE '%asset%' OR s.item_name LIKE '%资产%') THEN 'asset'
            WHEN s.statement_type = 'balance_sheet' AND (s.canonical_name ILIKE '%liabilit%' OR s.item_name LIKE '%负债%') THEN 'liability'
            WHEN s.statement_type = 'balance_sheet' AND (s.canonical_name ILIKE '%equity%' OR s.item_name LIKE '%权益%' OR s.item_name LIKE '%股东%') THEN 'equity'
            WHEN s.statement_type = 'income_statement' AND (s.canonical_name ILIKE '%revenue%' OR s.canonical_name ILIKE '%income%' OR s.item_name LIKE '%收入%' OR s.item_name LIKE '%收益%') THEN 'revenue_or_income'
            WHEN s.statement_type = 'income_statement' AND (s.canonical_name ILIKE '%cost%' OR s.canonical_name ILIKE '%expense%' OR s.canonical_name ILIKE '%loss%' OR s.item_name LIKE '%成本%' OR s.item_name LIKE '%费用%' OR s.item_name LIKE '%损失%') THEN 'cost_or_expense'
            WHEN s.statement_type = 'income_statement' AND (s.canonical_name ILIKE '%profit%' OR s.item_name LIKE '%利润%') THEN 'profit'
            WHEN s.statement_type = 'cash_flow_statement' AND (s.canonical_name ILIKE '%cash%' OR s.item_name LIKE '%现金%') THEN 'cash_flow'
            ELSE NULL
        END AS metric_family,
        CASE
            WHEN s.canonical_name IS NOT NULL OR s.item_name IS NOT NULL THEN 'weak-family-keyword-v1'
            ELSE NULL
        END AS metric_family_rule_id,
        s.value AS value_extracted,
        s.raw_value,
        NULLIF(btrim(coalesce(s.unit, '')), '') AS unit_raw,
        s.currency,
        CASE
            WHEN NULLIF(btrim(coalesce(s.unit, '')), '') = '元' THEN '元'
            WHEN NULLIF(btrim(coalesce(s.unit, '')), '') = '人民币千元' THEN '元'
            WHEN NULLIF(btrim(coalesce(s.unit, '')), '') = '元/股' THEN '元/股'
            ELSE NULL
        END AS unit_standardized,
        CASE
            WHEN NULLIF(btrim(coalesce(s.unit, '')), '') = '元' THEN 1::numeric
            WHEN NULLIF(btrim(coalesce(s.unit, '')), '') = '人民币千元' THEN 1000::numeric
            WHEN NULLIF(btrim(coalesce(s.unit, '')), '') = '元/股' THEN 1::numeric
            ELSE NULL
        END AS unit_scale,
        CASE
            WHEN NULLIF(btrim(coalesce(s.unit, '')), '') = '元' THEN 'unit_cny_yuan_identity'
            WHEN NULLIF(btrim(coalesce(s.unit, '')), '') = '人民币千元' THEN 'unit_cny_thousand_to_cny'
            WHEN NULLIF(btrim(coalesce(s.unit, '')), '') = '元/股' THEN 'unit_per_share_identity'
            WHEN NULLIF(btrim(coalesce(s.unit, '')), '') IS NULL THEN 'unit_raw_missing'
            ELSE 'unit_unmapped'
        END AS unit_rule_id,
        CASE
            WHEN NULLIF(btrim(coalesce(s.unit, '')), '') = '元' THEN s.value
            WHEN NULLIF(btrim(coalesce(s.unit, '')), '') = '人民币千元' THEN pdf2md.financial_parse_raw_numeric(s.raw_value) * 1000
            WHEN NULLIF(btrim(coalesce(s.unit, '')), '') = '元/股' THEN s.value
            ELSE NULL
        END AS value_standardized,
        CASE
            WHEN s.statement_type = 'balance_sheet' AND s.period_key ~ '^\d{4}-\d{2}-\d{2}$' THEN 'instant'
            WHEN s.statement_type IN ('income_statement', 'cash_flow_statement') AND s.period_key ~ '^\d{4}$' AND s.report_period = 'FY' THEN 'annual'
            ELSE NULL
        END AS period_type,
        CASE
            WHEN s.statement_type IN ('income_statement', 'cash_flow_statement') AND s.period_key ~ '^\d{4}$' AND s.report_period = 'FY' THEN make_date(s.period_key::int, 1, 1)
            ELSE NULL
        END AS period_start_date,
        CASE
            WHEN s.statement_type = 'balance_sheet' AND s.period_key ~ '^\d{4}-\d{2}-\d{2}$' THEN s.period_key::date
            WHEN s.statement_type IN ('income_statement', 'cash_flow_statement') AND s.period_key ~ '^\d{4}$' AND s.report_period = 'FY' THEN make_date(s.period_key::int, 12, 31)
            ELSE NULL
        END AS period_end_date,
        CASE
            WHEN s.statement_type = 'balance_sheet' AND s.period_key ~ '^\d{4}-\d{2}-\d{2}$' THEN 'period_balance_date_identity'
            WHEN s.statement_type IN ('income_statement', 'cash_flow_statement') AND s.period_key ~ '^\d{4}$' AND s.report_period = 'FY' THEN 'period_annual_year_to_range'
            ELSE 'period_unparsed'
        END AS period_rule_id,
        s.source_page_number,
        s.source_table_index,
        s.source_bbox,
        s.source,
        dt.heading AS source_heading,
        dt.preview AS source_preview,
        s.raw_item,
        CASE
            WHEN s.source_page_number IS NOT NULL
                AND s.canonical_name IS NOT NULL
                AND NULLIF(btrim(coalesce(s.unit, '')), '') IN ('元', '人民币千元', '元/股')
            THEN CASE WHEN coalesce(s.raw_item->>'canonical_name', '') <> '' THEN 'high' ELSE 'medium' END
            WHEN s.source_page_number IS NOT NULL THEN 'low'
            ELSE 'low'
        END AS normalization_confidence,
        to_jsonb(ARRAY_REMOVE(ARRAY[
            CASE WHEN s.source_page_number IS NULL THEN 'source_page_missing' END,
            CASE WHEN NULLIF(btrim(coalesce(s.unit, '')), '') IS NULL THEN 'unit_missing' END,
            CASE WHEN NULLIF(btrim(coalesce(s.unit, '')), '') IS NOT NULL
                AND NULLIF(btrim(coalesce(s.unit, '')), '') NOT IN ('元', '人民币千元', '元/股') THEN 'unit_unmapped' END,
            CASE WHEN NULLIF(btrim(coalesce(s.unit, '')), '') = '人民币千元'
                AND pdf2md.financial_parse_raw_numeric(s.raw_value) IS NULL THEN 'raw_value_unparsed_for_unit_scale' END,
            CASE WHEN s.canonical_name IS NULL THEN 'canonical_unmapped' END,
            CASE WHEN s.canonical_name IS NOT NULL AND coalesce(s.raw_item->>'canonical_name', '') = '' THEN 'canonical_import_fallback' END,
            CASE WHEN NOT (
                (s.statement_type = 'balance_sheet' AND s.period_key ~ '^\d{4}-\d{2}-\d{2}$')
                OR (s.statement_type IN ('income_statement', 'cash_flow_statement') AND s.period_key ~ '^\d{4}$' AND s.report_period = 'FY')
            ) THEN 'period_unparsed' END
        ], NULL)) AS quality_flags
    FROM source_items s
    LEFT JOIN pdf2md.companies c
        ON s.company_id = c.company_id
    LEFT JOIN pdf2md.non_a_share_companies nc
        ON s.company_id = nc.company_id
    LEFT JOIN pdf2md.document_tables dt
        ON s.task_id = dt.task_id
       AND s.source_table_index = dt.table_index
)
INSERT INTO pdf2md.financial_items_enriched (
    enriched_id, source_table, task_id, statement_id, item_index, period_key_raw,
    company_id, stock_code, stock_name, exchange, industry, identity_kind, market,
    security_code, synthetic_code, filing_id, parse_run_id,
    report_year, report_period, statement_type, statement_name, scope, scope_name,
    item_name_raw, canonical_label, canonical_source, canonical_rule_id,
    metric_family, metric_family_rule_id, value_extracted, raw_value, unit_raw,
    currency, unit_standardized, unit_scale, unit_rule_id, value_standardized,
    period_type, period_start_date, period_end_date, period_rule_id,
    source_page_number, source_table_index, source_bbox, source, source_heading,
    source_preview, raw_item, normalization_confidence, quality_flags
)
SELECT
    enriched_id, source_table, task_id, statement_id, item_index, period_key_raw,
    company_id, stock_code, stock_name, exchange, industry, identity_kind, market,
    security_code, synthetic_code, filing_id, parse_run_id,
    report_year, report_period, statement_type, statement_name, scope, scope_name,
    item_name_raw, canonical_label, canonical_source, canonical_rule_id,
    metric_family, metric_family_rule_id, value_extracted, raw_value, unit_raw,
    currency, unit_standardized, unit_scale, unit_rule_id, value_standardized,
    period_type, period_start_date, period_end_date, period_rule_id,
    source_page_number, source_table_index, source_bbox, source, source_heading,
    source_preview, raw_item, normalization_confidence, quality_flags
FROM enriched
ON CONFLICT (source_table, task_id, statement_id, item_index, period_key_raw)
DO UPDATE SET
    company_id = EXCLUDED.company_id,
    stock_code = EXCLUDED.stock_code,
    stock_name = EXCLUDED.stock_name,
    exchange = EXCLUDED.exchange,
    industry = EXCLUDED.industry,
    identity_kind = EXCLUDED.identity_kind,
    market = EXCLUDED.market,
    security_code = EXCLUDED.security_code,
    synthetic_code = EXCLUDED.synthetic_code,
    filing_id = EXCLUDED.filing_id,
    parse_run_id = EXCLUDED.parse_run_id,
    report_year = EXCLUDED.report_year,
    report_period = EXCLUDED.report_period,
    statement_type = EXCLUDED.statement_type,
    statement_name = EXCLUDED.statement_name,
    scope = EXCLUDED.scope,
    scope_name = EXCLUDED.scope_name,
    item_name_raw = EXCLUDED.item_name_raw,
    canonical_label = EXCLUDED.canonical_label,
    canonical_source = EXCLUDED.canonical_source,
    canonical_rule_id = EXCLUDED.canonical_rule_id,
    metric_family = EXCLUDED.metric_family,
    metric_family_rule_id = EXCLUDED.metric_family_rule_id,
    value_extracted = EXCLUDED.value_extracted,
    raw_value = EXCLUDED.raw_value,
    unit_raw = EXCLUDED.unit_raw,
    currency = EXCLUDED.currency,
    unit_standardized = EXCLUDED.unit_standardized,
    unit_scale = EXCLUDED.unit_scale,
    unit_rule_id = EXCLUDED.unit_rule_id,
    value_standardized = EXCLUDED.value_standardized,
    period_type = EXCLUDED.period_type,
    period_start_date = EXCLUDED.period_start_date,
    period_end_date = EXCLUDED.period_end_date,
    period_rule_id = EXCLUDED.period_rule_id,
    source_page_number = EXCLUDED.source_page_number,
    source_table_index = EXCLUDED.source_table_index,
    source_bbox = EXCLUDED.source_bbox,
    source = EXCLUDED.source,
    source_heading = EXCLUDED.source_heading,
    source_preview = EXCLUDED.source_preview,
    raw_item = EXCLUDED.raw_item,
    normalization_confidence = EXCLUDED.normalization_confidence,
    quality_flags = EXCLUDED.quality_flags;
