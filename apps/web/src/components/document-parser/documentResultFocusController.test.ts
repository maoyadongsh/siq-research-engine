/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

import type { FocusTarget } from './documentResultWorkbenchUtils.ts'

const {
  documentResultFocusControllerReducer,
  firstDocumentResultPage,
} = await import('./documentResultFocusController.ts')

const initialState = {
  activePage: 1,
  focused: null,
  activeTab: 'preview',
}

test('focus controller syncs active page for block table figure and page targets', () => {
  const targets: NonNullable<FocusTarget>[] = [
    { kind: 'block', id: 'block-2', page: 2 },
    { kind: 'table', id: 'table-3', page: 3 },
    { kind: 'figure', id: 'figure-4', page: 4 },
  ]

  for (const target of targets) {
    const nextState = documentResultFocusControllerReducer(initialState, { type: 'focus', target })
    assert.equal(nextState.activePage, target.page)
    assert.deepEqual(nextState.focused, target)
    assert.equal(nextState.activeTab, 'preview')
  }

  const pageState = documentResultFocusControllerReducer(initialState, { type: 'selectPage', page: 5 })
  assert.equal(pageState.activePage, 5)
  assert.deepEqual(pageState.focused, { kind: 'page', id: 'page-5', page: 5 })
  assert.equal(pageState.activeTab, 'preview')
})

test('focus controller resets focus and active page for a new task', () => {
  const dirtyState = {
    activePage: 8,
    focused: { kind: 'table', id: 'table-8', page: 8 } as FocusTarget,
    activeTab: 'tables',
  }

  const resetState = documentResultFocusControllerReducer(dirtyState, { type: 'resetTask', pageNumbers: [2, 3, 4] })
  assert.equal(resetState.activePage, 2)
  assert.equal(resetState.focused, null)
  assert.equal(resetState.activeTab, 'tables')

  const emptyResetState = documentResultFocusControllerReducer(dirtyState, { type: 'resetTask', pageNumbers: [] })
  assert.equal(emptyResetState.activePage, 1)
  assert.equal(emptyResetState.focused, null)
  assert.equal(emptyResetState.activeTab, 'tables')
  assert.equal(firstDocumentResultPage([]), 1)
})

test('mobile result tab changes do not pollute active page or focus state', () => {
  const focusedState = {
    activePage: 6,
    focused: { kind: 'page', id: 'page-6', page: 6 } as FocusTarget,
    activeTab: 'preview',
  }

  const tabState = documentResultFocusControllerReducer(focusedState, { type: 'setActiveTab', tab: 'figures' })
  assert.equal(tabState.activeTab, 'figures')
  assert.equal(tabState.activePage, 6)
  assert.deepEqual(tabState.focused, focusedState.focused)

  const refocusedState = documentResultFocusControllerReducer(tabState, {
    type: 'focus',
    target: { kind: 'block', id: 'block-7', page: 7 },
  })
  assert.equal(refocusedState.activeTab, 'figures')
  assert.equal(refocusedState.activePage, 7)
  assert.deepEqual(refocusedState.focused, { kind: 'block', id: 'block-7', page: 7 })
})
