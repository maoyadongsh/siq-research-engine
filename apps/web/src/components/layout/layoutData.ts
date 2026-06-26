import {
  LayoutDashboard,
  Search,
  FileText,
  BarChart3,
  ShieldCheck,
  TrendingUp,
  Scale,
  MessageCircle,
  Settings,
  HelpCircle,
  UserRound,
  UsersRound,
  MonitorCog,
} from 'lucide-react'

export type SidebarItem = { to: string; icon: typeof LayoutDashboard; label: string }
export type SidebarLinkVariant = 'nav' | 'assistant' | 'utility'

export const navItems: SidebarItem[] = [
  { to: '/', icon: LayoutDashboard, label: '工作平台' },
  { to: '/search', icon: Search, label: '搜索下载' },
  { to: '/parse', icon: FileText, label: '财报解析' },
  { to: '/analysis', icon: BarChart3, label: '智能分析' },
  { to: '/verify', icon: ShieldCheck, label: '事实核查' },
  { to: '/tracking', icon: TrendingUp, label: '持续跟踪' },
  { to: '/legal', icon: Scale, label: '法务合规' },
]

export const bottomItems: SidebarItem[] = [
  { to: '/account', icon: UserRound, label: '账户' },
  { to: '/settings', icon: Settings, label: '设置' },
  { to: '/help', icon: HelpCircle, label: '帮助' },
]

export const adminItems: SidebarItem[] = [
  { to: '/admin/users', icon: UsersRound, label: '用户审批' },
  { to: '/system-dashboard', icon: MonitorCog, label: '系统平台' },
]

export const assistantItem: SidebarItem = { to: '/chat', icon: MessageCircle, label: '问答助手' }
