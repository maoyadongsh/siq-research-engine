export type SearchDownloadSearchParamKey =
  | 'market'
  | 'q'
  | 'year'
  | 'exchange'
  | 'form'
  | 'country'
  | 'downloaded'
  | 'ask'

export interface SearchDownloadSearchParamsUpdate {
  market?: 'CN' | 'HK' | 'US' | 'EU' | 'KR' | 'JP'
  q?: string
  year?: string
  exchange?: string
  form?: string
  country?: string
  downloaded?: string
  ask?: string
}

export type SearchDownloadSearchParamsPatch = Partial<Record<SearchDownloadSearchParamKey, string | null>>

export interface SearchDownloadSearchParamsApplyResult {
  searchParams: URLSearchParams
  replace: boolean
}

type SearchDownloadMarket = NonNullable<SearchDownloadSearchParamsUpdate['market']>

const SEARCH_DOWNLOAD_MARKET_FILTER_KEYS: Partial<Record<SearchDownloadMarket, SearchDownloadSearchParamKey>> = {
  CN: 'exchange',
  US: 'form',
  EU: 'country',
}

function normalizeSearchDownloadSearchParamValue(value: string | null | undefined) {
  return String(value ?? '').trim()
}

export function getSearchDownloadMarketFilterKey(market: SearchDownloadMarket) {
  return SEARCH_DOWNLOAD_MARKET_FILTER_KEYS[market] ?? 'exchange'
}

export function buildSearchDownloadMarketFilterPatch(
  market: SearchDownloadMarket,
  value: string,
): SearchDownloadSearchParamsUpdate {
  const key = getSearchDownloadMarketFilterKey(market)
  if (key === 'form') return { form: value, exchange: '', country: '' }
  if (key === 'country') return { country: value, exchange: '', form: '' }
  return { exchange: value, form: '', country: '' }
}

export function buildSearchDownloadSearchParamsPatch(
  next: SearchDownloadSearchParamsUpdate,
  currentSearchParams: URLSearchParams,
): SearchDownloadSearchParamsPatch {
  const patch: SearchDownloadSearchParamsPatch = {}
  for (const [key, rawValue] of Object.entries(next) as [SearchDownloadSearchParamKey, string | undefined][]) {
    const nextValue = normalizeSearchDownloadSearchParamValue(rawValue)
    const currentHasKey = currentSearchParams.has(key)
    const currentValue = normalizeSearchDownloadSearchParamValue(currentSearchParams.get(key))

    if (nextValue) {
      if (!currentHasKey || nextValue !== currentValue) patch[key] = nextValue
      continue
    }

    if (currentHasKey) patch[key] = null
  }
  return patch
}

export function applySearchDownloadSearchParamsPatch(
  currentSearchParams: URLSearchParams,
  next: SearchDownloadSearchParamsUpdate,
  replace = true,
): SearchDownloadSearchParamsApplyResult {
  const searchParams = new URLSearchParams(currentSearchParams)
  const patch = buildSearchDownloadSearchParamsPatch(next, currentSearchParams)

  for (const [key, value] of Object.entries(patch) as [SearchDownloadSearchParamKey, string | null][]) {
    if (value === null) searchParams.delete(key)
    else searchParams.set(key, value)
  }

  return { searchParams, replace }
}
