/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

const { openDocumentResourceWithFeedback } = await import('./documentResourceOpener.ts')

test('document resource opener clears errors before successful open', async () => {
  const errors: string[] = []
  const calls: Array<[string, string | undefined]> = []

  await openDocumentResourceWithFeedback({
    url: '/api/documents/download/task-1',
    filename: 'task-1.zip',
    setResourceError: (value) => errors.push(value),
    openDocumentResourceImpl: async (url, filename) => {
      calls.push([url, filename])
    },
  })

  assert.deepEqual(errors, [''])
  assert.deepEqual(calls, [['/api/documents/download/task-1', 'task-1.zip']])
})

test('document resource opener reports thrown errors and ignores empty url', async () => {
  const errors: string[] = []
  let callCount = 0

  await openDocumentResourceWithFeedback({
    url: '/api/documents/download/task-2',
    setResourceError: (value) => errors.push(value),
    openDocumentResourceImpl: async () => {
      callCount += 1
      throw new Error('boom')
    },
  })

  await openDocumentResourceWithFeedback({
    url: '',
    setResourceError: (value) => errors.push(value),
    openDocumentResourceImpl: async () => {
      callCount += 1
    },
  })

  assert.equal(callCount, 1)
  assert.deepEqual(errors, ['', 'boom'])
})
