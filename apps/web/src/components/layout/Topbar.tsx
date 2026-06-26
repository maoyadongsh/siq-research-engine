import { useNavigate } from 'react-router-dom'
import { Menu, LogOut } from 'lucide-react'
import { useAuth } from '../../hooks/useAuth'
import GlobalSearch from './GlobalSearch'
import NotificationMenu from './NotificationMenu'

interface TopbarProps {
  sidebarCollapsed: boolean
  onMenuClick: () => void
  onSidebarToggle: () => void
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

export default function Topbar({ sidebarCollapsed, onMenuClick, onSidebarToggle }: TopbarProps) {
  const navigate = useNavigate()
  const { user, logout } = useAuth()

  const handleLogout = () => {
    logout()
    navigate('/login', { replace: true })
  }
  const handleMenuClick = () => {
    if (window.matchMedia('(min-width: 1024px)').matches) {
      onSidebarToggle()
      return
    }
    onMenuClick()
  }

  return (
    <header
      className={`fixed left-0 right-0 top-0 z-30 flex items-center gap-3 border-b border-white/70 bg-white/72 px-3 shadow-[0_1px_0_rgba(255,255,255,0.78)_inset] backdrop-blur-2xl transition-[left] duration-300 sm:px-6 lg:gap-4 xl:pr-14 2xl:pr-16 ${
        sidebarCollapsed ? 'lg:left-20' : 'lg:left-64 xl:left-72'
      }`}
      style={{
        height: 'var(--app-topbar-height)',
        paddingLeft: 'max(0.75rem, env(safe-area-inset-left))',
        paddingRight: 'max(0.75rem, env(safe-area-inset-right))',
      }}
    >
      <button onClick={handleMenuClick} className="icon-button shrink-0" aria-label="切换侧边栏">
        <Menu className="h-5 w-5" />
      </button>
      <GlobalSearch />
      <div className="ml-auto flex shrink-0 items-center gap-2">
        <NotificationMenu />
        {user && (
          <button
            onClick={() => navigate('/account')}
            className="hidden h-10 items-center gap-2 rounded-lg px-2 text-sm font-semibold text-text-muted transition hover:text-text focus:outline-none focus:ring-4 focus:ring-primary/10 sm:inline-flex"
            title="我的账户"
          >
            <span className="hidden max-w-[140px] truncate xl:inline">{user.full_name || user.username}</span>
            <span className="hidden rounded-full bg-bg px-2 py-0.5 text-xs text-text-muted lg:inline">
              {roleLabel(user.role)}
            </span>
          </button>
        )}
        {user && (
          <button
            onClick={handleLogout}
            className="inline-flex h-10 items-center gap-2 rounded-lg px-2 text-sm font-semibold text-text-muted transition hover:text-text focus:outline-none focus:ring-4 focus:ring-primary/10"
            title="退出登录"
            aria-label="退出登录"
          >
            <LogOut className="h-4 w-4" />
            <span className="hidden whitespace-nowrap md:inline">退出登录</span>
          </button>
        )}
      </div>
    </header>
  )
}
