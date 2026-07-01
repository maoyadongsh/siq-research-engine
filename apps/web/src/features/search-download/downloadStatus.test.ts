/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

import {
  buildDownloadedReportDeleteFailureToast,
  buildDownloadedReportDeleteToast,
  buildDownloadedReportOpenFailureToast,
  shouldRefreshDownloadedReports,
  summarizeDownloadResults,
} from './downloadStatus.ts'

test('summarizeDownloadResults counts success and failure entries', () => {
  const summary = summarizeDownloadResults([
    { success: true },
    { success: false },
    {},
  ])

  assert.deepEqual(summary, {
    total: 3,
    succeeded: 2,
    failed: 1,
    hasSuccess: true,
    hasFailure: true,
  })
})

test('shouldRefreshDownloadedReports only refreshes when at least one item succeeds', () => {
  assert.equal(shouldRefreshDownloadedReports([{ success: false }]), false)
  assert.equal(shouldRefreshDownloadedReports([{ success: false }, {}]), true)
})

test('toast helpers keep delete and open failure copy in one place', () => {
  assert.deepEqual(buildDownloadedReportDeleteToast({ filename: 'demo.pdf' } as never), {
    type: 'success',
    title: '文件已删除',
    description: 'demo.pdf',
  })
  assert.deepEqual(buildDownloadedReportDeleteFailureToast(), {
    type: 'error',
    title: '删除失败',
    description: '请确认后端服务可用，且文件仍在 downloads 目录内。',
  })
  assert.deepEqual(buildDownloadedReportOpenFailureToast(), {
    type: 'error',
    title: '打开失败',
    description: '请确认登录状态有效，且文件仍在 downloads 目录内。',
  })
})
