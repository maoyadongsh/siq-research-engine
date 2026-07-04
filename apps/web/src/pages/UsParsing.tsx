import { FileText, FileUp } from 'lucide-react'
import { Link } from 'react-router-dom'
import { MarketParsingTabs } from '../components/pdf/MarketParsingTabs'
import { UsSecIngestionPanel } from '../components/sec/UsSecIngestionPanel'

export default function UsParsing() {
  return (
    <div className="secondary-page min-w-0 overflow-x-hidden">
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
      <section className="secondary-panel p-4 sm:p-5">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div className="min-w-0">
            <div className="flex items-center gap-2 text-sm font-semibold text-primary">
              <FileUp className="h-4 w-4" />
              美股 PDF 兼容入口
            </div>
            <p className="mt-1 text-sm leading-6 text-text-muted">
              SEC HTML/iXBRL 是美股主链路；若遇到 PDF 附件、IR 年报、presentation 或 proxy 等文件，可进入通用 PDF 解析并限定查看 US 下载目录。
            </p>
          </div>
          <Link
            to="/parse?market=US"
            className="inline-flex h-10 shrink-0 items-center justify-center gap-2 rounded-[var(--radius-control)] border border-border bg-white px-4 text-sm font-semibold text-text hover:bg-bg"
          >
            <FileText className="h-4 w-4" />
            打开 PDF 解析
          </Link>
        </div>
      </section>
    </div>
  )
}
