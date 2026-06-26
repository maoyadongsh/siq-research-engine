import { lazy, Suspense } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import Layout from './components/layout/Layout'
import { LoginPage } from './pages/Login'
import { AuthProvider, ProtectedRoute } from './lib/auth'
import { RegisterPage } from './pages/Register'

const Dashboard = lazy(() => import('./pages/Dashboard'))
const MyWorkspace = lazy(() => import('./pages/MyWorkspace'))
const SearchDownload = lazy(() => import('./pages/SearchDownload'))
const PdfParsing = lazy(() => import('./pages/PdfParsing'))
const AnalysisReport = lazy(() => import('./pages/AnalysisReport'))
const FactVerification = lazy(() => import('./pages/FactVerification'))
const Tracking = lazy(() => import('./pages/Tracking'))
const LegalCompliance = lazy(() => import('./pages/LegalCompliance'))
const ChatPage = lazy(() => import('./pages/ChatPage'))
const Settings = lazy(() => import('./pages/Settings'))
const Account = lazy(() => import('./pages/Account'))
const UserAdmin = lazy(() => import('./pages/UserAdmin'))
const UserDetail = lazy(() => import('./pages/UserDetail'))
const Help = lazy(() => import('./pages/Help'))

function PageFallback() {
  return (
    <div className="flex min-h-[240px] items-center justify-center text-sm font-semibold text-text-muted">
      页面加载中...
    </div>
  )
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Suspense fallback={<PageFallback />}>
          <Routes>
            <Route path="/auto-login" element={<Navigate to="/login" replace />} />

            {/* 公开路由 - 手动登录页面 */}
            <Route path="/login" element={<LoginPage />} />

            {/* 公开路由 - 注册页面 */}
            <Route path="/register" element={<RegisterPage />} />

            {/* 受保护的路由 - 需要登录 */}
            <Route element={<ProtectedRoute><Layout /></ProtectedRoute>}>
              <Route path="/" element={<MyWorkspace />} />
              <Route path="/system-dashboard" element={
                <ProtectedRoute permission="user.manage">
                  <Dashboard />
                </ProtectedRoute>
              } />
              <Route path="/admin/users" element={
                <ProtectedRoute permission="user.manage">
                  <UserAdmin />
                </ProtectedRoute>
              } />
              <Route path="/admin/users/:userId" element={
                <ProtectedRoute permission="user.manage">
                  <UserDetail />
                </ProtectedRoute>
              } />
              <Route path="/search" element={<SearchDownload />} />
              <Route path="/parse" element={<PdfParsing />} />
              <Route path="/analysis" element={<AnalysisReport />} />
              <Route path="/verify" element={<FactVerification />} />
              <Route path="/tracking" element={<Tracking />} />
              <Route path="/legal" element={<LegalCompliance />} />
              <Route path="/chat" element={<ChatPage />} />
              <Route path="/account" element={<Account />} />
              <Route path="/help" element={<Help />} />

              {/* 系统设置 - 需要管理员权限 */}
              <Route path="/settings" element={
                <ProtectedRoute permission="system.config">
                  <Settings />
                </ProtectedRoute>
              } />
            </Route>

            <Route path="*" element={<Navigate to="/login" replace />} />
          </Routes>
        </Suspense>
      </BrowserRouter>
    </AuthProvider>
  )
}
