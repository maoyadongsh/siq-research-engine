/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { test } from 'node:test'

const source = readFileSync(
  resolve(process.cwd(), 'src/pages/pdf/usePdfTasks.ts'),
  'utf-8',
)

test('PDF result fetch failures are surfaced and leave a retry gate visible', () => {
  assert.match(source, /catch \(error\) \{/)
  assert.match(source, /setResultDeferred\(true\)/)
  assert.match(source, /reportError\(`解析结果拉取失败：/)
})
