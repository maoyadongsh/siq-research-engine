/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

import {
  applySearchDownloadSearchParamsPatch,
  buildSearchDownloadMarketFilterPatch,
  buildSearchDownloadSearchParamsPatch,
  getSearchDownloadMarketFilterKey,
  readSearchDownloadInitialState,
} from './urlState.ts'

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
