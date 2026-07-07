import { Link } from 'react-router-dom'
import {
  AlertTriangle,
  ArrowRight,
  BarChart3,
  BriefcaseBusiness,
  Database,
  DatabaseZap,
  ExternalLink,
  FileQuestion,
  FileText,
  Files,
  FolderOpen,
  Landmark,
  LayoutDashboard,
  MessageCircle,
  MonitorCog,
  RefreshCw,
  Scale,
  Search,
  Settings,
  ShieldCheck,
  TrendingUp,
  UploadCloud,
  UserRound,
  type LucideIcon,
} from 'lucide-react'
import { PageHeader, PageSection, PageShell, StatusBadge, Surface } from '@/components/page'

type GuideEntry = {
  to: string
  icon: LucideIcon
  title: string
  desc: string
}

const workspaceEntries: GuideEntry[] = [
  { to: '/', icon: LayoutDashboard, title: '工作平台', desc: '查看研究对象、近期任务、研究资产缺口和已生成的分析/核查/跟踪/法务材料。' },
  { to: '/search', icon: Search, title: '搜索下载', desc: '按市场检索官方披露：A 股、港股、SEC、美欧、韩国 DART、日本 EDINET/IR。' },
  { to: '/parse', icon: FileText, title: '通用财报解析', desc: '上传 PDF 或选择 downloads 中的文件，生成 Markdown、表格索引、质量报告、财务抽取和原文溯源。' },
  { to: '/documents', icon: Files, title: '文档解析', desc: '处理合同、研报、网页、Office、图片和通用 PDF，输出 blocks、tables、figures、source map 和抽取结果。' },
  { to: '/analysis', icon: BarChart3, title: '智能分析', desc: '查看已生成的 HTML 研究报告，并围绕当前公司继续追问经营、财务和同业问题。' },
  { to: '/chat', icon: MessageCircle, title: '问答助手', desc: '独立多轮问答入口，适合跨公司、跨报告检索和研究讨论，支持历史会话。' },
]

const marketEntries: GuideEntry[] = [
  { to: '/parse?market=CN', icon: FileText, title: 'A 股 PDF', desc: 'A 股年报/半年报/季报走通用 PDF 解析，支持三大表、主要会计数据、财务指标和勾稽校验。' },
  { to: '/parse-hk', icon: Database, title: '港股 PDF', desc: '解析 HKEX PDF，生成解析产物、质量报告、表格证据和 PostgreSQL 入库材料。' },
  { to: '/parse-us', icon: DatabaseZap, title: '美股 SEC', desc: 'SEC HTML/iXBRL 是主链路，覆盖 facts、tables、evidence 和 case set；PDF 附件可回到通用解析。' },
  { to: '/parse-eu', icon: Landmark, title: '欧洲 ESEF/PDF', desc: '覆盖 UK、法国、德国、荷兰、瑞士等官方源，支持 XHTML/iXBRL、ZIP 或 PDF 产物链路。' },
  { to: '/parse-jp', icon: FileQuestion, title: '日本 EDINET', desc: '以有价证券报告书为主，质量报告识别 Financial Highlights、日本核心报表和 JP 专属一致性校验。' },
  { to: '/parse-kr', icon: FileText, title: '韩国 DART', desc: '支持 DART 官方 PDF；配置 DART_API_KEY 后可增强为 OpenDART ZIP 和结构化 package。' },
]

