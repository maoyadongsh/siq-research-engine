import { MarketParsingPage } from './MarketParsingPage'
import { MarketEvidencePackagesPanel } from '../components/sec/MarketEvidencePackagesPanel'

export default function KrParsing() {
  return (
    <MarketParsingPage
      market="KR"
      title="韩股解析"
      kicker="KR Report Parsing"
      description="解析 DART 事业报告、半期报告和季度披露；优先使用 DART XBRL/API 财务数据，并以 PDF 表格提供溯源兜底。"
      steps={['韩股', 'DART 抽取', '校验入库']}
      workflowMode="generic"
      workflowTitle="韩股数据管线"
      workflowDescription="韩股按 DART 结构化披露优先处理，策略更接近美股 SEC；PDF/HTML 表格用于本地语言行名兜底和可视化证据。"
      emptyTitle="选择一份韩股披露文件后开始解析"
      emptyDescription="优先从 downloads/KR 中选择已下载 PDF；DART XBRL/API 产物可通过后端规则服务抽取结构化指标。"
      extraPanel={<MarketEvidencePackagesPanel market="KR" />}
    />
  )
}
