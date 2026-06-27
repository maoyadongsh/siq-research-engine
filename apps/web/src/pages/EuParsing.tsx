import { MarketParsingPage } from './MarketParsingPage'
import { MarketEvidencePackagesPanel } from '../components/sec/MarketEvidencePackagesPanel'

export default function EuParsing() {
  return (
    <MarketParsingPage
      market="EU"
      title="欧股解析"
      kicker="EU Report Parsing"
      description="解析英国、法国、德国、荷兰、瑞士年报；PDF 走表格证据包，ESEF/iXBRL 后续走结构化 facts 链路。"
      steps={['欧股', '形态分流', 'IFRS 证据包']}
      workflowMode="generic"
      workflowTitle="欧股 IFRS 数据管线"
      workflowDescription="欧股统一进入 eu_ifrs schema 和 data/wiki/eu_reports；国家作为字段保留，不拆分国家级 schema。"
      emptyTitle="选择一份欧股年报后开始解析"
      emptyDescription="优先从 downloads/EU 中选择已下载 PDF；ZIP/HTML/iXBRL 会在后续结构化入口中分流处理。"
      extraPanel={<MarketEvidencePackagesPanel market="EU" />}
    />
  )
}
