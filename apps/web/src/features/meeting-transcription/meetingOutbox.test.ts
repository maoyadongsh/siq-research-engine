/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

import { MemoryMeetingOutboxStore } from './meetingOutbox.ts'

function frame(value: number) {
  return Uint8Array.from([value]).buffer
}

test('meeting outbox restores unacknowledged frames and the original stream identity', async () => {
  const store = new MemoryMeetingOutboxStore()

  await store.putFrame('meeting-1', 3, 'client-stream-1', -1, 0, frame(10))
  await store.putFrame('meeting-1', 3, 'client-stream-1', -1, 1, frame(11))

  const restored = await store.restore('meeting-1', 3)
  assert.equal(restored.clientStreamId, 'client-stream-1')
  assert.equal(restored.lastAckedSequence, -1)
  assert.deepEqual([...restored.frames.keys()], [0, 1])
  const restoredFrame = restored.frames.get(1)
  assert.ok(restoredFrame)
  assert.deepEqual([...new Uint8Array(restoredFrame)], [11])
})

test('meeting outbox removes acknowledged frames without touching another epoch', async () => {
  const store = new MemoryMeetingOutboxStore()
  await store.putFrame('meeting-1', 4, 'client-stream-1', -1, 0, frame(20))
  await store.putFrame('meeting-1', 4, 'client-stream-1', -1, 1, frame(21))
  await store.putFrame('meeting-1', 5, 'client-stream-2', -1, 0, frame(30))

  await store.acknowledge('meeting-1', 4, 'client-stream-1', 0)

  const current = await store.restore('meeting-1', 4)
  const next = await store.restore('meeting-1', 5)
  assert.equal(current.lastAckedSequence, 0)
  assert.deepEqual([...current.frames.keys()], [1])
  assert.deepEqual([...next.frames.keys()], [0])
})

test('meeting outbox clear is scoped to one meeting and epoch', async () => {
  const store = new MemoryMeetingOutboxStore()
  await store.putFrame('meeting-1', 1, 'client-1', -1, 0, frame(1))
  await store.putFrame('meeting-2', 1, 'client-2', -1, 0, frame(2))

  await store.clear('meeting-1', 1)

  assert.equal((await store.restore('meeting-1', 1)).frames.size, 0)
  assert.equal((await store.restore('meeting-2', 1)).frames.size, 1)
})
