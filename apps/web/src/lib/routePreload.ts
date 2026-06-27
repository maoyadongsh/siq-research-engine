type RouteLoader = () => Promise<unknown>

const routeLoaders: Record<string, RouteLoader> = {
  '/': () => import('../pages/MyWorkspace'),
  '/search': () => import('../pages/SearchDownload'),
  '/parse': () => import('../pages/PdfParsing'),
  '/documents': () => import('../pages/DocumentParsing'),
  '/parse-hk': () => import('../pages/HkParsing'),
  '/parse-us': () => import('../pages/UsParsing'),
  '/parse-eu': () => import('../pages/EuParsing'),
  '/parse-jp': () => import('../pages/JpParsing'),
  '/parse-kr': () => import('../pages/KrParsing'),
  '/analysis': () => import('../pages/AnalysisReport'),
  '/verify': () => import('../pages/FactVerification'),
  '/tracking': () => import('../pages/Tracking'),
  '/legal': () => import('../pages/LegalCompliance'),
  '/chat': () => import('../pages/ChatPage'),
  '/account': () => import('../pages/Account'),
  '/help': () => import('../pages/Help'),
  '/settings': () => import('../pages/Settings'),
  '/admin/users': () => import('../pages/UserAdmin'),
  '/vector-ingest': () => import('../pages/VectorIngest'),
  '/system-dashboard': () => import('../pages/Dashboard'),
}

const loadedRoutes = new Set<string>()

export function preloadRoute(path: string) {
  if (loadedRoutes.has(path)) return
  const load = routeLoaders[path]
  if (!load) return
  loadedRoutes.add(path)
  load().catch(() => {
    loadedRoutes.delete(path)
  })
}
