import { FileText } from 'lucide-react'
import { MarketParsingTabs } from '../components/pdf/MarketParsingTabs'
import { UsSecIngestionPanel } from '../components/sec/UsSecIngestionPanel'
import { PDF_CSS } from './pdf/pdfStyles'

export default function UsParsing() {
  return (
    <div className="secondary-page min-w-0 overflow-x-hidden">
      <style>{PDF_CSS}</style>
      <section className="secondary-hero">
        <div className="secondary-hero-inner">
          <div className="max-w-3xl">
            <div className="secondary-kicker">
              <FileText className="h-3.5 w-3.5" />
              US Report Parsing
            </div>
            <h1 className="secondary-title">美股解析</h1>
            <p className="secondary-description">
              解析 SEC 10-K、10-Q、20-F、6-K 披露文件；HTML/iXBRL 走主体、附注、表格、XBRL facts 和 evidence 关系链入库。
            </p>
          </div>
          <div className="secondary-step-row">
            <span className="secondary-step-chip is-active">美股</span>
            <span className="secondary-step-chip">SEC 解析</span>
            <span className="secondary-step-chip">关系入库</span>
          </div>
        </div>
      </section>

      <MarketParsingTabs active="US" />
      <UsSecIngestionPanel />
    </div>
  )
}
