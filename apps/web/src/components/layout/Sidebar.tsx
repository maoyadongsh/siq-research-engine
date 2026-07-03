import { useMemo } from 'react'
import { NavLink } from 'react-router-dom'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { Tooltip } from '../ui'
import { useAuth } from '../../hooks/useAuth'
import { preloadRoute } from '../../lib/routePreload'
import { navItems, userAdminItems, systemAdminItems, assistantItem, bottomItems, type SidebarItem, type SidebarLinkVariant } from './layoutData'

interface SidebarProps {
  collapsed: boolean
  mobileOpen?: boolean
  onToggle: () => void
  onCloseMobile?: () => void
}

export default function Sidebar({ collapsed, mobileOpen = false, onToggle, onCloseMobile }: SidebarProps) {
  const { hasPermission } = useAuth()
  const canManageUsers = hasPermission('user.manage')
  const canConfigureSystem = hasPermission('system.config')
  const visibleNavItems = useMemo(
    () => [
      ...navItems,
      ...(canManageUsers ? userAdminItems : []),
      ...(canConfigureSystem ? systemAdminItems : []),
    ],
    [canManageUsers, canConfigureSystem],
  )
  const visibleBottomItems = useMemo(
    () => bottomItems.filter((item) => item.to !== '/settings' || canConfigureSystem),
    [canConfigureSystem],
  )
  const renderLink = (item: SidebarItem, compact: boolean, variant: SidebarLinkVariant = 'nav') => {
    const sizeClass = compact
      ? variant === 'nav'
        ? 'h-10 min-h-10 w-10 rounded-[11px] px-0 py-0 text-[0.94rem]'
        : 'h-10 min-h-10 w-10 rounded-[11px] px-0 py-0 text-sm'
      : variant === 'nav'
        ? 'min-h-11 rounded-[12px] px-3 py-2 text-[0.94rem]'
        : variant === 'assistant'
          ? 'min-h-11 rounded-[12px] px-2.5 py-2 text-sm'
          : 'min-h-11 rounded-[11px] px-2 py-1.5 text-xs'
    const iconClass = variant === 'nav' ? 'h-[18px] w-[18px]' : 'h-4 w-4'
    const link = (
      <NavLink
        key={item.to}
        to={item.to}
        onClick={onCloseMobile}
        className={({ isActive }) =>
          `group relative flex items-center gap-2.5 font-semibold transition-[background,color,box-shadow] duration-200 ${sizeClass} ${
            isActive
              ? `bg-primary/10 text-primary shadow-[0_8px_18px_rgba(0,113,227,0.08)] before:absolute before:left-0 before:rounded-full before:bg-primary ${compact ? 'before:top-2.5 before:bottom-2.5 before:w-0.5' : 'before:top-2 before:bottom-2 before:w-1'}`
              : 'text-slate-600 hover:bg-slate-100/80 hover:text-slate-950'
          } ${compact ? 'justify-center' : ''}`
        }
        onPointerEnter={() => preloadRoute(item.to)}
        onFocus={() => preloadRoute(item.to)}
      >
        <item.icon className={`${iconClass} shrink-0`} />
        {!compact && <span className="truncate whitespace-nowrap">{item.label}</span>}
      </NavLink>
    )
    return compact ? (
      <Tooltip key={item.to} content={item.label} className="flex justify-center" delay="medium">
        {link}
      </Tooltip>
    ) : (
      link
    )
  }

  const renderContent = (compact: boolean, showDesktopToggle = true) => (
    <>
        <div
          className={`flex items-center border-b border-border bg-white/70 ${compact ? 'justify-center px-0' : 'gap-3 px-5'}`}
          style={{ height: 'var(--app-topbar-height)' }}
        >
        {!compact && (
          <>
            <div className="relative flex h-11 w-11 shrink-0 items-center justify-center rounded-[14px] bg-gradient-to-br from-[#2ea8ff] via-[#0071e3] to-[#004fb8] text-[16px] font-black text-white tracking-tighter shadow-[0_10px_24px_rgba(29,78,216,0.32)] transition-[background,box-shadow] duration-200">
              <span className="relative z-10">SIQ</span>
              <div className="pointer-events-none absolute inset-0 rounded-2xl bg-gradient-to-br from-white/24 via-white/5 to-transparent" />
            </div>
            <span className="whitespace-nowrap text-[19px] font-bold leading-none text-primary">
              Research Engine
            </span>
          </>
        )}
        {compact && (
          <div className="relative flex h-11 w-11 shrink-0 items-center justify-center rounded-[14px] bg-gradient-to-br from-[#2ea8ff] via-[#0071e3] to-[#004fb8] text-[14px] font-black text-white tracking-tighter shadow-[0_10px_24px_rgba(29,78,216,0.24)]">
            <span className="relative z-10">SIQ</span>
            <div className="pointer-events-none absolute inset-0 rounded-2xl bg-gradient-to-br from-white/24 via-white/5 to-transparent" />
          </div>
        )}
      </div>
      <nav className={`sidebar-scrollbarless flex-1 overflow-y-auto overflow-x-hidden pb-1 ${compact ? 'mt-2 space-y-0.5 px-1.5' : 'mt-3 space-y-0.5 px-2.5'}`}>
        {visibleNavItems.map((item) => renderLink(item, compact))}
      </nav>
      <div className={`border-t border-border ${compact ? 'px-1.5 py-1.5' : 'px-2.5 py-2'}`}>{renderLink(assistantItem, compact, 'assistant')}</div>
      <div className={`border-t border-border ${compact ? 'px-1.5 py-1.5' : 'px-2.5 py-2'}`}>
        <div className={compact ? 'space-y-1' : 'grid grid-cols-3 gap-1.5'}>
          {visibleBottomItems.map((item) => renderLink(item, compact, 'utility'))}
        </div>
      </div>
      {showDesktopToggle && (
        <div className="border-t border-border px-2.5 py-2">
          <button
            onClick={onToggle}
            className={`inline-flex h-11 w-full items-center justify-center rounded-[12px] text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-950 focus:outline-none focus:ring-4 focus:ring-primary/10 ${compact ? 'px-0' : 'px-3'}`}
            aria-label={compact ? '展开侧边栏' : '收起侧边栏'}
            title={compact ? '展开侧边栏' : '收起侧边栏'}
          >
            {compact ? <ChevronRight className="h-4 w-4" /> : <ChevronLeft className="h-4 w-4" />}
          </button>
        </div>
      )}
    </>
  )

  return (
    <>
      {mobileOpen && (
        <button
          className="fixed inset-0 z-40 bg-slate-950/35 backdrop-blur-md lg:hidden"
          onClick={onCloseMobile}
          aria-label="关闭侧边栏"
        />
      )}
        <aside
        id="app-sidebar"
        className={`fixed bottom-0 left-0 top-0 z-50 flex w-72 flex-col overflow-hidden border-r border-border bg-white/96 text-slate-700 shadow-[10px_0_32px_rgba(15,23,42,0.06)] backdrop-blur-xl transition-all duration-300 ease-out ${
          collapsed ? 'lg:w-20' : 'lg:w-64 xl:w-72'
        } ${mobileOpen ? 'translate-x-0' : '-translate-x-full lg:translate-x-0'}`}
      >
        <div className="hidden h-full flex-col lg:flex">{renderContent(collapsed)}</div>
        <div className="flex h-full flex-col lg:hidden">{renderContent(false, false)}</div>
      </aside>
    </>
  )
}

export type { SidebarItem, SidebarLinkVariant }
