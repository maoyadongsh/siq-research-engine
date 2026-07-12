import { MarketParsingPage } from './MarketParsingPage'

export default function EuParsing() {
  return (
    <MarketParsingPage
      market="EU"
      title="欧股财报解析"
      kicker="EU Report Parsing"
      description="解析欧股 PDF 与 ESEF/iXBRL/XHTML/ZIP 披露文件，生成结构化解析产物和 PostgreSQL 入库材料。"
      steps={['欧股', '解析', '数据管线']}
      workflowMode="generic"
      workflowTitle="数据管线"
      workflowDescription="解析产物与 results 目录保存全量解析信息；LLM-Wiki、Wiki语义增强和 PostgreSQL 入库都读取同一套解析产物。"
      emptyTitle="选择一份欧股披露文件后开始解析"
      emptyDescription="downloads/EU 中的 PDF 进入 PDF parser，ESEF/iXBRL/XHTML/ZIP 直接生成结构化解析产物。"
    />
  )
}
