import { Suspense } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import Layout from './components/layout/Layout'
import { LoginPage } from './pages/Login'
import { AuthProvider, ProtectedRoute } from './lib/auth'
import { RegisterPage } from './pages/Register'
import { appRoutes } from './app/routes'

function PageFallback() {
  return (
    <div className="flex min-h-[240px] items-center justify-center text-sm font-semibold text-text-muted">
      页面加载中...
    </div>
  )
}

function renderRouteElement(route: (typeof appRoutes)[number]) {
  const Component = route.component
  const element = <Component />
  return route.permission ? <ProtectedRoute permission={route.permission}>{element}</ProtectedRoute> : element
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
              {appRoutes.map((route) => (
                <Route key={route.path} path={route.path} element={renderRouteElement(route)} />
              ))}
            </Route>

            <Route path="*" element={<Navigate to="/login" replace />} />
          </Routes>
        </Suspense>
      </BrowserRouter>
    </AuthProvider>
  )
}
