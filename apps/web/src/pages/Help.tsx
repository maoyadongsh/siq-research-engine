import { Link } from 'react-router-dom'
import {
  AlertTriangle,
  ArrowRight,
  BarChart3,
  Database,
  ExternalLink,
  FileQuestion,
  FileText,
  Files,
  FolderOpen,
  LayoutDashboard,
  MessageCircle,
  RefreshCw,
  Scale,
  Search,
  Settings,
  ShieldCheck,
  TrendingUp,
  UploadCloud,
} from 'lucide-react'
import { PageHeader, PageSection, PageShell, StatusBadge, Surface } from '@/components/page'

const entryCards = [
  { to: '/', icon: LayoutDashboard, title: '工作平台', desc: '查看 Wiki 公司、近期任务和每家公司当前缺少的分析、核查、跟踪或法务材料。' },
  { to: '/search', icon: Search, title: '搜索下载', desc: '用公司名或股票代码查询公告财报，支持年报、半年报、一季报和三季报批量下载。' },
  { to: '/parse', icon: FileText, title: '财报解析', desc: '上传 PDF 或选择已下载文件，生成 Markdown、表格、财务抽取、质量报告和原文溯源。' },
  { to: '/documents', icon: Files, title: '文档解析', desc: '上传 PDF、Office、图片、HTML 或网页 URL，生成 Markdown、JSON、表格、图片索引和可溯源抽取结果。' },
  { to: '/analysis', icon: BarChart3, title: '智能分析', desc: '展示已生成的 HTML 研究报告，并通过分析助手继续追问经营、财务和同业问题。' },
  { to: '/verify', icon: ShieldCheck, title: '事实核查', desc: '检查关键数据、公式勾稽和证据链，找出需要人工复核的结论。' },
  { to: '/tracking', icon: TrendingUp, title: '持续跟踪', desc: '沉淀跟踪事项、舆情变化和预警信号，用于后续复盘和日常监控。' },
  { to: '/legal', icon: Scale, title: '法务合规', desc: '查看法务 Agent 生成的法律意见书，并追问法规依据、合规风险和披露事项。' },
  { to: '/chat', icon: MessageCircle, title: '问答助手', desc: '面向已入库财报进行普通多轮问答，支持新建会话、查看历史和停止生成。' },
  { to: '/settings', icon: Settings, title: '设置', desc: '配置本地或云端模型连接，测试模型调用，并查看关键服务运行状态。' },
]

const recommendedFlow = [
  { icon: Search, title: '检索并下载', desc: '在搜索下载页输入公司名或股票代码，选择报告年份和交易所，下载目标财报 PDF。' },
  { icon: UploadCloud, title: '解析并校验', desc: '到财报解析页选择已下载 PDF 或直接上传，等待解析完成后查看 Markdown、质量报告和表格溯源。' },
  { icon: Database, title: '导入研究资产', desc: '确认解析结果后导入 Wiki/DB，让后续分析、核查、跟踪和问答可以读取同一份材料。' },
  { icon: BarChart3, title: '生成研究结论', desc: '进入智能分析页查看已生成报告，或使用右侧分析助手围绕当前公司继续生成分析。' },
  { icon: ShieldCheck, title: '核查与追踪', desc: '用事实核查检查数据可信度，再用持续跟踪沉淀事项、舆情和预警；需要时进入法务合规补充意见书。' },
]

const dataLocations = [
  ['Wiki 公司库', 'data/wiki/companies', '分析、核查、跟踪和法务报告最终展示来源；可通过 SIQ_WIKI_ROOT 覆盖。'],
  ['下载 PDF', 'data/market-report-finder/downloads', '搜索下载页保存的公告 PDF，财报解析页可直接选择。'],
  ['PDF 解析结果', 'data/pdf-parser/results', 'Markdown、结构化表格、质量报告和财务抽取产物。'],
  ['文档解析结果', 'data/document-parser/results', '通用 Markdown、blocks、tables、figures、source map、质量报告和抽取产物。'],
  ['聊天历史', 'data/backend/agent.db', '问答助手会话、智能体状态和成就数据。'],
]

const serviceChecks = [
  ['聚合后端', 'http://localhost:18081/health', '工作平台、报告页、聊天、设置和 Wiki 文件服务依赖它。'],
  ['PDF 下载服务', 'http://localhost:18000/health', '搜索下载页查询公告和批量下载依赖它。'],
  ['PDF 解析服务', 'http://localhost:15000/api/health', '财报解析页提交任务、查看结果和溯源依赖它。'],
  ['文档解析服务', 'http://localhost:15010/api/health', '通用文档解析页上传、URL 解析、产物下载和抽取依赖它。'],
  ['主前端', 'http://localhost:15173', '当前 React/Vite 工作台入口。'],
]

