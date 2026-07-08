import { lazy, type ComponentType, type LazyExoticComponent } from 'react'
import {
  BarChart3,
  DatabaseZap,
  FileText,
  Files,
  HelpCircle,
  Landmark,
  LayoutDashboard,
  MessageCircle,
  MonitorCog,
  Scale,
  Search,
  Settings,
  ShieldCheck,
  TrendingUp,
  UserRound,
  UsersRound,
  type LucideIcon,
} from 'lucide-react'

type PageLoader = () => Promise<{ default: ComponentType }>

type SidebarGroup = 'nav' | 'assistant' | 'utility' | 'userAdmin' | 'systemAdmin'

export type SidebarItem = {
  to: string
  icon: LucideIcon
  label: string
  end?: boolean
  children?: SidebarChildItem[]
}

export type SidebarChildItem = {
  to: string
  label: string
  end?: boolean
}

type RouteSidebarItem = SidebarItem & { group: SidebarGroup }

export type AppRoute = {
  path: string
  component: LazyExoticComponent<ComponentType>
  load: PageLoader
  permission?: string
  sidebar?: RouteSidebarItem
}

function defineRoute(path: string, load: PageLoader, route: Omit<AppRoute, 'path' | 'component' | 'load'> = {}): AppRoute {
  return {
    path,
    load,
    component: lazy(load),
    ...route,
  }
}

export const appRoutes: AppRoute[] = [
  defineRoute('/', () => import('../pages/MyWorkspace'), {
    sidebar: { group: 'nav', to: '/', icon: LayoutDashboard, label: '工作平台' },
  }),
  defineRoute('/search', () => import('../pages/SearchDownload'), {
    sidebar: { group: 'nav', to: '/search', icon: Search, label: '搜索下载' },
  }),
  defineRoute('/parse', () => import('../pages/PdfParsing'), {
    sidebar: { group: 'nav', to: '/parse', icon: FileText, label: '财报解析' },
  }),
  defineRoute('/parse-hk', () => import('../pages/HkParsing')),
  defineRoute('/parse-us', () => import('../pages/UsParsing')),
  defineRoute('/parse-eu', () => import('../pages/EuParsing')),
  defineRoute('/parse-jp', () => import('../pages/JpParsing')),
  defineRoute('/parse-kr', () => import('../pages/KrParsing')),
  defineRoute('/documents', () => import('../pages/DocumentParsing'), {
    sidebar: { group: 'nav', to: '/documents', icon: Files, label: '文档解析' },
  }),
  defineRoute('/deals', () => import('../pages/Deals')),
  defineRoute('/primary-market', () => import('../pages/PrimaryMarketWorkbench'), {
    sidebar: {
      group: 'nav',
      to: '/primary-market',
      icon: Landmark,
      label: '一级市场',
      children: [
        { to: '/primary-market', label: '工作平台', end: true },
        { to: '/deals', label: '项目管理' },
        { to: '/primary-market/materials', label: '材料中心' },
        { to: '/primary-market/meeting', label: '投研决策' },
        { to: '/primary-market/post-investment', label: '投后管理' },
      ],
    },
  }),
  defineRoute('/primary-market/materials', () => import('../pages/PrimaryMarketMaterials')),
  defineRoute('/primary-market/meeting', () => import('../pages/PrimaryMarketMeeting')),
  defineRoute('/primary-market/post-investment', () => import('../pages/PrimaryMarketPostInvestment')),
  defineRoute('/deals/:dealId', () => import('../pages/DealWorkspace')),
  defineRoute('/deals/:dealId/data-room', () => import('../pages/DealDataRoom')),
  defineRoute('/deals/:dealId/evidence', () => import('../pages/DealEvidence')),
  defineRoute('/deals/:dealId/agents', () => import('../pages/DealAgents')),
  defineRoute('/deals/:dealId/workflow', () => import('../pages/DealWorkflow')),
  defineRoute('/deals/:dealId/reports', () => import('../pages/DealReports')),
  defineRoute('/deals/:dealId/decision', () => import('../pages/DealDecision')),
  defineRoute('/deals/:dealId/audit', () => import('../pages/DealAudit')),
  defineRoute('/analysis', () => import('../pages/AnalysisReport'), {
    sidebar: { group: 'nav', to: '/analysis', icon: BarChart3, label: '智能分析' },
  }),
  defineRoute('/verify', () => import('../pages/FactVerification'), {
    sidebar: { group: 'nav', to: '/verify', icon: ShieldCheck, label: '事实核查' },
  }),
  defineRoute('/tracking', () => import('../pages/Tracking'), {
    sidebar: { group: 'nav', to: '/tracking', icon: TrendingUp, label: '持续跟踪' },
  }),
  defineRoute('/legal', () => import('../pages/LegalCompliance'), {
    sidebar: { group: 'nav', to: '/legal', icon: Scale, label: '法务合规' },
  }),
  defineRoute('/chat', () => import('../pages/ChatPage'), {
    sidebar: { group: 'assistant', to: '/chat', icon: MessageCircle, label: '问答助手' },
  }),
  defineRoute('/account', () => import('../pages/Account'), {
    sidebar: { group: 'utility', to: '/account', icon: UserRound, label: '账户' },
  }),
  defineRoute('/settings', () => import('../pages/Settings'), {
    permission: 'system.config',
    sidebar: { group: 'utility', to: '/settings', icon: Settings, label: '设置' },
  }),
  defineRoute('/help', () => import('../pages/Help'), {
    sidebar: { group: 'utility', to: '/help', icon: HelpCircle, label: '帮助' },
  }),
  defineRoute('/admin/users', () => import('../pages/UserAdmin'), {
    permission: 'user.manage',
    sidebar: { group: 'userAdmin', to: '/admin/users', icon: UsersRound, label: '用户审批' },
  }),
  defineRoute('/admin/users/:userId', () => import('../pages/UserDetail'), {
    permission: 'user.manage',
  }),
  defineRoute('/vector-ingest', () => import('../pages/VectorIngest'), {
    permission: 'system.config',
    sidebar: { group: 'systemAdmin', to: '/vector-ingest', icon: DatabaseZap, label: '向量入库' },
  }),
  defineRoute('/system-dashboard', () => import('../pages/Dashboard'), {
    permission: 'user.manage',
    sidebar: { group: 'systemAdmin', to: '/system-dashboard', icon: MonitorCog, label: '系统平台' },
  }),
  defineRoute('/forbidden', () => import('../pages/Forbidden')),
]

