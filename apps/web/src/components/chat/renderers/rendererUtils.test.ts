/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

import { parseCitationActions } from './rendererUtils.ts'

test('parseCitationActions keeps source task page and table text while extracting source links', () => {
  const parsed = parseCitationActions(
    '[1] source annual-report.pdf task task_20260630 page 12 table 3 [page source](/api/source/task_20260630/page/12), [table](/api/source/task_20260630/table/3)',
  )

  assert.equal(parsed.text, '[1] source annual-report.pdf task task_20260630 page 12 table 3')
  assert.deepEqual(parsed.actions, [
    { label: 'page source', href: '/api/source/task_20260630/page/12', kind: 'source' },
    { label: 'table', href: '/api/source/task_20260630/table/3', kind: 'table' },
  ])
})

test('parseCitationActions preserves normal markdown links and tolerates long citation lines', () => {
  const longTitle = Array.from({ length: 80 }, (_, index) => `segment-${index}`).join(' ')
  const parsed = parseCitationActions(
    `[2] source ${longTitle} task task_long page 88 table 9 see [filing](https://example.com/report.pdf) [source](/api/documents/source/task_long/page/88)`,
  )

  assert.match(parsed.text, /^\[2\] source segment-0/)
  assert.match(parsed.text, /task task_long page 88 table 9/)
  assert.match(parsed.text, /\[filing\]\(https:\/\/example.com\/report.pdf\)$/)
  assert.deepEqual(parsed.actions, [
    { label: 'source', href: '/api/documents/source/task_long/page/88', kind: 'source' },
  ])
})
