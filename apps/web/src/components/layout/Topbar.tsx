import { useNavigate } from 'react-router-dom'
import { Menu, Settings } from 'lucide-react'
import { useAuth } from '../../hooks/useAuth'
import GlobalSearch from './GlobalSearch'
import NotificationMenu from './NotificationMenu'

interface TopbarProps {
  sidebarCollapsed: boolean
  mobileSidebarOpen: boolean
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

export default function Topbar({ sidebarCollapsed, mobileSidebarOpen, onToggleMobileSidebar }: TopbarProps) {
  const navigate = useNavigate()
  const { user } = useAuth()
  const accountLabel = user
    ? `${user.full_name || user.username || '我的账户'}（${roleLabel(user.role)}）`
    : '我的账户'

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
        onClick={onToggleMobileSidebar}
        className="icon-button shrink-0 lg:hidden"
        aria-label={mobileSidebarOpen ? '关闭导航' : '展开导航'}
        aria-controls="app-sidebar"
        aria-expanded={mobileSidebarOpen}
        title={mobileSidebarOpen ? '关闭导航' : '展开导航'}
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
          <button
            onClick={() => navigate('/account')}
            className="inline-flex h-10 w-10 items-center justify-center rounded-full border border-transparent text-sm font-semibold text-text-muted transition hover:border-border hover:bg-white hover:text-text focus:outline-none focus:ring-4 focus:ring-primary/10"
            title={accountLabel}
            aria-label={accountLabel}
          >
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-bold text-primary">
              {(user.full_name || user.username || 'U').charAt(0).toUpperCase()}
            </span>
          </button>
        )}
      </div>
    </header>
  )
}
