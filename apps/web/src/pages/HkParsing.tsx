import { MarketParsingPage } from './MarketParsingPage'
import { MarketEvidencePackagesPanel } from '../components/pdf/MarketEvidencePackagesPanel'

export default function HkParsing() {
  return (
    <MarketParsingPage
      market="HK"
      title="港股 PDF 解析"
      kicker="HK Report Parsing"
      description="解析港股 PDF 披露文件，生成 Markdown、表格证据和通用入库材料。"
      steps={['港股', '解析', '通用入库']}
      workflowMode="generic"
      workflowTitle="数据管线"
      workflowDescription="PostgreSQL 与 results 目录保存全量解析信息；Wiki package 为主证据入口，PostgreSQL 用于结构化查询和证据坐标兜底。"
      emptyTitle="选择一份港股 PDF 后开始解析"
      emptyDescription="优先从 downloads/HK 中选择已下载 PDF；也支持上传本地 PDF。"
      extraPanel={<MarketEvidencePackagesPanel market="HK" />}
    />
  )
}
