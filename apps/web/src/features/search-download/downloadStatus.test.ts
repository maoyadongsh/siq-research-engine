/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

import {
  buildAllDownloadsFinishedLog,
  buildBatchDownloadCompleteLog,
  buildBatchDownloadFallbackLog,
  buildDownloadedReportDeleteFailureToast,
  buildDownloadedReportDeleteToast,
  buildDownloadedReportOpenFailureToast,
  buildIndividualDownloadLogs,
  buildQuickDownloadCompleteLog,
  buildQuickDownloadFailureLog,
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

test('download log helpers keep batch and fallback copy stable', () => {
  assert.deepEqual(buildBatchDownloadCompleteLog({ succeeded: 2, failed: 1 }), {
    type: 'success',
    message: '下载完成: 成功 2, 失败 1',
  })
  assert.deepEqual(buildBatchDownloadFallbackLog(new Error('network down')), {
    type: 'warn',
    message: '批量下载失败: network down, 尝试逐个下载...',
  })
  assert.deepEqual(buildAllDownloadsFinishedLog(), {
    type: 'success',
    message: '全部下载任务完成',
  })
})

test('buildIndividualDownloadLogs maps each report outcome to log severity', () => {
  assert.deepEqual(
    buildIndividualDownloadLogs([
      { success: true, report: { title: 'Annual report' } as never },
      { success: false, report: { title: 'Quarterly report' } as never },
    ]),
    [
      { type: 'success', message: '下载成功: Annual report' },
      { type: 'error', message: '下载失败: Quarterly report' },
    ],
  )
})

test('quick download log helpers keep completion and failure copy stable', () => {
  assert.deepEqual(buildQuickDownloadCompleteLog({ companyName: 'Demo Co', succeeded: 3, total: 4 }), {
    type: 'success',
    message: '下载完成: Demo Co 成功 3/4',
  })
  assert.deepEqual(buildQuickDownloadFailureLog(new Error('missing source')), {
    type: 'error',
    message: '下载失败: missing source',
  })
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
