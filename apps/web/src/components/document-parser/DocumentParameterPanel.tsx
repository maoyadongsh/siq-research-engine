import type { DocumentParseConfig } from '@/lib/documentTypes'

const modelOptions = [
  ['auto', '自动'],
  ['pipeline', 'Pipeline'],
  ['vlm', 'VLM'],
  ['MinerU-HTML', 'HTML'],
]

export function DocumentParameterPanel({
  config,
  onChange,
}: {
  config: DocumentParseConfig
  onChange: (next: DocumentParseConfig) => void
}) {
  const set = <K extends keyof DocumentParseConfig>(key: K, value: DocumentParseConfig[K]) => {
    onChange({ ...config, [key]: value })
  }

  const toggleFormat = (format: string) => {
    const has = config.extraFormats.includes(format)
    set('extraFormats', has ? config.extraFormats.filter((item) => item !== format) : [...config.extraFormats, format])
  }

  return (
    <section className="doc-panel">
      <div className="doc-panel-head">
        <div>
          <h2>解析参数</h2>
          <p>参数语义稳定，provider 负责映射到本地解析器或 MinerU。</p>
        </div>
      </div>
      <div className="doc-panel-body grid gap-3">
        <div className="doc-field">
          <label className="doc-label">模型版本</label>
          <div className="doc-segment" role="tablist" aria-label="模型版本">
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
          <label className="doc-field">
            <span className="doc-label">OCR</span>
            <select className="doc-select" value={config.ocr} onChange={(event) => set('ocr', event.target.value)}>
              <option value="auto">自动 OCR</option>
              <option value="force">强制 OCR</option>
              <option value="off">关闭 OCR</option>
            </select>
          </label>
          <label className="doc-field">
            <span className="doc-label">语言</span>
            <select className="doc-select" value={config.language} onChange={(event) => set('language', event.target.value)}>
              <option value="auto">自动</option>
              <option value="ch">中文</option>
              <option value="en">英文</option>
              <option value="ja">日文</option>
              <option value="ko">韩文</option>
              <option value="multi">多语言</option>
            </select>
          </label>
        </div>

        <label className="doc-field">
          <span className="doc-label">页码范围</span>
          <input
            className="doc-input"
            value={config.pageRanges}
            onChange={(event) => set('pageRanges', event.target.value)}
            placeholder="例如 1-20,24,30-32"
          />
        </label>

        <div className="doc-toggle-grid">
          <label className="doc-check">
            <input type="checkbox" checked={config.enableTable} onChange={(event) => set('enableTable', event.target.checked)} />
            表格识别
          </label>
          <label className="doc-check">
            <input type="checkbox" checked={config.enableFormula} onChange={(event) => set('enableFormula', event.target.checked)} />
            公式识别
          </label>
          <label className="doc-check">
            <input type="checkbox" checked={!config.noCache} onChange={(event) => set('noCache', !event.target.checked)} />
            使用缓存
          </label>
          {['html', 'docx', 'latex', 'zip'].map((format) => (
            <label className="doc-check" key={format}>
              <input type="checkbox" checked={config.extraFormats.includes(format)} onChange={() => toggleFormat(format)} />
              导出 {format.toUpperCase()}
            </label>
          ))}
        </div>
      </div>
    </section>
  )
}
