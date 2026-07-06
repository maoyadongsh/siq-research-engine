/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'

const source = readFileSync(
  resolve(dirname(fileURLToPath(import.meta.url)), 'PdfSourceWorkbench.tsx'),
  'utf-8',
)

test('PdfSourceWorkbench page content cache is scoped by task and table identity', () => {
  assert.match(source, /pageContentCacheByScope/)
  assert.match(source, /pageContentScopeKey = `\$\{taskId \|\| ''\}:\$\{sourcePage\}:\$\{sourceTableIndex\}:\$\{srcTable\?\.line \|\| ''\}`/)
  assert.match(source, /\[pageContentScopeKey\]: \{/)
})