const operationEntries: GuideEntry[] = [
  { to: '/verify', icon: ShieldCheck, title: '事实核查', desc: '检查关键数据、公式勾稽和证据链，定位需要人工复核的结论。' },
  { to: '/tracking', icon: TrendingUp, title: '持续跟踪', desc: '沉淀跟踪事项、舆情变化和预警信号，用于复盘与日常监控。' },
  { to: '/legal', icon: Scale, title: '法务合规', desc: '查看法务 Agent 生成的法律意见书，并追问法规依据、披露事项和合规风险。' },
  { to: '/deals', icon: BriefcaseBusiness, title: '交易工作台', desc: '管理交易项目、资料室、证据、工作流、决策和审计材料。' },
  { to: '/primary-market', icon: Landmark, title: '一级市场', desc: '维护一级市场项目、材料和会议记录，沉淀投研与沟通资产。' },
  { to: '/vector-ingest', icon: DatabaseZap, title: '向量入库', desc: '系统管理员入口，用于将通过质量复核的解析产物、文档和研究资产送入检索集合。' },
  { to: '/settings', icon: Settings, title: '设置', desc: '配置模型供应商、Base URL、API Key、演示登录默认值和服务状态。' },
  { to: '/account', icon: UserRound, title: '账户', desc: '查看当前账户、角色和可访问的系统能力；受限页面会根据权限显示。' },
]

const recommendedFlow = [
  { icon: Search, title: '检索并下载', desc: '在搜索下载页选择市场，输入公司名、股票代码、CIK、ISIN、LEI、EDINET Code 或 DART Corp Code，下载目标披露。' },
  { icon: UploadCloud, title: '选择正确解析入口', desc: 'A 股和普通 PDF 走通用财报解析；港股/日本/韩国/欧洲走各自市场页；美股 SEC HTML/iXBRL 优先走 /parse-us。' },
  { icon: ShieldCheck, title: '复核质量与财务校验', desc: '查看质量报告、核心表候选、可疑表样本、财务抽取和一致性检查；从表格、PDF 页码和 bbox 回跳原文。' },
  { icon: Database, title: '入库并生成研究资产', desc: '确认解析质量后，从解析产物写入 PostgreSQL，并生成研究资产和派生知识资产；需要检索问答时再做向量入库。' },
  { icon: BarChart3, title: '生成研究与追踪结论', desc: '进入智能分析、事实核查、持续跟踪和法务合规页面生成或查看报告，再用问答助手做跨材料追问。' },
]

const dataLocations = [
  ['下载文件', 'data/market-report-finder/downloads', '搜索下载页保存的 PDF、HTML、XHTML、ZIP 等原始披露文件。'],
  ['PDF 解析结果', 'data/pdf-parser/results', 'Markdown、content_list、table_index、quality_report、financial_data、financial_checks 和页图。'],
  ['文档解析结果', 'data/document-parser/results', '通用文档的 Markdown、blocks、tables、figures、source map、quality 和抽取产物。'],
  ['后端会话与运行数据', 'data/backend/agent.db', '问答助手会话、智能体状态、历史消息和本地运行记录。'],
  ['运行日志', 'var/logs', '本地前端、PDF parser 等服务的守护日志；排查公网展示和服务重启时常用。'],
  ['派生知识资产兼容目录', 'data/wiki/companies', '历史 Wiki 文件服务使用的公司级派生知识资产位置；不是解析或 PostgreSQL 的主数据源，可通过 SIQ_WIKI_ROOT 覆盖。'],
  ['市场解析产物兼容目录', 'data/wiki/{hk_reports,us_sec,eu_reports,jp_reports,kr_reports}', '历史 Evidence Package 目录，保存多市场解析产物包、quality、metrics、source map、manifest 和导入状态。'],
]

const serviceChecks = [
  ['公网入口', 'https://arthurmao.synology.me:9391', '当前对外访问的 Web 工作台入口，通常反代到本机 Vite 前端。'],
  ['聚合后端', 'http://localhost:18081/health', '工作平台、报告页、聊天、设置、鉴权和派生知识资产文件服务依赖它。'],
  ['PDF 下载服务', 'http://localhost:18000/health', '搜索下载页查询公告、批量下载和市场官方源状态依赖它。'],
  ['PDF 解析服务', 'http://localhost:15000/api/health', '财报解析、质量报告、表格溯源、财务抽取和 JP/KR/HK/EU PDF 解析依赖它。'],
  ['文档解析服务', 'http://localhost:15010/api/health', '通用文档上传、URL 解析、产物下载和抽取依赖它。'],
  ['主前端', 'http://localhost:15173', '本机 React/Vite 工作台入口；公网更新异常时先确认此端口。'],
]

