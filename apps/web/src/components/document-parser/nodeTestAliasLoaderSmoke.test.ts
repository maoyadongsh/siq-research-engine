/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

const { cn } = await import('@/lib/utils')
const { buildDocumentResultJsonPreview } = await import('./documentResultWorkbenchDerivations')

test('node test alias loader resolves runtime aliases and extensionless relative imports', () => {
  assert.equal(cn('base', false, 'active'), 'base active')
  const preview = {
    manifest: { task_id: 'task-42' },
    blocks: null,
    tables: null,
    figures: null,
    sourceMap: null,
  }

  assert.deepEqual(buildDocumentResultJsonPreview(preview), preview)
})
