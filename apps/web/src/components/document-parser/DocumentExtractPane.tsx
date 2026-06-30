import { useMemo, useState } from 'react'
import { Button } from '@/components/ui/button'
import type { DocumentExtractionTemplate } from '@/lib/documentTypes'
import { stringify } from './documentResultWorkbenchUtils'

const DEFAULT_SCHEMA_TEXT = '{\n  "type": "object",\n  "properties": {\n    "title": { "type": "string" }\n  }\n}'
const DEFAULT_INSTRUCTIONS = '只从原文抽取，不确定则返回 null。'

type EvidenceRecord = Record<string, unknown>

export type DocumentExtractPaneProps = {
  extractionResult: Record<string, unknown> | null
  extractionTemplates: DocumentExtractionTemplate[]
  onRunExtraction: (schemaText: string, instructions: string, templateId?: string) => Promise<void>
  openResource: (url: string, filename?: string) => void | Promise<void>
}

function objectRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
}

function evidenceRecordList(value: unknown): EvidenceRecord[] {
  return Array.isArray(value)
    ? value.filter((item): item is EvidenceRecord => Boolean(objectRecord(item)))
    : []
}

export function DocumentExtractPane({
  extractionResult,
  extractionTemplates,
  onRunExtraction,
  openResource,
}: DocumentExtractPaneProps) {
  const [schemaText, setSchemaText] = useState(DEFAULT_SCHEMA_TEXT)
  const [instructions, setInstructions] = useState(DEFAULT_INSTRUCTIONS)
  const [templateId, setTemplateId] = useState('')

  const selectedTemplate = useMemo(() => {
    return extractionTemplates.find((item) => item.template_id === templateId)
  }, [extractionTemplates, templateId])
  const validationReport = useMemo(() => {
    return objectRecord(extractionResult?.validation_report)
  }, [extractionResult])
  const evidenceMap = useMemo(() => {
    const rawMap = objectRecord(extractionResult?.evidence_map) || {}
    return Object.fromEntries(
      Object.entries(rawMap).map(([field, evidences]) => [field, evidenceRecordList(evidences)])
    )
  }, [extractionResult])
  const evidenceEntries = Object.entries(evidenceMap)
  const missingFields = Array.isArray(validationReport?.missing_fields) ? validationReport.missing_fields : []

  const applyTemplate = (nextTemplateId: string) => {
    setTemplateId(nextTemplateId)
    const template = extractionTemplates.find((item) => item.template_id === nextTemplateId)
    if (!template) return
    setSchemaText(JSON.stringify(template.schema || {}, null, 2))
    setInstructions(template.instructions || DEFAULT_INSTRUCTIONS)
  }

  return (
    <div className="grid gap-4 p-4 lg:grid-cols-[minmax(0,.95fr)_minmax(0,1.05fr)]">
      <div className="grid gap-3">
        <label className="doc-field">
          <span className="doc-label">抽取模板</span>
          <select className="doc-select" value={templateId} onChange={(event) => applyTemplate(event.target.value)}>
            <option value="">自定义 JSON Schema</option>
            {extractionTemplates.map((template) => (
              <option key={template.template_id} value={template.template_id}>
                {template.name || template.template_id}
              </option>
            ))}
          </select>
        </label>
        {selectedTemplate ? (
          <div className="doc-data-row">
            <h3>{selectedTemplate.name || selectedTemplate.template_id}</h3>
            <p>{selectedTemplate.description || '模板 schema 已载入，可直接运行抽取。'}</p>
          </div>
        ) : null}
        <label className="doc-field">
          <span className="doc-label">JSON Schema</span>
          <textarea className="doc-textarea" value={schemaText} onChange={(event) => setSchemaText(event.target.value)} />
        </label>
        <label className="doc-field">
          <span className="doc-label">抽取指令</span>
          <input className="doc-input" value={instructions} onChange={(event) => setInstructions(event.target.value)} />
        </label>
        <Button type="button" onClick={() => void onRunExtraction(schemaText, instructions, templateId)}>运行抽取</Button>
        {validationReport ? (
          <div className="doc-data-row">
            <h3>{validationReport.schema_valid ? 'Schema 有效' : 'Schema 需检查'}</h3>
            <p>
              evidence coverage {String(validationReport.evidence_coverage_ratio ?? 0)}
              {missingFields.length ? ` · 缺失 ${missingFields.map(String).join(', ')}` : ''}
            </p>
          </div>
        ) : null}
      </div>
      <div className="grid gap-3">
        <pre className="doc-json">{stringify(extractionResult || { status: 'not_run' })}</pre>
        {evidenceEntries.length ? (
          <div className="doc-table-list">
            <div className="doc-data-row">
              <h3>字段证据</h3>
              <p>每个非空字段会保留 evidence id、页码和原文片段。</p>
            </div>
            {evidenceEntries.map(([field, evidences]) => (
              <div className="doc-data-row" key={field}>
                <h3>{field}</h3>
                {evidences.length ? evidences.map((evidence, index) => (
                  <p key={`${field}-${index}`}>
                    p{String(evidence.page_number || 1)} · {String(evidence.quote || '')}
                    {evidence.open_source_url ? (
                      <>
                        {' · '}
                        <button
                          type="button"
                          className="doc-source-link"
                          onClick={() => void openResource(String(evidence.open_source_url), `${field}-evidence.json`)}
                        >
                          打开证据
                        </button>
                      </>
                    ) : null}
                  </p>
                )) : <p>未找到证据，结果保持 null 或需人工复核。</p>}
              </div>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  )
}
