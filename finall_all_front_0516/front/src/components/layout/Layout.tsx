import { useEffect, useState } from 'react'
import { Outlet, useLocation } from 'react-router-dom'
import Sidebar from './Sidebar'
import Topbar from './Topbar'
import ChatBot from '../chat/ChatBot'

const AGENT_PAGE_PATHS = ['/analysis', '/verify', '/tracking', '/legal']

export default function Layout() {
  const [collapsed, setCollapsed] = useState(false)
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false)
  const { pathname } = useLocation()
  const hideGlobalChat = AGENT_PAGE_PATHS.includes(pathname)

  useEffect(() => { setMobileSidebarOpen(false) }, [pathname])

  return (
    <div className="min-h-screen bg-bg text-text">
      <Sidebar collapsed={collapsed} mobileOpen={mobileSidebarOpen} onToggle={() => setCollapsed(!collapsed)} onCloseMobile={() => setMobileSidebarOpen(false)} />
      <Topbar sidebarCollapsed={collapsed} onMenuClick={() => setMobileSidebarOpen(true)} />
      <main className={`pt-[72px] transition-all duration-300 ease-out ${collapsed ? 'lg:pl-20' : 'lg:pl-72'}`}>
        <div className={`mx-auto max-w-[1680px] py-5 pl-[76px] pr-4 sm:pl-[84px] sm:pr-6 lg:py-8 ${collapsed ? 'lg:px-6' : 'lg:px-8'}`}>
          <Outlet />
        </div>
      </main>
      {!hideGlobalChat && <ChatBot />}
    </div>
  )
}
