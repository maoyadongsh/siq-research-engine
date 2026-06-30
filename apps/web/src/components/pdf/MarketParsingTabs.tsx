import { Link } from 'react-router-dom'
import { DISCLOSURE_MARKET_ORDER, DISCLOSURE_MARKETS, type DisclosureMarketCode } from '../../lib/marketMetadata'
import { preloadRoute } from '../../lib/routePreload'

export function MarketParsingTabs({ active }: { active: DisclosureMarketCode }) {
  return (
    <nav className="grid grid-cols-2 gap-2 rounded-[20px] border border-border bg-card p-2 shadow-sm xl:grid-cols-6" aria-label="财报解析市场">
      {DISCLOSURE_MARKET_ORDER.map((marketCode) => {
        const market = DISCLOSURE_MARKETS[marketCode]
        const isActive = market.code === active
        return (
            <Link
            key={market.code}
            to={market.parseTo}
            aria-current={isActive ? 'page' : undefined}
            title={`${market.professionalName} · ${market.exchanges}`}
            onPointerEnter={() => preloadRoute(market.parseTo)}
            onFocus={() => preloadRoute(market.parseTo)}
            className={`flex min-h-[4.5rem] items-center justify-between gap-2 rounded-2xl px-3 py-2.5 font-semibold transition-colors sm:min-h-20 sm:gap-3 sm:px-4 sm:py-3 ${
              isActive
                ? 'bg-primary/10 text-primary'
                : 'text-text-muted hover:bg-bg hover:text-text'
            }`}
          >
            <span className="min-w-0">
              <span className="block text-[15px] font-extrabold">{market.label}</span>
              <span className="mt-0.5 block truncate font-mono text-[11px] font-bold leading-4 opacity-85">{market.exchanges}</span>
              <span className="mt-0.5 block text-xs font-semibold leading-5 opacity-85">{market.parsingDescription}</span>
            </span>
            <span className="flex shrink-0 flex-col items-end gap-1">
              <span className="rounded-full border border-current/20 px-2 py-0.5 font-mono text-xs font-bold">{market.code}</span>
            </span>
          </Link>
        )
      })}
    </nav>
  )
}
