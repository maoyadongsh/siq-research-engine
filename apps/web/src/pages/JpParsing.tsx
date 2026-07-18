import { MarketParsingPage } from './MarketParsingPage'

export default function JpParsing() {
  return (
    <MarketParsingPage
      market="JP"
      title="日本市场财报解析"
      kicker="JP Report Parsing"
      description="解析日股 PDF 披露文件，生成 Markdown、表格证据和 PostgreSQL 入库材料。"
      steps={['日股', '解析', '数据管线']}
      workflowMode="generic"
      workflowTitle="数据管线"
      workflowDescription="解析产物与 results 目录保存全量解析信息；LLM-Wiki、Wiki语义增强和 PostgreSQL 入库都读取同一套解析产物。"
      emptyTitle="选择一份日股 PDF 后开始解析"
      emptyDescription="优先从 downloads/JP 中选择已下载 PDF；也支持上传本地 PDF。"
    />
  )
}
