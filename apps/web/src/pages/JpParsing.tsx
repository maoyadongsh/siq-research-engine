import { MarketParsingPage } from './MarketParsingPage'
import { MarketEvidencePackagesPanel } from '../components/sec/MarketEvidencePackagesPanel'

export default function JpParsing() {
  return (
    <MarketParsingPage
      market="JP"
      title="日股解析"
      kicker="JP Report Parsing"
      description="解析 EDINET 有价证券报告书、半期报告书和季度披露；优先使用 XBRL/iXBRL 结构化数据，并保留 PDF 表格溯源兜底。"
      steps={['日股', 'XBRL 抽取', '表格溯源']}
      workflowMode="generic"
      workflowTitle="日股数据管线"
      workflowDescription="日股按 EDINET 结构化披露优先处理，不深度复刻 A 股 PDF 解析；PDF 表格用于补充可视化证据与缺失字段。"
      emptyTitle="选择一份日股披露文件后开始解析"
      emptyDescription="优先从 downloads/JP 中选择已下载 PDF；EDINET XBRL/iXBRL 产物可通过后端规则服务抽取结构化指标。"
      extraPanel={<MarketEvidencePackagesPanel market="JP" />}
    />
  )
}
