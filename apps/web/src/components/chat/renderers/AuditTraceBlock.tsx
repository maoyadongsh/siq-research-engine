import { useCallback, useState, type SyntheticEvent } from 'react'
import { CheckCircle2, FileJson, Loader2, TriangleAlert } from 'lucide-react'
import { apiFetch } from '../../../lib/apiClient'
import { renderInline } from './InlineRenderer'
import { extractAnswerAuditTraceId } from './rendererUtils'
import { validationRunSummary, validationRunsForTitle } from './auditTraceUtils'

type AuditTraceStatus = 'idle' | 'loading' | 'loaded' | 'error'

export function AuditTraceBlock({
  lines,
  blockKey,
  apiPrefix = '/api',
  auditTraceId,
  title = '证据链审计详情',
}: {
  lines: string[]
  blockKey: string
  apiPrefix?: string
  auditTraceId?: string
  title?: string
}) {
  const items = lines.map((line) => line.trim()).filter(Boolean)
  const traceId = extractAnswerAuditTraceId(lines) || auditTraceId || ''
  const traceApiPrefix = apiPrefix.replace(/\/$/, '')
  const validationPassed = /全部通过/.test(title)
  const validationWarning = /待核对|失败|未通过/.test(title)
  const toneClass = validationPassed ? ' chat-audit-block-success' : validationWarning ? ' chat-audit-block-warning' : ''
  const [status, setStatus] = useState<AuditTraceStatus>('idle')
  const [trace, setTrace] = useState<unknown>(null)
  const [error, setError] = useState('')
  const [rawTraceVisible, setRawTraceVisible] = useState(false)

  const loadTrace = useCallback(async () => {
    if (!traceId || status === 'loading' || trace) return
    setStatus('loading')
    setError('')
    try {
      const response = await apiFetch(`${traceApiPrefix}/chat/audit-traces/${encodeURIComponent(traceId)}`)
      if (!response.ok) {
        throw new Error(response.status === 404 ? '审计 trace 不可用' : '审计 trace 读取失败')
      }
      const payload = await response.json()
      setTrace(payload?.trace ?? payload)
      setStatus('loaded')
    } catch (err) {
      setError(err instanceof Error ? err.message : '审计 trace 读取失败')
      setStatus('error')
    }
  }, [status, trace, traceId, traceApiPrefix])

  const validationRuns = validationRunsForTitle(trace, title).map(validationRunSummary)
  const handleToggle = (event: SyntheticEvent<HTMLDetailsElement>) => {
    if (event.currentTarget.open) void loadTrace()
  }

  return (
    <details key={blockKey} className={`chat-audit-block${toneClass}`} onToggle={handleToggle}>
      <summary className="chat-audit-summary">
        {validationPassed ? <CheckCircle2 className="chat-audit-status-icon" /> : null}
        {validationWarning ? <TriangleAlert className="chat-audit-status-icon" /> : null}
        <span>{title}</span>
      </summary>
      <div className="chat-audit-list">
        {items.length ? items.map((item, index) => (
          <div key={`${blockKey}-${index}`} className="chat-audit-item">
            {renderInline(item.replace(/^[-*+]\s+/, ''), `${blockKey}-${index}`)}
          </div>
        )) : (
          <div className="chat-audit-item">暂无可展示的审计详情。</div>
        )}
        {validationRuns.length > 0 ? (
          <div className="chat-audit-validation-records" aria-label="完整校验记录">
            <div className="chat-audit-validation-title">完整校验记录（{validationRuns.length}）</div>
            {validationRuns.map((run) => (
              <div key={`${blockKey}-validation-${run.label}-${run.details}`} className="chat-audit-validation-record">
                <div className="chat-audit-validation-meta">
                  <span>{run.label}</span>
                  <span className={`chat-audit-validation-status${run.status === '已验证' ? ' is-valid' : ' is-pending'}`}>{run.status}</span>
                </div>
                <div className="chat-audit-validation-details">{run.details}</div>
                {run.line ? <div className="chat-audit-validation-line">{run.line}</div> : null}
                <details className="chat-audit-validation-payload">
                  <summary>查看输入与结果</summary>
                  <pre>{JSON.stringify(run.payload, null, 2)}</pre>
                </details>
              </div>
            ))}
          </div>
        ) : null}
        {traceId ? (
          <div className="chat-audit-actions">
            <button
              type="button"
              className="chat-audit-action"
              onClick={() => {
                if (trace) setRawTraceVisible((visible) => !visible)
                else void loadTrace()
              }}
              disabled={status === 'loading'}
              title="读取完整审计 trace"
            >
              {status === 'loading' ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <FileJson className="h-3.5 w-3.5" />}
              <span>{rawTraceVisible ? '隐藏完整 trace' : '完整 trace'}</span>
            </button>
          </div>
        ) : null}
        {error ? <div className="chat-audit-error">{error}</div> : null}
        {trace && rawTraceVisible ? (
          <pre className="chat-audit-json">
            {JSON.stringify(trace, null, 2)}
          </pre>
        ) : null}
      </div>
    </details>
  )
}
