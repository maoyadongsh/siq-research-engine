/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'

const source = readFileSync(new URL('./PrimaryMarketMeeting.tsx', import.meta.url), 'utf8')

test('primary-market readiness separates chat service from formal task gates', () => {
  assert.match(source, /智能体服务状态与双库检索/)
  assert.match(source, /聊天服务可用/)
  assert.match(source, /正式任务可执行/)
  assert.match(source, /shared \{row\.sharedConnected \? 'connected' : 'disconnected'\}/)
  assert.match(source, /private \{row\.privateConnected \? 'connected' : 'disconnected'\}/)
  assert.match(source, /0 hits · 当前项目暂无可检索 Evidence/)
  assert.doesNotMatch(source, /title="Agent Readiness 与双库检索"/)
})

test('rerank status uses its own tone instead of formal-task readiness', () => {
  assert.match(source, /tone=\{row\.rerankTone\}>rerank \{row\.rerankStatus\}/)
  assert.match(source, /formal retrieval \{row\.retrievalStatus\}/)
})

test('readiness refreshes after material pipeline changes without executing tasks', () => {
  assert.match(source, /fetchPrimaryMarketMeetingAgentReadiness\(selectedDealId, controller\.signal\)/)
  assert.match(source, /window\.setInterval\(\(\) => void refreshReadiness\(\), 20_000\)/)
  assert.match(source, /document\.addEventListener\('visibilitychange', handleVisibility\)/)
})
