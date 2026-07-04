/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'

const pageDir = dirname(fileURLToPath(import.meta.url))

test('KrParsing.tsx mounts the KR evidence packages panel', () => {
  const source = readFileSync(resolve(pageDir, 'KrParsing.tsx'), 'utf-8')

  assert.match(source, /MarketEvidencePackagesPanel/)
  assert.match(source, /extraPanel=\{<MarketEvidencePackagesPanel market="KR" \/>\}/)
})

for (const page of ['JpParsing.tsx', 'HkParsing.tsx', 'EuParsing.tsx']) {
  test(`${page} matches the A-share PDF surface without evidence package panel`, () => {
    const source = readFileSync(resolve(pageDir, page), 'utf-8')

    assert.doesNotMatch(source, /MarketEvidencePackagesPanel/)
    assert.doesNotMatch(source, /extraPanel=/)
  })
}
