import type { DocumentParseConfig } from '@/lib/documentTypes'

const modelOptions = [
  ['auto', '自动'],
  ['pipeline', '快速模式'],
  ['vlm', '增强模式'],
  ['MinerU-HTML', 'HTML 模式'],
]

export function DocumentParameterPanel({
  config,
  onChange,
  defaultOpen = false,
}: {
  config: DocumentParseConfig
  onChange: (next: DocumentParseConfig) => void
  defaultOpen?: boolean
}) {
  const set = <K extends keyof DocumentParseConfig>(key: K, value: DocumentParseConfig[K]) => {
    onChange({ ...config, [key]: value })
  }

  const toggleFormat = (format: string) => {
    const has = config.extraFormats.includes(format)
    set('extraFormats', has ? config.extraFormats.filter((item) => item !== format) : [...config.extraFormats, format])
  }

  return (
    <details className="doc-panel" open={defaultOpen}>
      <summary className="doc-panel-head">
        <div>
          <h2>解析参数</h2>
          <p>参数语义稳定，服务端负责映射到可用解析能力。</p>
        </div>
      </summary>
      <div className="doc-panel-body grid gap-3">
        <div className="doc-field">
          <span className="doc-label" id="doc-model-version-label">模型版本</span>
          <div className="doc-segment" role="tablist" aria-labelledby="doc-model-version-label">
            {modelOptions.map(([value, label]) => (
              <button
                key={value}
                type="button"
                className={config.modelVersion === value ? 'active' : ''}
                onClick={() => set('modelVersion', value)}
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          <label className="doc-field" htmlFor="doc-ocr">
            <span className="doc-label">OCR</span>
            <select id="doc-ocr" className="doc-select" name="document-ocr" value={config.ocr} onChange={(event) => set('ocr', event.target.value)}>
              <option value="auto">自动 OCR</option>
              <option value="force">强制 OCR</option>
              <option value="off">关闭 OCR</option>
            </select>
          </label>
          <label className="doc-field" htmlFor="doc-language">
            <span className="doc-label">语言</span>
            <select id="doc-language" className="doc-select" name="document-language" value={config.language} onChange={(event) => set('language', event.target.value)}>
              <option value="auto">自动</option>
              <option value="ch">中文</option>
              <option value="en">英文</option>
              <option value="ja">日文</option>
              <option value="ko">韩文</option>
              <option value="multi">多语言</option>
            </select>
          </label>
        </div>

        <label className="doc-field" htmlFor="doc-page-ranges">
          <span className="doc-label">页码范围</span>
          <input
            id="doc-page-ranges"
            className="doc-input"
            name="document-page-ranges"
            autoComplete="off"
            value={config.pageRanges}
            onChange={(event) => set('pageRanges', event.target.value)}
            placeholder="例如 1-20,24,30-32"
          />
        </label>

        <div className="doc-toggle-grid">
          <label className="doc-check" htmlFor="doc-enable-table">
            <input id="doc-enable-table" type="checkbox" name="document-enable-table" checked={config.enableTable} onChange={(event) => set('enableTable', event.target.checked)} />
            表格识别
          </label>
          <label className="doc-check" htmlFor="doc-enable-formula">
            <input id="doc-enable-formula" type="checkbox" name="document-enable-formula" checked={config.enableFormula} onChange={(event) => set('enableFormula', event.target.checked)} />
            公式识别
          </label>
          <label className="doc-check" htmlFor="doc-use-cache">
            <input id="doc-use-cache" type="checkbox" name="document-use-cache" checked={!config.noCache} onChange={(event) => set('noCache', !event.target.checked)} />
            使用缓存
          </label>
          {['html', 'docx', 'latex', 'zip'].map((format) => (
            <label className="doc-check" htmlFor={`doc-export-${format}`} key={format}>
              <input id={`doc-export-${format}`} type="checkbox" name={`document-export-${format}`} checked={config.extraFormats.includes(format)} onChange={() => toggleFormat(format)} />
              导出 {format.toUpperCase()}
            </label>
          ))}
        </div>
      </div>
    </details>
  )
}
