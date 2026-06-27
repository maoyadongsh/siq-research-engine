import { useMemo } from 'react'
import { NavLink } from 'react-router-dom'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { Tooltip } from '../ui'
import { useAuth } from '../../hooks/useAuth'
import { preloadRoute } from '../../lib/routePreload'
import { navItems, bottomItems, userAdminItems, systemAdminItems, assistantItem, type SidebarItem, type SidebarLinkVariant } from './layoutData'

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
    const sizeClass =
      variant === 'nav'
        ? 'min-h-10 rounded-[12px] px-3 py-2 text-[0.94rem]'
        : variant === 'assistant'
          ? 'min-h-10 rounded-[12px] px-2.5 py-2 text-sm'
          : 'min-h-9 rounded-[11px] px-2 py-1.5 text-xs'
    const iconClass = variant === 'nav' ? 'h-[18px] w-[18px]' : 'h-4 w-4'
    const link = (
      <NavLink
        key={item.to}
        to={item.to}
        onClick={onCloseMobile}
        className={({ isActive }) =>
          `group relative flex items-center gap-2.5 font-semibold transition-[background,color,box-shadow] duration-200 ${sizeClass} ${
            isActive
              ? 'bg-primary/10 text-primary shadow-[0_8px_18px_rgba(0,113,227,0.08)] before:absolute before:left-0 before:top-2 before:bottom-2 before:w-1 before:rounded-full before:bg-primary'
              : 'text-slate-600 hover:bg-slate-100/80 hover:text-slate-950'
          } ${compact ? 'w-full justify-center px-0' : ''}`
        }
        onPointerEnter={() => preloadRoute(item.to)}
        onFocus={() => preloadRoute(item.to)}
      >
        <item.icon className={`${iconClass} shrink-0`} />
        {!compact && <span className="truncate whitespace-nowrap">{item.label}</span>}
      </NavLink>
    )
    return compact ? (
      <Tooltip key={item.to} content={item.label} className="w-full">
        {link}
      </Tooltip>
    ) : (
      link
    )
  }

  const renderContent = (compact: boolean) => (
    <>
      <div
        className={`flex items-center border-b border-border bg-white/70 ${compact ? 'justify-center px-0' : 'gap-3 px-5'}`}
        style={{ height: 'var(--app-topbar-height)' }}
      >
        <div className="relative flex h-11 w-11 shrink-0 items-center justify-center rounded-[14px] bg-blue-700 text-[16px] font-black text-white tracking-tighter shadow-[0_10px_24px_rgba(29,78,216,0.32)] transition-[background,box-shadow] duration-200">
          <span className="relative z-10">SIQ</span>
          <div className="pointer-events-none absolute inset-0 rounded-2xl bg-gradient-to-br from-white/24 via-white/5 to-transparent" />
        </div>
        {!compact && (
          <span className="whitespace-nowrap text-[19px] font-bold leading-none text-primary">
            Research Engine
          </span>
        )}
      </div>
      <nav className="sidebar-scrollbarless mt-3 flex-1 space-y-0.5 overflow-y-auto overflow-x-hidden px-2.5 pb-1">
        {visibleNavItems.map((item) => renderLink(item, compact))}
      </nav>
      {!compact && (
        <div className="mx-2.5 mb-2.5 rounded-[14px] border border-slate-200/90 bg-slate-50 px-2.5 py-2.5 text-left">
          <div className="text-xs font-bold leading-4 text-slate-900">Research OS</div>
          <div className="mt-1 whitespace-nowrap text-xs font-medium leading-4 text-slate-700">基于全链路可审计的公司、行业研究平台。</div>
        </div>
      )}
      <div className="border-t border-border px-2.5 py-2">{renderLink(assistantItem, compact, 'assistant')}</div>
      <div className={`border-t border-border px-2.5 py-2 ${compact ? 'flex flex-col gap-1' : 'grid grid-cols-3 gap-1.5'}`}>
        {visibleBottomItems.map((item) => renderLink(item, compact, 'utility'))}
      </div>
      <button
        onClick={onToggle}
        className="hidden h-12 items-center justify-center border-t border-border text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-950 lg:flex"
        aria-label="折叠侧边栏"
      >
        {compact ? <ChevronRight className="h-5 w-5" /> : <ChevronLeft className="h-5 w-5" />}
      </button>
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
        className={`fixed bottom-0 left-0 top-0 z-50 flex w-72 flex-col overflow-hidden border-r border-border bg-white/96 text-slate-700 shadow-[10px_0_32px_rgba(15,23,42,0.06)] backdrop-blur-xl transition-all duration-300 ease-out ${
          collapsed ? 'lg:w-20' : 'lg:w-64 xl:w-72'
        } ${mobileOpen ? 'translate-x-0' : '-translate-x-full lg:translate-x-0'}`}
      >
        <div className="hidden h-full flex-col lg:flex">{renderContent(collapsed)}</div>
        <div className="flex h-full flex-col lg:hidden">{renderContent(false)}</div>
      </aside>
    </>
  )
}

export type { SidebarItem, SidebarLinkVariant }
