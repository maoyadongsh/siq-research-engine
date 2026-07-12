/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

import { createRequestScope } from './requestScope.ts'

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((next) => {
    resolve = next
  })
  return { promise, resolve }
}

test('request scope commits only C when A, B, C resolve out of order', async () => {
  const scope = createRequestScope()
  const requests = {
    a: deferred<string>(),
    b: deferred<string>(),
    c: deferred<string>(),
  }
  const commits: string[] = []

  const tokenA = scope.begin('task-a')
  const pendingA = requests.a.promise.then((value) => {
    if (scope.isCurrent(tokenA, 'task-a')) commits.push(value)
  })
  const tokenB = scope.begin('task-b')
  const pendingB = requests.b.promise.then((value) => {
    if (scope.isCurrent(tokenB, 'task-b')) commits.push(value)
  })
  const tokenC = scope.begin('task-c')
  const pendingC = requests.c.promise.then((value) => {
    if (scope.isCurrent(tokenC, 'task-c')) commits.push(value)
  })

  assert.equal(tokenA.signal.aborted, true)
  assert.equal(tokenB.signal.aborted, true)
  assert.equal(tokenC.signal.aborted, false)

  requests.b.resolve('result-b')
  requests.c.resolve('result-c')
  requests.a.resolve('result-a')
  await Promise.all([pendingA, pendingB, pendingC])

  assert.deepEqual(commits, ['result-c'])
})

test('an old poll owner cannot invalidate a newer poll owner', () => {
  const scope = createRequestScope()
  const pollA = scope.begin('task-a')
  const pollB = scope.begin('task-b')

  assert.equal(scope.invalidate(pollA), false)
  assert.equal(scope.isCurrent(pollB, 'task-b'), true)
  assert.equal(scope.invalidate(pollB), true)
  assert.equal(scope.isCurrent(pollB, 'task-b'), false)
  assert.equal(pollB.signal.aborted, true)
})
