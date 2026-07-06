/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'

const pageDir = dirname(fileURLToPath(import.meta.url))

const marketPages = {
  JpParsing: 'JP',
  HkParsing: 'HK',
  EuParsing: 'EU',
  KrParsing: 'KR',
}

for (const [name, market] of Object.entries(marketPages)) {
  test(`${name}.tsx mounts the evidence package panel through the shared extension slot`, () => {
    const page = `${name}.tsx`
    const source = readFileSync(resolve(pageDir, page), 'utf-8')

    assert.match(source, /MarketEvidencePackagesPanel/)
    assert.match(source, new RegExp(`extraPanel=\\{<MarketEvidencePackagesPanel market="${market}" />\\}`))
  })
}
