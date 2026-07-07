import { useSearchParams } from 'react-router-dom'
import { PdfParsingWorkbench } from '../features/pdf-parsing/PdfParsingWorkbench'
import type { MarketParsingCode } from './MarketParsingPage'

type PdfParsingMarket = MarketParsingCode

function parseMarketParam(value: string | null): PdfParsingMarket {
  const market = String(value || 'CN').toUpperCase()
  return market === 'CN' || market === 'HK' || market === 'US' || market === 'JP' || market === 'KR' || market === 'EU'
    ? market
    : 'CN'
}

const marketCopy: Record<PdfParsingMarket, { title: string; description: string; emptyTitle: string; emptyDescription: string }> = {
  CN: {
    title: '智能解析',
    description: '上传财报 PDF，生成 Markdown、表格、财务数据抽取和可视化溯源结果。',
    emptyTitle: '选择一份财报后开始解析',
    emptyDescription: '可从已下载列表直接解析，也支持批量上传最多 5 个 PDF。',
  },
  HK: {
    title: '港股 PDF 解析',
    description: '解析港股 PDF 披露文件，生成 Markdown、表格证据和通用主体 Wiki 入库材料。',
    emptyTitle: '选择一份港股 PDF 后开始解析',
    emptyDescription: '优先从 downloads/HK 中选择已下载 PDF；也支持上传本地 PDF。',
  },
  US: {
    title: '美股 PDF 解析',
    description: '用于 SEC PDF 附件、IR 年报、presentation、proxy 等非 HTML/iXBRL 主披露文件；10-K/10-Q 主链路请回到美股 SEC 工作台。',
    emptyTitle: '选择一份美股 PDF 后开始解析',
    emptyDescription: '优先从 downloads/US 中选择 PDF；SEC HTML/iXBRL 请使用美股解析页的结构化入库工作台。',
  },
  EU: {
    title: '欧股 PDF 解析',
    description: '解析欧股 PDF 年报；ESEF ZIP、XHTML、iXBRL 和 HTML 文件仍走欧股结构化证据包入口。',
    emptyTitle: '选择一份欧股 PDF 后开始解析',
    emptyDescription: '优先从 downloads/EU 中选择 PDF；非 PDF 年报请使用欧股解析页的结构化入口。',
  },
  JP: {
    title: '日股 PDF 解析',
    description: '解析日股 PDF 披露文件，生成 Markdown、表格证据和通用入库材料。',
    emptyTitle: '选择一份日股 PDF 后开始解析',
    emptyDescription: '优先从 downloads/JP 中选择已下载 PDF；也支持上传本地 PDF。',
  },
  KR: {
    title: '韩股 PDF 解析',
    description: '解析韩股 PDF 披露文件，生成 Markdown、表格证据和通用入库材料。',
    emptyTitle: '选择一份韩股 PDF 后开始解析',
    emptyDescription: '优先从 downloads/KR 中选择已下载 PDF；DART HTML/XML 请使用韩股解析页的结构化入口。',
  },
}

export default function PdfParsing() {
  const [searchParams] = useSearchParams()
  const market = parseMarketParam(searchParams.get('market'))
  const copy = marketCopy[market]

  return (
    <PdfParsingWorkbench
      market={market}
      title={copy.title}
      description={copy.description}
      emptyTitle={copy.emptyTitle}
      emptyDescription={copy.emptyDescription}
      workflowMode="standard"
      workflowTitle="数据管线"
      workflowDescription="解析产物与 results 目录保存全量解析信息；PostgreSQL 直接从解析产物入库，Wiki 作为解析产物派生的公司级知识资产。"
    />
  )
}
