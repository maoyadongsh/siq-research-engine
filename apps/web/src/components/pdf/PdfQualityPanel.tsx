import type { QualityReport } from '../../lib/pdfTypes'
import { candidateMeta, suspectReasons, suspectTableMeta } from '../../lib/pdfFormatting'
import {
  pdfCoreTablesLabel,
  pdfIndicatorCandidatesLabel,
  pdfKeyCandidatesLabel,
  pdfNoIndicatorCandidatesLabel,
  pdfQualityPanelTitle,
} from './pdfMarketPanelLabels'

export interface PdfQualityPanelProps {
  quality: QualityReport
  market?: string | null
  onShowTableSource: (tableIndex: number, line?: number) => void
}

export function PdfQualityPanel({ quality, market, onShowTableSource }: PdfQualityPanelProps) {
  const core = quality.core_financial_table_candidates || []
  const key = quality.key_table_candidates || {}
  const ind = (quality.indicator_table_candidates || []).filter((i) => i.status === 'found')
  const cFound = core.filter((i) => i.status === 'found')
  const susp = quality.suspicious_tables || []
  const currencies = quality.detected_currencies?.length ? quality.detected_currencies.join('、') : quality.currency
  const profile = [quality.market || quality.market_profile, quality.accounting_standard, quality.industry_profile, currencies, quality.unit]
    .filter(Boolean)
    .join(' / ')

  return (
    <div className="apple-card rounded-[24px] p-4 sm:p-6">
      <h3 className="text-base font-semibold text-text mb-3">{pdfQualityPanelTitle(market)}</h3>
      {profile ? <p className="mb-3 text-xs font-medium text-text-muted">{profile}</p> : null}
      <div className="pdf-quality-grid">
        <div>
          <strong>{quality.table_count || 0}</strong>
          <span>表格</span>
        </div>
        <div>
          <strong>{quality.single_row_table_count || 0}</strong>
          <span>单行/空壳表</span>
        </div>
        <div>
          <strong>{Math.round((quality.single_row_table_ratio || 0) * 1000) / 10}%</strong>
          <span>空壳比例</span>
        </div>
        <div>
          <strong>{quality.image_ref_count || 0}</strong>
          <span>图片引用</span>
        </div>
        <div>
          <strong>{(quality.suspicious_tables || []).length}</strong>
          <span>可疑表样本</span>
        </div>
      </div>
      <div className="pdf-quality-row">
        <span>核心章节</span>
        <b>
          {(quality.found_sections || []).length}/
          {(quality.found_sections || []).length + (quality.missing_sections || []).length}
        </b>
      </div>
      <div className="pdf-quality-row">
        <span>{pdfCoreTablesLabel(market)}</span>
        <b>
          {core.length
            ? `${cFound.length}/${core.length} · ${cFound.map((i) => i.name).join('、') || '未识别'}`
            : (quality.found_financial_tables || []).join('、') || '未识别'}
        </b>
      </div>
      <div className="pdf-quality-section">
        <div className="pdf-quality-section-title">{pdfKeyCandidatesLabel(market)}</div>
        <div className="pdf-chip-row">
          {core.length ? (
            core.map((c, i) =>
              c.table_index && c.status !== 'missing' ? (
                <button
                  key={i}
                  className="pdf-chip trace-chip"
                  onClick={() => onShowTableSource(Number(c.table_index), Number(c.line))}
                >
                  {String(c.name || '候选表')} · {candidateMeta(c)}
                </button>
              ) : (
                <span key={i} className="pdf-chip pdf-chip-missing">
                  {String(c.name || '候选表')} · {candidateMeta(c)}
                </span>
              ),
            )
          ) : Object.keys(key).length ? (
            Object.keys(key)
              .slice(0, 8)
              .map((name) => {
                const first = key[name]?.[0]
                return first?.table_index ? (
                  <button
                    key={name}
                    className="pdf-chip trace-chip"
                    onClick={() => onShowTableSource(Number(first.table_index), Number(first.line))}
                  >
                    {name} · {candidateMeta(first)}
                  </button>
                ) : (
                  <span key={name} className="pdf-chip">
                    {name}
                  </span>
                )
              })
          ) : (
            <span className="text-text-muted text-sm">未定位到候选表</span>
          )}
        </div>
      </div>
      <div className="pdf-quality-section">
        <div className="pdf-quality-section-title">{pdfIndicatorCandidatesLabel(market)}</div>
        <div className="pdf-chip-row">
          {ind.length ? (
            ind.map((c, i) => (
              <button
                key={i}
                className="pdf-chip trace-chip pdf-chip-secondary"
                onClick={() => onShowTableSource(Number(c.table_index), Number(c.line))}
              >
                {String(c.name || '候选表')} · {candidateMeta(c)}
              </button>
            ))
          ) : (
            <span className="text-text-muted text-sm">{pdfNoIndicatorCandidatesLabel(market)}</span>
          )}
        </div>
      </div>
      <div className="pdf-quality-section">
        <div className="pdf-quality-section-title">优先复核表</div>
        <ul className="list-disc pl-5 text-sm text-text">
          {susp.length ? (
            susp.map((s, i) => (
              <li key={i}>
                {s.table_index ? (
                  <button className="pdf-trace-btn" onClick={() => onShowTableSource(Number(s.table_index), Number(s.line))}>
                    {suspectTableMeta(s)}
                  </button>
                ) : (
                  <span className="text-text-muted">{suspectTableMeta(s)}</span>
                )}
                {' · '}
                {suspectReasons((s.suspect_reasons as string[]) || [])}
              </li>
            ))
          ) : (
            <li>未发现可疑表样本</li>
          )}
        </ul>
      </div>
      {quality.warnings?.length ? (
        <ul className="list-disc pl-5 mt-2.5 text-sm text-warning">
          {quality.warnings.map((w, i) => (
            <li key={i}>{w}</li>
          ))}
        </ul>
      ) : (
        <ul className="list-disc pl-5 mt-2.5 text-sm text-warning">
          <li>未发现明显质量告警</li>
        </ul>
      )}
    </div>
  )
}
