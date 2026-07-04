/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'

const pageDir = dirname(fileURLToPath(import.meta.url))

for (const page of ['JpParsing.tsx', 'HkParsing.tsx', 'KrParsing.tsx', 'EuParsing.tsx']) {
  test(`${page} matches the A-share PDF surface without evidence package panel`, () => {
    const source = readFileSync(resolve(pageDir, page), 'utf-8')

    assert.doesNotMatch(source, /MarketEvidencePackagesPanel/)
    assert.doesNotMatch(source, /extraPanel=/)
  })
}