const faqs = [
  ['顶部搜索框搜什么？', '可搜索已生成的智能分析、事实核查、持续跟踪、法务 HTML 和个人文档解析产物，支持公司名、股票代码、报告类型、文件名和文档名。'],
  ['为什么报告页为空？', '通常是该公司 Wiki 目录下还没有对应 HTML。先完成 PDF 解析和导入，再运行对应 Agent 生成报告。'],
  ['解析完成后为什么看不到分析？', '解析服务只负责生成 Markdown、表格和财务抽取；智能分析、事实核查、持续跟踪和法务意见书需要对应 Agent 继续生成。'],
  ['文档解析和财报解析怎么选？', '普通合同、研报、网页、Office 和图片走文档解析；面向上市公司财报的财务抽取、勾稽校验和市场证据包继续走财报解析。'],
  ['本地模型在哪里配置？', '进入设置页，选择本地或云端供应商，填写 Base URL、模型名和 API Key，再点击测试调用。'],
  ['右侧页面 Agent 和问答助手有什么区别？', '报告页右侧 Agent 会围绕当前业务页面工作；问答助手是独立多轮对话，适合跨公司、跨报告的普通研究问答。'],
]

export default function Help() {
  return (
    <PageShell>
      <PageHeader
        icon={FileQuestion}
        eyebrow="Help Center"
        title="SIQ 操作指南"
        description="按当前真实功能整理页面入口、推荐流程、数据产物和常见排查方式。先从工作平台确认研究对象状态，再沿着下载、解析、分析、核查、跟踪和法务推进。"
        meta={['下载', '解析', '分析', '核查', '跟踪', '法务'].map((label) => (
          <StatusBadge key={label} tone="info">{label}</StatusBadge>
        ))}
      />

      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {entryCards.map((item) => (
          <Surface
            as={Link}
            key={item.to}
            to={item.to}
            kind="card"
            padding="md"
            className="group flex h-full min-h-0 sm:min-h-[168px] flex-col transition-transform hover:-translate-y-0.5 hover:border-primary/25"
          >
            <div className="flex items-start justify-between gap-4">
              <span className="premium-icon h-11 w-11 shrink-0 rounded-2xl">
                <item.icon className="h-5 w-5" />
              </span>
              <ArrowRight className="h-5 w-5 shrink-0 text-text-muted transition-all group-hover:translate-x-1 group-hover:text-primary" />
            </div>
            <h2 className="mt-4 text-base font-bold text-text">{item.title}</h2>
            <p className="mt-2 text-sm leading-6 text-text-muted">{item.desc}</p>
          </Surface>
        ))}
      </section>

      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        <PageSection
          className="xl:col-span-2"
          title="推荐工作流"
          description="从原始 PDF 到可审计研究结论。"
          actions={<StatusBadge tone="success">建议路径</StatusBadge>}
        >
          <ol className="space-y-3">
            {recommendedFlow.map((step, index) => (
              <Surface key={step.title} as="li" kind="row" padding="md" className="grid gap-4 sm:grid-cols-[44px_minmax(0,1fr)]">
                <span className="premium-icon h-11 w-11 rounded-2xl">
                  <step.icon className="h-5 w-5" />
                </span>
                <span>
                  <span className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-xs font-bold text-primary">0{index + 1}</span>
                    <strong className="text-base text-text">{step.title}</strong>
                  </span>
                  <span className="mt-1 block text-sm leading-6 text-text-muted">{step.desc}</span>
                </span>
              </Surface>
            ))}
          </ol>
        </PageSection>

        <PageSection
          title="快速排查"
          description="页面没数据时先看这里。"
          actions={<AlertTriangle className="h-5 w-5 text-warning" />}
        >
          <div className="space-y-3">
            {serviceChecks.map(([name, url, desc]) => (
              <Surface key={name} kind="row" padding="md">
                <div className="flex items-center justify-between gap-3">
                  <span className="text-sm font-bold text-text">{name}</span>
                  <StatusBadge tone="info" className="font-mono">检查</StatusBadge>
                </div>
                <a href={url} target="_blank" rel="noreferrer" className="mt-2 inline-flex items-center gap-1 break-all font-mono text-xs leading-5 text-primary hover:underline">
                  {url}
                  <ExternalLink className="h-3 w-3 shrink-0" />
                </a>
                <p className="mt-2 text-sm leading-6 text-text-muted">{desc}</p>
              </Surface>
            ))}
          </div>
        </PageSection>
      </section>

      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        <PageSection
          title="产物位置"
          description="排查数据来源时常用。"
          actions={<FolderOpen className="h-5 w-5 text-primary" />}
        >
          <div className="space-y-3">
            {dataLocations.map(([name, path, desc]) => (
              <Surface key={name} kind="row" padding="md">
                <h3 className="text-sm font-bold text-text">{name}</h3>
                <p className="mt-2 break-all font-mono text-xs leading-5 text-primary">{path}</p>
                <p className="mt-2 text-sm leading-6 text-text-muted">{desc}</p>
              </Surface>
            ))}
          </div>
        </PageSection>

        <PageSection
          className="xl:col-span-2"
          title="常见问题"
          description="围绕当前工作台能力整理。"
          actions={<RefreshCw className="h-5 w-5 text-primary" />}
        >
          <div className="grid gap-3 md:grid-cols-2">
            {faqs.map(([question, answer]) => (
              <Surface key={question} kind="row" padding="md">
                <h3 className="text-sm font-bold text-text">{question}</h3>
                <p className="mt-2 text-sm sm:text-base leading-6 text-text-muted">{answer}</p>
              </Surface>
            ))}
          </div>
        </PageSection>
      </section>
    </PageShell>
  )
}
