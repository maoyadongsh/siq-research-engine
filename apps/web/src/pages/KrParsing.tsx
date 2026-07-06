import { MarketParsingPage } from './MarketParsingPage'
import { MarketEvidencePackagesPanel } from '../components/pdf/MarketEvidencePackagesPanel'

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
      workflowDescription="PostgreSQL 与 results 目录保存全量解析信息；Wiki 保留报告入口、公司级知识资产和轻量产物清单。"
      emptyTitle="选择一份韩股 PDF 后开始解析"
      emptyDescription="优先从 downloads/KR 中选择已下载 PDF；也支持上传本地 PDF。"
      extraPanel={<MarketEvidencePackagesPanel market="KR" />}
    />
  )
}
