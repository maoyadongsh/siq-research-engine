import { MarketParsingPage } from './MarketParsingPage'

export default function KrParsing() {
  return (
    <MarketParsingPage
      market="KR"
      title="韩股 PDF 解析"
      kicker="KR Report Parsing"
      description="解析韩股 PDF 披露文件，生成 Markdown、表格证据和通用入库材料。"
      steps={['韩股', '解析', '通用入库']}
      workflowMode="generic"
      workflowTitle="数据管线"
      workflowDescription="解析产物与 results 目录保存全量解析信息；PostgreSQL 直接从解析产物入库，Wiki 作为解析产物派生的公司级知识资产。"
      emptyTitle="选择一份韩股 PDF 后开始解析"
      emptyDescription="优先从 downloads/KR 中选择已下载 PDF；也支持上传本地 PDF。"
    />
  )
}
