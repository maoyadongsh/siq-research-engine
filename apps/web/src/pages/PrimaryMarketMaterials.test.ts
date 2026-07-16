/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'

const source = readFileSync(new URL('./PrimaryMarketMaterials.tsx', import.meta.url), 'utf8')

test('primary-market materials exposes one Wiki-first pipeline for every material class', () => {
  assert.match(source, /Wiki-first 研究链路/)
  assert.match(source, /\['解析', parseStatus\]/)
  assert.match(source, /\['项目 Wiki', wikiStatus\]/)
  assert.match(source, /\['Evidence', evidenceStatus\]/)
  assert.match(source, /\['Milvus', materialMilvusStatus\]/)
  assert.match(source, /prospectus \? materialCapabilities\(material\) : \[\]/)
})

test('ordinary materials can start parsing or bind a pre-existing parser task in place', () => {
  assert.match(source, /parsePrimaryMarketMaterial\(selectedDealId, uploaded\.document_id\)/)
  assert.match(source, /parsePrimaryMarketMaterial\(selectedDealId, material\.document_id\)/)
  assert.match(source, /bindPrimaryMarketDocumentParserTask\(selectedDealId, material\.document_id/)
  assert.match(source, /重试解析/)
  assert.match(source, /绑定 Parser/)
})

test('Evidence build keeps Milvus receipt state visible and retryable', () => {
  assert.match(source, /applyPipelinePayload\(payload\)/)
  assert.match(source, /indexPrimaryMarketEvidenceMilvus\(selectedDealId\)/)
  assert.match(source, /\['failed', 'stale'\]\.includes\(milvusPipelineStatus\)/)
  assert.match(source, /snapshot \{shortHash\(milvusIndex\?\.snapshot_hash\)\}/)
})
