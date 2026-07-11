import { MarketParsingPage } from './MarketParsingPage'

export default function EuParsing() {
  return (
    <MarketParsingPage
      market="EU"
      title="欧股 PDF 解析"
      kicker="EU Report Parsing"
      description="解析欧股 PDF 年报，生成 Markdown、表格证据和 PostgreSQL 入库材料。"
      steps={['欧股', '解析', '数据管线']}
      workflowMode="generic"
      workflowTitle="数据管线"
      workflowDescription="解析产物与 results 目录保存全量解析信息；LLM-Wiki、Wiki语义增强和 PostgreSQL 入库都读取同一套解析产物。"
      emptyTitle="选择一份欧股 PDF 后开始解析"
      emptyDescription="优先从 downloads/EU 中选择已下载 PDF；也支持上传本地 PDF。"
    />
  )
}
