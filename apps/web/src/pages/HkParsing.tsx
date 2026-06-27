import { MarketParsingPage } from './MarketParsingPage'
import { MarketEvidencePackagesPanel } from '../components/sec/MarketEvidencePackagesPanel'

export default function HkParsing() {
  return (
    <MarketParsingPage
      market="HK"
      title="港股解析"
      kicker="HK Report Parsing"
      description="解析 HKEX 年报、中报和季度披露，生成 Markdown、表格溯源和通用主体 Wiki 入口。"
      steps={['港股', '解析', '通用入库']}
      workflowMode="generic"
      workflowTitle="港股数据管线"
      workflowDescription="港股报告默认走通用主体 Wiki 入库与通用语义层，不触碰 A 股标准命名校验。"
      emptyTitle="选择一份港股财报后开始解析"
      emptyDescription="优先从 downloads/HK 中选择已下载 PDF；也可以上传本地港股年报或中报 PDF。"
      extraPanel={<MarketEvidencePackagesPanel market="HK" />}
    />
  )
}
