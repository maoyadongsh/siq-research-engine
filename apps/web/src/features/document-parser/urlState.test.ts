/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

import { applyDocumentParserTaskSearchParam } from './urlState.ts'

test('applyDocumentParserTaskSearchParam writes the selected task and preserves unrelated params', () => {
  const current = new URLSearchParams('view=preview&task=old&keep=1')

  const result = applyDocumentParserTaskSearchParam(current, ' task-new ')

  assert.equal(result.replace, true)
  assert.equal(result.searchParams.toString(), 'view=preview&task=task-new&keep=1')
  assert.equal(current.toString(), 'view=preview&task=old&keep=1')
})

test('applyDocumentParserTaskSearchParam deletes task when selection clears', () => {
  const result = applyDocumentParserTaskSearchParam(new URLSearchParams('task=old&keep=1'), '')

  assert.equal(result.searchParams.toString(), 'keep=1')
})
