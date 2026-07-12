/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

import {
  applySearchDownloadSearchParamsPatch,
  buildSearchDownloadMarketFilterPatch,
  buildSearchDownloadSearchParamsPatch,
  buildSearchDownloadUrlStateUpdate,
  getSearchDownloadMarketFilterKey,
  readSearchDownloadInitialState,
  sameSearchDownloadUrlState,
} from './urlState.ts'

test('URL-backed search state roundtrips atomically across market changes', () => {
  const current = new URLSearchParams('keep=1&market=EU&q=ASML&year=2024&country=NL')
  const nextState = {
    market: 'US' as const,
    query: 'Apple',
    year: '2025',
    marketFilter: '10-K',
    downloadedQuery: 'apple',
    smartPrompt: '找 Apple 2025 年 10-K',
  }

  const result = applySearchDownloadSearchParamsPatch(
    current,
    buildSearchDownloadUrlStateUpdate(nextState),
    false,
  )

  assert.deepEqual(readSearchDownloadInitialState(result.searchParams), nextState)
  assert.equal(result.searchParams.get('country'), null)
  assert.equal(result.searchParams.get('form'), '10-K')
  assert.equal(result.searchParams.get('keep'), '1')
  assert.equal(result.replace, false)
})

test('URL state equality distinguishes a history transition from the local canonical state', () => {
  const current = readSearchDownloadInitialState(new URLSearchParams('market=CN&q=BYD&year=2025'))
  const same = { ...current }
  const previous = { ...current, market: 'US' as const, query: 'AAPL' }

  assert.equal(sameSearchDownloadUrlState(current, same), true)
  assert.equal(sameSearchDownloadUrlState(current, previous), false)
})

test('buildSearchDownloadSearchParamsPatch trims values and deletes cleared params', () => {
  const current = new URLSearchParams('q=old&exchange=SSE&keep=1&ask=')

  const patch = buildSearchDownloadSearchParamsPatch(
    {
      q: ' old ',
      downloaded: ' report ',
      ask: '',
      market: 'CN',
    },
    current,
  )

  assert.deepEqual(patch, {
    downloaded: 'report',
    ask: null,
    market: 'CN',
  })
})

test('applySearchDownloadSearchParamsPatch clones current params and forwards replace', () => {
  const current = new URLSearchParams('q=old&exchange=SSE&keep=1')

  const result = applySearchDownloadSearchParamsPatch(
    current,
    {
      q: ' new ',
      exchange: '',
      downloaded: 'report',
    },
    false,
  )

  assert.equal(result.replace, false)
  assert.equal(result.searchParams.toString(), 'q=new&keep=1&downloaded=report')
  assert.equal(current.toString(), 'q=old&exchange=SSE&keep=1')
})

test('buildSearchDownloadMarketFilterPatch maps the market filter key', () => {
  assert.equal(getSearchDownloadMarketFilterKey('US'), 'form')
  assert.deepEqual(buildSearchDownloadMarketFilterPatch('EU', 'FR'), {
    country: 'FR',
    exchange: '',
    form: '',
  })
})

test('readSearchDownloadInitialState reads URL-backed page defaults', () => {
  const params = new URLSearchParams({
    market: 'EU',
    q: 'ASML',
    year: '2024',
    country: 'NL',
    downloaded: 'asml',
    ask: '找 ASML 年报',
  })

  assert.deepEqual(readSearchDownloadInitialState(params), {
    market: 'EU',
    query: 'ASML',
    year: '2024',
    marketFilter: 'NL',
    downloadedQuery: 'asml',
    smartPrompt: '找 ASML 年报',
  })
})

test('readSearchDownloadInitialState keeps current fallbacks and filter priority', () => {
  const params = new URLSearchParams({
    market: 'BAD',
    exchange: 'SSE',
    form: '10-K',
    country: 'FR',
  })

  assert.deepEqual(readSearchDownloadInitialState(params), {
    market: 'CN',
    query: '',
    year: '2025',
    marketFilter: 'SSE',
    downloadedQuery: '',
    smartPrompt: '',
  })
})

test('readSearchDownloadInitialState does not trim initial URL values', () => {
  const params = new URLSearchParams({
    q: '  spaced  ',
    ask: '  prompt  ',
  })

  const state = readSearchDownloadInitialState(params)

  assert.equal(state.query, '  spaced  ')
  assert.equal(state.smartPrompt, '  prompt  ')
})
