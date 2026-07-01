/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

import {
  applySearchDownloadSearchParamsPatch,
  buildSearchDownloadMarketFilterPatch,
  buildSearchDownloadSearchParamsPatch,
  getSearchDownloadMarketFilterKey,
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
