import { Database, Globe2, MonitorCog, Server, Sparkles } from 'lucide-react'
import type { ProviderFormData } from './types'

interface ConnectionSectionProps {
  apiBase: string
  setApiBase: (value: string) => void
  wikiRoot: string
  setWikiRoot: (value: string) => void
  recentLimit: string
  setRecentLimit: (value: string) => void
  loadingLlm: boolean
  activeProviderMeta: { label: string; provider: ProviderFormData }
}

export function ConnectionSection({
  apiBase,
  setApiBase,
  wikiRoot,
  setWikiRoot,
  recentLimit,
  setRecentLimit,
  loadingLlm,
  activeProviderMeta,
}: ConnectionSectionProps) {
  return (
    <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_360px]">
      <section className="apple-card rounded-[var(--radius-card)] p-4 sm:rounded-[var(--radius-panel)] sm:p-6">
        <div className="mb-5 flex items-center gap-3 sm:mb-6">
          <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-primary/10 text-primary sm:h-12 sm:w-12">
            <Server className="h-6 w-6" />
          </div>
          <div>
            <h2 className="text-base font-semibold text-text sm:text-lg">服务连接</h2>
            <p className="text-sm text-text-muted sm:text-base">
              用于前端调用后端、PDF 解析和 Wiki 产物。
            </p>
          </div>
        </div>
        <div className="grid gap-5 lg:grid-cols-2">
          <label className="space-y-2">
            <span className="flex items-center gap-2 text-base font-semibold text-text">
              <Globe2 className="h-5 w-5 text-primary" />
              后端 API Base
            </span>
            <input
              value={apiBase}
              onChange={(e) => setApiBase(e.target.value)}
              placeholder="留空表示使用当前域名 /api"
              className="form-control w-full px-4 text-base"
            />
            <p className="text-sm text-text-muted">
              例如 http://localhost:18081。留空会走 Vite 代理。
            </p>
          </label>
          <label className="space-y-2 lg:col-span-2">
            <span className="flex items-center gap-2 text-base font-semibold text-text">
              <Database className="h-5 w-5 text-primary" />
              Wiki 根目录提示
            </span>
            <input
              value={wikiRoot}
              onChange={(e) => setWikiRoot(e.target.value)}
              className="form-control w-full px-4 font-mono text-base"
            />
            <p className="text-sm text-text-muted">
              后端实际读取由 WIKI_ROOT 控制；这里用于前端展示和团队约定。
            </p>
          </label>
        </div>
      </section>

      <aside className="space-y-5">
        <section className="apple-card rounded-[var(--radius-card)] p-4 sm:rounded-[var(--radius-panel)] sm:p-6">
          <div className="mb-4 flex items-center gap-3 sm:mb-5">
            <div className="flex h-10 w-10 items-center justify-center rounded-2xl bg-primary/10 text-primary sm:h-11 sm:w-11">
              <MonitorCog className="h-6 w-6" />
            </div>
            <div>
              <h2 className="text-lg font-semibold text-text">工作台偏好</h2>
              <p className="text-sm text-text-muted">调整列表显示数量。</p>
            </div>
          </div>
          <div className="space-y-4">
            <label className="block space-y-2">
              <span className="text-base font-semibold text-text">工作平台近期任务数量</span>
              <input
                type="number"
                min="4"
                max="30"
                value={recentLimit}
                onChange={(e) => setRecentLimit(e.target.value)}
                className="form-control w-full px-4 text-base"
              />
            </label>
          </div>
        </section>

        <section className="inverted-section rounded-[var(--radius-panel)] p-6">
          <Sparkles className="relative z-10 mb-4 h-7 w-7 text-primary-light" />
          <h3 className="relative z-10 text-2xl font-semibold text-white">当前模型调用源</h3>
          <p className="relative z-10 mt-2 text-base leading-7 text-white/72">
            {loadingLlm
              ? '正在加载模型配置...'
              : `${activeProviderMeta.label}：${activeProviderMeta.provider.model || '未填写模型'}`}
          </p>
        </section>
      </aside>
    </div>
  )
}
