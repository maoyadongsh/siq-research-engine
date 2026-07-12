/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

const { createTaskRequestScope } = await import('./taskRequestScope.ts')

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((next) => {
    resolve = next
  })
  return { promise, resolve }
}

test('task request scope rejects a late response after task switching', async () => {
  let currentTaskId = 'task-a'
  const scope = createTaskRequestScope()
  const taskA = deferred<string>()
  const taskB = deferred<string>()
  const commits: string[] = []

  const tokenA = scope.begin(currentTaskId)
  const pendingA = taskA.promise.then((value) => {
    if (scope.isCurrent(tokenA, currentTaskId)) commits.push(value)
  })

  currentTaskId = 'task-b'
  const tokenB = scope.begin(currentTaskId)
  const pendingB = taskB.promise.then((value) => {
    if (scope.isCurrent(tokenB, currentTaskId)) commits.push(value)
  })

  taskB.resolve('result-b')
  await pendingB
  taskA.resolve('result-a')
  await pendingA

  assert.deepEqual(commits, ['result-b'])
})

test('task request scope keeps only the latest request for the same task', () => {
  let currentTaskId = 'task-a'
  const scope = createTaskRequestScope()
  const first = scope.begin(currentTaskId)
  const second = scope.begin(currentTaskId)

  assert.equal(scope.isCurrent(first, currentTaskId), false)
  assert.equal(scope.isCurrent(second, currentTaskId), true)

  scope.invalidate()
  assert.equal(scope.isCurrent(second, currentTaskId), false)
  currentTaskId = ''
  assert.equal(scope.isCurrent(scope.begin(currentTaskId), currentTaskId), true)
})
