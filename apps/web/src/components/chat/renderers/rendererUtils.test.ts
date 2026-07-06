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

test('parseCitationActions removes bare source link helpers and dedupes generated buttons', () => {
  const parsed = parseCitationActions(
    '[1] source_type=wiki_document_links, task_id=task_a, pdf_page=137, table_index=165，打开PDF页(/api/pdf_page/task_a/137)，查看页来源(/api/source/task_a/page/137)，查看表格(/api/source/task_a/table/165)，[打开PDF定位页137](https://public.example/api/pdf_page/task_a/137?format=html)，[查看定位页137来源](https://public.example/api/source/task_a/page/137?format=html)，[查看可读表格165](https://public.example/api/source/task_a/table/165?format=html)',
  )

  assert.equal(parsed.text, '[1] source_type=wiki_document_links, task_id=task_a, pdf_page=137, table_index=165')
  assert.deepEqual(parsed.actions, [
    { label: '打开PDF定位页137', href: 'https://public.example/api/pdf_page/task_a/137?format=html', kind: 'pdf' },
    { label: '查看定位页137来源', href: 'https://public.example/api/source/task_a/page/137?format=html', kind: 'source' },
    { label: '查看可读表格165', href: 'https://public.example/api/source/task_a/table/165?format=html', kind: 'table' },
  ])
})