const faqs = [
  ['多市场入口怎么选？', '先用搜索下载拿到官方披露。A 股和普通 PDF 用财报解析；港股、日本、韩国、欧洲用对应市场页；美股 SEC HTML/iXBRL 用美股解析。'],
  ['为什么质量报告还有“需复核”？', '需复核不是必然失败，通常表示候选表、空单元格、数字密度或视觉溯源需要人工确认。日本市场已按 EDINET 有报特征识别 Financial Highlights 和核心报表。'],
  ['解析完成后为什么没有研究报告？', '解析只生成 Markdown、表格、质量报告、财务数据和 package 材料；智能分析、事实核查、跟踪和法务报告需要后续 Agent 或导入流程生成。'],
  ['解析产物包有什么用？', '它把原文、表格、事实、指标、质量门禁和证据坐标放在同一目录，是 PostgreSQL 入库、向量入库和后续问答引用的稳定合同；历史接口中也称 Evidence Package。'],
  ['文档解析和财报解析怎么选？', '普通合同、研报、网页、Office 和图片走文档解析；上市公司财报、市场规则、财务抽取和勾稽校验走财报解析或市场解析页。'],
  ['JP/KR 下载需要配置什么？', '日本完整法定年报主链路依赖 EDINET_API_KEY；韩国 OpenDART ZIP 依赖 DART_API_KEY。未配置时仍可使用部分官方 PDF/IR 辅助链路。'],
  ['页面没更新怎么办？', '先看公网入口是否返回 Vite 页面，再检查本机 15173、18081、15000 是否健康；已打开的浏览器页面可刷新以重新加载最新模块。'],
  ['本地模型在哪里配置？', '进入设置页配置本地或云端供应商，填写 Base URL、模型名和 API Key，再点击测试调用。受限系统页面需要相应权限。'],
]

function GuideCard({ item }: { item: GuideEntry }) {
  return (
    <Surface
      as={Link}
      to={item.to}
      kind="card"
      padding="md"
      className="group flex h-full min-h-[156px] flex-col transition-transform hover:-translate-y-0.5 hover:border-primary/25"
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
  )
}

export default function Help() {
  return (
    <PageShell>
      <PageHeader
        icon={FileQuestion}
        eyebrow="Help Center"
        title="SIQ 操作指南"
        description="按当前真实功能整理页面入口、市场解析链路、解析产物、数据位置和常见排查方式。先从工作平台确认研究对象，再沿下载、解析、质量复核、入库、分析和追踪推进。"
        meta={['CN', 'HK', 'US', 'EU', 'JP', 'KR'].map((label) => (
          <StatusBadge key={label} tone="info">{label}</StatusBadge>
        ))}
      />

      <PageSection
        title="常用入口"
        description="日常研究最常走的页面。"
        actions={<StatusBadge tone="success">核心</StatusBadge>}
      >
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {workspaceEntries.map((item) => <GuideCard key={item.to} item={item} />)}
        </div>
      </PageSection>

      <PageSection
        title="市场解析入口"
        description="不同市场的主链路和兜底路径不同，优先进入对应市场页。"
        actions={<StatusBadge tone="info">披露市场</StatusBadge>}
      >
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {marketEntries.map((item) => <GuideCard key={item.to} item={item} />)}
        </div>
      </PageSection>

      <PageSection
        title="运营与系统入口"
        description="研究报告之外的交易、入库、配置和账户能力。"
        actions={<MonitorCog className="h-5 w-5 text-primary" />}
      >
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          {operationEntries.map((item) => <GuideCard key={item.to} item={item} />)}
        </div>
      </PageSection>

      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        <PageSection
          className="xl:col-span-2"
          title="推荐工作流"
          description="从原始披露到可审计研究结论。"
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
          description="页面没数据或公网没更新时先看这里。"
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
          description="定位数据来源、回归测试和导入问题时常用。"
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
                <p className="mt-2 text-sm leading-6 text-text-muted">{answer}</p>
              </Surface>
            ))}
          </div>
        </PageSection>
      </section>
    </PageShell>
  )
}
