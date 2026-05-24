import { NavLink } from 'react-router-dom'
import { Search, FileText, BarChart3, ShieldCheck, TrendingUp, Scale, MessageCircle, Settings, HelpCircle, ChevronLeft, ChevronRight, LayoutDashboard } from 'lucide-react'
import { Tooltip } from '../ui'

const navItems = [
  { to: '/', icon: LayoutDashboard, label: '工作平台' },
  { to: '/search', icon: Search, label: '搜索下载' },
  { to: '/parse', icon: FileText, label: '财报解析' },
  { to: '/analysis', icon: BarChart3, label: '智能分析' },
  { to: '/verify', icon: ShieldCheck, label: '事实核查' },
  { to: '/tracking', icon: TrendingUp, label: '持续跟踪' },
  { to: '/legal', icon: Scale, label: '法务合规' },
]
const bottomItems = [
  { to: '/settings', icon: Settings, label: '设置' },
  { to: '/help', icon: HelpCircle, label: '帮助' },
]

interface SidebarProps { collapsed: boolean; mobileOpen?: boolean; onToggle: () => void; onCloseMobile?: () => void }

export default function Sidebar({ collapsed, mobileOpen = false, onToggle, onCloseMobile }: SidebarProps) {
  const renderLink = (item: { to: string; icon: typeof LayoutDashboard; label: string }, compact: boolean) => {
    const link = (
      <NavLink key={item.to} to={item.to} onClick={onCloseMobile} className={({ isActive }) =>
        `group flex min-h-12 items-center gap-3 rounded-[14px] px-3.5 py-3 text-base font-semibold transition-all duration-200 ${
          isActive ? 'bg-slate-950 text-white shadow-[0_10px_24px_rgba(15,23,42,0.12)]' : 'text-slate-600 hover:bg-slate-100/80 hover:text-slate-950'
        } ${compact ? 'justify-center px-0' : ''}`
      }>
        <item.icon className="h-5 w-5 shrink-0" />
        {!compact && <span>{item.label}</span>}
      </NavLink>
    )
    return compact ? <Tooltip key={item.to} content={item.label}>{link}</Tooltip> : link
  }

  return (
    <>
      {mobileOpen && <button className="fixed inset-0 z-40 bg-slate-950/35 backdrop-blur-md lg:hidden" onClick={onCloseMobile} aria-label="关闭侧边栏" />}
      <aside className={`fixed bottom-0 left-0 top-0 z-50 flex flex-col border-r border-white/70 bg-white/82 text-slate-700 shadow-[14px_0_42px_rgba(15,23,42,0.07)] backdrop-blur-2xl transition-all duration-300 ease-out ${collapsed ? 'lg:w-20' : 'lg:w-72'} w-72 ${mobileOpen ? 'translate-x-0' : '-translate-x-full lg:translate-x-0'}`}>
        <div className={`flex h-[72px] items-center border-b border-border ${collapsed ? 'justify-center px-0' : 'gap-3 px-5'}`}>
          <div className="relative flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-slate-950 text-[16px] font-bold text-white tracking-tighter shadow-[0_12px_26px_rgba(15,23,42,0.16)] transition-all duration-200">
            <span className="relative z-10">FS</span>
            <div className="pointer-events-none absolute inset-0 rounded-2xl bg-gradient-to-br from-white/18 to-transparent" />
          </div>
          {!collapsed && <span className="whitespace-nowrap text-[21px] font-bold tracking-tight text-slate-950" style={{ fontFamily: "'Inter', -apple-system, BlinkMacSystemFont, 'SF Pro Display', system-ui, sans-serif" }}>FinSight</span>}
        </div>
        <nav className="mt-4 flex-1 space-y-1 px-3">{navItems.map((item) => renderLink(item, collapsed))}</nav>
        {!collapsed && (
          <div className="mx-3 mb-4 rounded-2xl border border-slate-200/90 bg-white/82 px-4 py-4 text-left shadow-[0_10px_28px_rgba(15,23,42,0.045),0_1px_0_rgba(255,255,255,0.86)_inset]">
            <div className="text-[13px] font-bold leading-5 text-slate-900">Research OS</div>
            <div className="mt-1.5 text-[13px] font-medium leading-5 text-slate-700">
              基于全链路可审计的财报分析平台。
            </div>
          </div>
        )}
        <div className="space-y-1 border-t border-border px-3 py-4">{bottomItems.map((item) => renderLink(item, collapsed))}</div>
        <div className="border-t border-border px-3 py-4">
          {renderLink({ to: '/chat', icon: MessageCircle, label: '问答助手' }, collapsed)}
        </div>
        <button onClick={onToggle} className="hidden h-12 items-center justify-center border-t border-border text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-950 lg:flex" aria-label="折叠侧边栏">
          {collapsed ? <ChevronRight className="h-5 w-5" /> : <ChevronLeft className="h-5 w-5" />}
        </button>
      </aside>
    </>
  )
}
