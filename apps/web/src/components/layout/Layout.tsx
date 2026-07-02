import { useEffect, useRef, useState } from 'react'
import { Outlet, useLocation } from 'react-router-dom'
import Sidebar from './Sidebar'
import Topbar from './Topbar'
import ChatBot from '../chat/ChatBot'

const AGENT_PAGE_PATHS = ['/analysis', '/verify', '/tracking', '/legal']
const COMPACT_DESKTOP_QUERY = '(min-width: 1024px) and (max-width: 1439px), (min-width: 1024px) and (max-height: 820px)'

function shouldUseCompactDesktop() {
  if (typeof window === 'undefined') return false
  return window.matchMedia(COMPACT_DESKTOP_QUERY).matches
}

export default function Layout() {
  const [collapsed, setCollapsed] = useState(() => shouldUseCompactDesktop())
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false)
  const sidebarManuallyChanged = useRef(false)
  const { pathname } = useLocation()
  const hideGlobalChat = AGENT_PAGE_PATHS.some((path) => pathname === path || pathname.startsWith(`${path}/`))

  useEffect(() => {
    const timer = window.setTimeout(() => setMobileSidebarOpen(false), 0)
    return () => window.clearTimeout(timer)
  }, [pathname])
  useEffect(() => {
    const media = window.matchMedia(COMPACT_DESKTOP_QUERY)
    const syncSidebar = () => {
      if (!sidebarManuallyChanged.current) setCollapsed(media.matches)
    }

    syncSidebar()
    media.addEventListener('change', syncSidebar)
    return () => media.removeEventListener('change', syncSidebar)
  }, [])

  const toggleSidebar = () => {
    sidebarManuallyChanged.current = true
    setCollapsed((current) => !current)
  }

  const toggleMobileSidebar = () => {
    setMobileSidebarOpen((current) => !current)
  }

  return (
    <div className="min-h-screen bg-bg text-text">
      <a href="#main-content" className="skip-link">跳到主内容</a>
      <Sidebar
        collapsed={collapsed}
        mobileOpen={mobileSidebarOpen}
        onToggle={toggleSidebar}
        onCloseMobile={() => setMobileSidebarOpen(false)}
      />
      <Topbar
        sidebarCollapsed={collapsed}
        mobileSidebarOpen={mobileSidebarOpen}
        onToggleSidebar={toggleSidebar}
        onToggleMobileSidebar={toggleMobileSidebar}
      />
      <main
        id="main-content"
        tabIndex={-1}
        className={`transition-[padding-left] duration-300 ease-out ${collapsed ? 'lg:pl-20' : 'lg:pl-64 xl:pl-72'}`}
        style={{ paddingTop: 'var(--app-topbar-height)' }}
      >
        <div
          className={`mx-auto max-w-[1680px] px-3 sm:px-4 md:px-5 ${collapsed ? 'lg:px-5 xl:px-6' : 'lg:px-6 xl:px-8'}`}
          style={{ paddingTop: 'calc(var(--app-content-y) / 2)', paddingBottom: 'calc(var(--app-content-y) / 2)' }}
        >
          <div key={pathname} className="animate-in fade-in slide-in-from-bottom-2 duration-200">
            <Outlet />
          </div>
        </div>
      </main>
      {!hideGlobalChat && <ChatBot />}
    </div>
  )
}