const routeLoaders = new Map(appRoutes.map((route) => [route.path, route.load]))

function splitPath(path: string) {
  return path.split('/').filter(Boolean)
}

function normalizeRoutePath(path: string) {
  return path.split(/[?#]/, 1)[0] || '/'
}

function matchesRoutePattern(pattern: string, path: string) {
  const patternParts = splitPath(pattern)
  const pathParts = splitPath(path)

  if (patternParts.length !== pathParts.length) return false

  return patternParts.every((part, index) => part.startsWith(':') || part === pathParts[index])
}

export function getRouteLoader(path: string) {
  const normalizedPath = normalizeRoutePath(path)
  const exact = routeLoaders.get(normalizedPath)
  if (exact) return exact

  return appRoutes.find((route) => route.path.includes('/:') && matchesRoutePattern(route.path, normalizedPath))?.load
}

function getSidebarItems(group: SidebarGroup): SidebarItem[] {
  return appRoutes
    .filter((route): route is AppRoute & { sidebar: RouteSidebarItem } => route.sidebar?.group === group)
    .map((route) => ({
      to: route.sidebar.to,
      icon: route.sidebar.icon,
      label: route.sidebar.label,
      end: route.sidebar.end,
      children: route.sidebar.children,
    }))
}

function requireSidebarItem(group: SidebarGroup): SidebarItem {
  const item = getSidebarItems(group)[0]
  if (!item) {
    throw new Error(`Missing sidebar item for group: ${group}`)
  }
  return item
}

export const navItems = getSidebarItems('nav')
export const bottomItems = getSidebarItems('utility')
export const userAdminItems = getSidebarItems('userAdmin')
export const systemAdminItems = getSidebarItems('systemAdmin')
export const assistantItem = requireSidebarItem('assistant')
