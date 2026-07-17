import { useMemo, useState } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
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
  const location = useLocation()
  const [expandedGroups, setExpandedGroups] = useState<Record<string, boolean>>({ '/': true, '/documents': true, '/primary-market': true })
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
    () => bottomItems.filter((item) => item.to === '/help' || (item.to === '/settings' && canConfigureSystem)),
    [canConfigureSystem],
  )
  const isChildActive = (child: NonNullable<SidebarItem['children']>[number]) => (
    child.end ? location.pathname === child.to : location.pathname === child.to || location.pathname.startsWith(`${child.to}/`)
  )
  const isGroupActive = (item: SidebarItem) => (
    location.pathname === item.to || location.pathname.startsWith(`${item.to}/`) || Boolean(item.children?.some(isChildActive))
  )
  const renderLink = (item: SidebarItem, compact: boolean, variant: SidebarLinkVariant = 'nav') => {
    const sizeClass = compact
      ? variant === 'nav'
        ? 'h-11 min-h-11 w-11 rounded-[10px] px-0 py-0 text-[0.95rem]'
        : 'h-11 min-h-11 w-11 rounded-[10px] px-0 py-0 text-[0.95rem]'
      : variant === 'nav'
        ? 'min-h-12 rounded-[10px] px-3.5 py-2.5 text-[16px] leading-5'
        : variant === 'assistant'
          ? 'min-h-11 rounded-[10px] px-3.5 py-2 text-[15px] leading-5'
          : 'min-h-11 w-full rounded-[10px] px-3.5 py-2 text-[15px] leading-5'
    const iconClass = variant === 'nav' ? 'h-[19px] w-[19px]' : 'h-[18px] w-[18px]'
    const link = (
      <NavLink
        key={item.to}
        to={item.to}
        end={item.end}
        onClick={onCloseMobile}
        className={({ isActive }) =>
          `group relative flex cursor-pointer items-center gap-3 transition-[background,color,box-shadow] duration-200 ease-out ${sizeClass} ${
            isActive
              ? `bg-primary/10 text-[#005bb5] shadow-[inset_0_0_0_1px_rgba(0,113,227,0.10)] before:absolute before:left-0 before:rounded-full before:bg-[#0071e3] ${compact ? 'before:bottom-2.5 before:top-2.5 before:w-0.5' : 'before:bottom-2 before:top-2 before:w-1'}`
              : 'text-slate-700 hover:bg-slate-100/80 hover:text-slate-950'
          } ${compact ? 'justify-center' : ''}`
        }
        onPointerEnter={() => preloadRoute(item.to)}
        onFocus={() => preloadRoute(item.to)}
      >
        <item.icon className={`${iconClass} shrink-0`} />
        {!compact && <span className={`truncate whitespace-nowrap ${variant === 'nav' ? 'font-semibold' : 'font-medium'}`}>{item.label}</span>}
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

  const renderChildLink = (item: NonNullable<SidebarItem['children']>[number]) => (
    <NavLink
      key={item.to}
      to={item.to}
      end={item.end}
      onClick={onCloseMobile}
      className={({ isActive }) =>
        `flex min-h-11 cursor-pointer items-center rounded-[10px] px-3.5 py-2 text-[15px] font-medium leading-5 transition-[background,color,box-shadow] duration-200 ease-out ${
          isActive
            ? 'bg-primary/10 text-[#005bb5] shadow-[inset_0_0_0_1px_rgba(0,113,227,0.12)]'
            : 'text-slate-600 hover:bg-slate-100/80 hover:text-slate-950'
        }`
      }
      onPointerEnter={() => preloadRoute(item.to)}
      onFocus={() => preloadRoute(item.to)}
    >
      <span className="block truncate whitespace-nowrap">{item.label}</span>
    </NavLink>
  )

  const renderNavItem = (item: SidebarItem, compact: boolean) => {
    if (compact || !item.children?.length) return renderLink(item, compact)
    const visibleChildren = item.children.filter((child) => !child.permission || hasPermission(child.permission))
    const expanded = expandedGroups[item.to] ?? false
    const active = isGroupActive(item)
    return (
      <div key={item.to} className="space-y-1.5">
        <button
          type="button"
          onClick={() => setExpandedGroups((current) => ({ ...current, [item.to]: !expanded }))}
          className={`group relative flex min-h-12 w-full cursor-pointer items-center gap-3 rounded-[10px] px-3.5 py-2.5 text-left text-[16px] font-semibold leading-5 transition-[background,color] duration-200 ease-out ${
            active ? 'text-[#005bb5] before:absolute before:bottom-2 before:left-0 before:top-2 before:w-1 before:rounded-full before:bg-[#0071e3] hover:bg-primary/5' : 'text-slate-700 hover:bg-slate-100/80 hover:text-slate-950'
          }`}
          aria-expanded={expanded}
          aria-controls={`sidebar-group-${item.to.replace(/[^a-zA-Z0-9_-]/g, '-')}`}
          onPointerEnter={() => preloadRoute(item.to)}
          onFocus={() => preloadRoute(item.to)}
        >
          <item.icon className="h-[19px] w-[19px] shrink-0" />
          <span className="min-w-0 flex-1 truncate whitespace-nowrap font-semibold">{item.label}</span>
          <ChevronRight className={`h-[17px] w-[17px] shrink-0 text-slate-500 transition-transform duration-200 ease-out group-hover:text-current ${expanded ? 'rotate-90' : ''}`} />
        </button>
        {expanded ? (
          <div id={`sidebar-group-${item.to.replace(/[^a-zA-Z0-9_-]/g, '-')}`} className="ml-6 space-y-1 border-l border-slate-200/90 pl-3.5">
            {visibleChildren.map(renderChildLink)}
          </div>
        ) : null}
      </div>
    )
  }

  const renderBrandMark = () => (
    <div
      className="flex h-11 w-11 shrink-0 items-center justify-center"
      role="img"
      aria-label="SIQ"
    >
      <span
        className="grid h-10 w-10 place-items-center rounded-full border border-primary/35 bg-primary/5 font-sans text-[15px] font-black leading-none text-[#0057d9]"
      >
        SIQ
      </span>
    </div>
  )

  const renderContent = (compact: boolean, showDesktopToggle = true) => (
    <>
        <div
          className={`flex items-center border-b border-border bg-white/70 ${compact ? 'justify-center px-0' : 'gap-3 px-5'}`}
          style={{ height: 'var(--app-topbar-height)' }}
        >
        {!compact && (
          <>
            {renderBrandMark()}
            <span className="whitespace-nowrap font-sans text-[17px] font-semibold leading-none text-slate-900">
              Research Engine
            </span>
          </>
        )}
        {compact && (
          renderBrandMark()
        )}
      </div>
      <nav className={`sidebar-scrollbarless flex-1 overflow-y-auto overflow-x-hidden pb-2 font-sans ${compact ? 'mt-2 space-y-1 px-2.5' : 'mt-3 space-y-1.5 px-2.5'}`}>
        {visibleNavItems.map((item) => renderNavItem(item, compact))}
      </nav>
      <div className="border-t border-border px-2.5 py-1.5 font-sans">{renderLink(assistantItem, compact, 'assistant')}</div>
      <div className="border-t border-border px-2.5 py-1.5 font-sans">
        <div className={compact ? 'space-y-1' : 'grid grid-cols-2 gap-2'}>
          {visibleBottomItems.map((item) => renderLink(item, compact, 'utility'))}
        </div>
      </div>
      {showDesktopToggle && (
        <div className="border-t border-border px-2.5 py-0.5">
          <button
            onClick={onToggle}
            className={`inline-flex h-11 w-full cursor-pointer items-center justify-center rounded-[10px] text-slate-500 transition-colors duration-200 hover:bg-slate-100/80 hover:text-[#005bb5] focus:outline-none focus:ring-2 focus:ring-[#0071e3]/30 ${compact ? 'px-0' : 'px-3'}`}
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
        className={`fixed bottom-0 left-0 top-0 z-50 flex w-72 flex-col overflow-hidden border-r border-border bg-white text-slate-700 transition-all duration-300 ease-out ${
          collapsed ? 'lg:w-16' : 'lg:w-64'
        } ${mobileOpen ? 'translate-x-0' : '-translate-x-full lg:translate-x-0'}`}
      >
        <div className="hidden h-full flex-col lg:flex">{renderContent(collapsed)}</div>
        <div className="flex h-full flex-col lg:hidden">{renderContent(false, false)}</div>
      </aside>
    </>
  )
}

export type { SidebarItem, SidebarLinkVariant }
