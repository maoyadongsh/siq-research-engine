/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'

const componentSource = readFileSync(
  resolve(dirname(fileURLToPath(import.meta.url)), 'MarketEvidencePackagesPanel.tsx'),
  'utf-8',
)

test('force gate confirmation exposes reasons, force_allowed, and audit consequences', () => {
  assert.match(componentSource, /Gate reason:/)
  assert.match(componentSource, /force_allowed:/)
  assert.match(componentSource, /审计后果/)
  assert.match(componentSource, /force=true/)
  assert.match(componentSource, /不会改变原始 gate 结果/)
})

test('force gate confirmation blocks actions when force_allowed is false', () => {
  assert.match(componentSource, /gates\.force_allowed === false/)
  assert.match(componentSource, /window\.alert/)
  assert.match(componentSource, /操作已取消/)
})
