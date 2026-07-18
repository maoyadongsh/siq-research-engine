/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

import {
  dealComponentLabel,
  dealComponentMessage,
  dealNextActionLabel,
  dealStatusLabel,
  dealWarningLabel,
} from './dealDisplay.ts'

test('deal workspace maps backend status codes to concise Chinese labels', () => {
  assert.equal(dealStatusLabel('r4_completed'), '投决已完成')
  assert.equal(dealStatusLabel('completed'), '已完成')
  assert.equal(dealStatusLabel('warn'), '待关注')
  assert.equal(dealNextActionLabel('resolve_blocking_contracts'), '补齐阻断项并重新校验')
})

test('deal workspace localizes component labels, messages, and warnings', () => {
  const component = {
    id: 'r1_expert_reports',
    label: 'R1 Expert Reports',
    status: 'warn',
    message: '0 pass, 6 warn, 0 missing.',
  }

  assert.equal(dealComponentLabel(component), 'R1 专家报告')
  assert.equal(dealComponentMessage(component), '0 项通过，6 项待关注，0 项缺失。')
  assert.equal(dealWarningLabel('required_event_missing:deal_created'), '缺少项目创建审计事件')
  assert.equal(dealComponentLabel({ id: 'r1_5_disputes' }), 'R1.5 修订争议')
})
