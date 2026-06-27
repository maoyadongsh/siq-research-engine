import { Link } from 'react-router-dom'

type MarketTab = 'CN' | 'HK' | 'US' | 'JP' | 'KR' | 'EU'

const tabs: Array<{ market: MarketTab; label: string; desc: string; to: string }> = [
  { market: 'CN', label: 'A股', desc: '标准解析', to: '/parse' },
  { market: 'HK', label: '港股', desc: '通用入库', to: '/parse-hk' },
  { market: 'US', label: '美股', desc: 'SEC 披露', to: '/parse-us' },
  { market: 'EU', label: '欧股', desc: 'IFRS/ESEF', to: '/parse-eu' },
  { market: 'JP', label: '日股', desc: 'EDINET 混合解析', to: '/parse-jp' },
  { market: 'KR', label: '韩股', desc: 'DART 混合解析', to: '/parse-kr' },
]

export function MarketParsingTabs({ active }: { active: MarketTab }) {
  return (
    <nav className="grid gap-2 rounded-[20px] border border-border bg-card p-2 shadow-sm sm:grid-cols-2 xl:grid-cols-6" aria-label="财报解析市场">
      {tabs.map((tab) => {
        const isActive = tab.market === active
        return (
          <Link
            key={tab.market}
            to={tab.to}
            aria-current={isActive ? 'page' : undefined}
            className={`flex min-h-16 items-center justify-between gap-3 rounded-2xl px-4 py-3 transition-colors ${
              isActive
                ? 'bg-primary/10 text-primary'
                : 'text-text-muted hover:bg-bg hover:text-text'
            }`}
          >
            <span className="min-w-0">
              <span className="block text-sm font-semibold">{tab.label}</span>
              <span className="mt-0.5 block text-xs leading-5 opacity-80">{tab.desc}</span>
            </span>
            <span className="shrink-0 rounded-full border border-current/20 px-2 py-0.5 font-mono text-xs">{tab.market}</span>
          </Link>
        )
      })}
    </nav>
  )
}
