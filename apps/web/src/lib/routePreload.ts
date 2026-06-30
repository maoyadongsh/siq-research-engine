import { getRouteLoader } from '../app/routes'

const loadedRoutes = new Set<string>()

function normalizeRoutePath(path: string) {
  return path.split(/[?#]/, 1)[0] || '/'
}

export function preloadRoute(path: string) {
  const routeKey = normalizeRoutePath(path)
  if (loadedRoutes.has(routeKey)) return
  const load = getRouteLoader(routeKey)
  if (!load) return
  loadedRoutes.add(routeKey)
  load().catch(() => {
    loadedRoutes.delete(routeKey)
  })
}
