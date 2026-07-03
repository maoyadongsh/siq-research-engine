import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { LogOut, Menu, Settings, UserRound } from 'lucide-react'
import { useAuth } from '../../hooks/useAuth'
import GlobalSearch from './GlobalSearch'
import NotificationMenu from './NotificationMenu'

const DESKTOP_NAV_QUERY = '(min-width: 1024px)'

interface TopbarProps {
  sidebarCollapsed: boolean
  mobileSidebarOpen: boolean
  onToggleSidebar: () => void
  onToggleMobileSidebar: () => void
}

function roleLabel(role?: string) {
  return (
    ({
      super_admin: '超级管理员',
      admin: '管理员',
      analyst: '分析师',
      reviewer: '复核员',
      viewer: '普通用户',
    } as Record<string, string>)[role || ''] || role || '用户'
  )
}

export default function Topbar({ sidebarCollapsed, mobileSidebarOpen, onToggleSidebar, onToggleMobileSidebar }: TopbarProps) {
  const navigate = useNavigate()
  const { user, logout } = useAuth()
  const [accountMenuOpen, setAccountMenuOpen] = useState(false)
  const [desktopNav, setDesktopNav] = useState(() => (
    typeof window !== 'undefined' ? window.matchMedia(DESKTOP_NAV_QUERY).matches : true
  ))
  const accountMenuRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!accountMenuOpen) return

    function handlePointerDown(event: PointerEvent) {
      if (accountMenuRef.current?.contains(event.target as Node)) return
      setAccountMenuOpen(false)
    }

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') setAccountMenuOpen(false)
    }

    document.addEventListener('pointerdown', handlePointerDown)
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('pointerdown', handlePointerDown)
      document.removeEventListener('keydown', handleKeyDown)
    }
  }, [accountMenuOpen])

  useEffect(() => {
    const media = window.matchMedia(DESKTOP_NAV_QUERY)
    const syncDesktopNav = () => setDesktopNav(media.matches)

    syncDesktopNav()
    media.addEventListener('change', syncDesktopNav)
    return () => media.removeEventListener('change', syncDesktopNav)
  }, [])

  const accountLabel = user
    ? `${user.full_name || user.username || '我的账户'}（${roleLabel(user.role)}）`
    : '我的账户'
  const navigationExpanded = desktopNav ? !sidebarCollapsed : mobileSidebarOpen
  const navigationLabel = desktopNav
    ? sidebarCollapsed ? '展开侧边栏' : '收起侧边栏'
    : mobileSidebarOpen ? '关闭导航' : '打开导航'
  const accountName = user?.full_name || user?.username || '我的账户'
  const handleToggleNavigation = () => {
    if (desktopNav) {
      onToggleSidebar()
      return
    }
    onToggleMobileSidebar()
  }
  const handleLogout = () => {
    setAccountMenuOpen(false)
    logout()
    navigate('/login')
  }
  const handleOpenAccount = () => {
    setAccountMenuOpen(false)
    navigate('/account')
  }

  return (
      <header
      className={`fixed left-0 right-0 top-0 z-30 flex items-center gap-3 border-b border-border bg-white/88 px-3 shadow-[0_1px_0_rgba(255,255,255,0.78)_inset] backdrop-blur-xl transition-[left] duration-300 sm:px-6 lg:gap-4 xl:pr-14 2xl:pr-16 ${
        sidebarCollapsed ? 'lg:left-20' : 'lg:left-64 xl:left-72'
      }`}
      style={{
        height: 'var(--app-topbar-height)',
        paddingLeft: 'max(0.75rem, env(safe-area-inset-left))',
        paddingRight: 'max(0.75rem, env(safe-area-inset-right))',
      }}
    >
      <button
        type="button"
        onClick={handleToggleNavigation}
        className="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-full border border-transparent text-slate-500 transition hover:border-border hover:bg-white hover:text-slate-950 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/20"
        aria-label={navigationLabel}
        aria-controls="app-sidebar"
        aria-expanded={navigationExpanded}
        title={navigationLabel}
      >
        <Menu className="h-5 w-5" />
      </button>
      <div className="min-w-0 flex-1 px-1 md:px-3 lg:max-w-[min(48vw,720px)]">
        <GlobalSearch />
      </div>
      <div className="ml-auto flex shrink-0 items-center gap-1.5 sm:gap-2">
        <NotificationMenu />
        {user && (
          <button
            onClick={() => navigate('/settings')}
            className="inline-flex h-10 w-10 items-center justify-center rounded-full border border-transparent text-sm font-semibold text-text-muted transition hover:border-border hover:bg-white hover:text-text focus:outline-none focus:ring-4 focus:ring-primary/10"
            title="设置"
            aria-label="设置"
          >
            <Settings className="h-4 w-4" />
          </button>
        )}
        {user && (
          <div ref={accountMenuRef} className="relative">
            <button
              type="button"
              onClick={() => setAccountMenuOpen((open) => !open)}
              className="inline-flex h-10 w-10 items-center justify-center rounded-full border border-transparent text-sm font-semibold text-text-muted transition hover:border-border hover:bg-white hover:text-text focus:outline-none focus:ring-4 focus:ring-primary/10"
              title={accountLabel}
              aria-label={accountLabel}
              aria-haspopup="menu"
              aria-expanded={accountMenuOpen}
            >
              <UserRound className="h-[18px] w-[18px]" />
            </button>
            {accountMenuOpen ? (
              <div
                role="menu"
                className="absolute right-0 top-full z-50 mt-2 w-56 overflow-hidden rounded-2xl border border-border bg-white p-2 text-sm shadow-[0_18px_45px_rgba(15,23,42,0.14)]"
              >
                <button
                  type="button"
                  role="menuitem"
                  onClick={handleOpenAccount}
                  className="flex w-full items-center gap-3 border-b border-border/70 px-3 py-3 text-left transition hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-primary/15"
                >
                  <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
                    <UserRound className="h-5 w-5" />
                  </span>
                  <div className="min-w-0">
                    <div className="truncate font-semibold text-text">{accountName}</div>
                    <div className="truncate text-xs text-text-muted">{roleLabel(user.role)}</div>
                  </div>
                </button>
                <button
                  type="button"
                  role="menuitem"
                  onClick={handleLogout}
                  className="mt-2 flex w-full items-center gap-2 rounded-xl px-3 py-2.5 text-left font-medium text-text-muted transition hover:bg-slate-50 hover:text-text focus:outline-none focus:ring-2 focus:ring-primary/15"
                >
                  <LogOut className="h-4 w-4" />
                  退出登录
                </button>
              </div>
            ) : null}
          </div>
        )}
      </div>
    </header>
  )
}
