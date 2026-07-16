/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

import type { PrimaryMarketMaterial } from '@/lib/dealTypes'
import {
  materialMilvusStage,
  materialWikiStage,
  projectWikiStage,
} from './primaryMarketMaterialPipeline.ts'

const material = {
  document_id: 'DOC-001',
  deal_id: 'DEAL-001',
  filename: 'memo.pdf',
} as PrimaryMarketMaterial

test('project Wiki readiness requires real material projections, not only a catalog hash', () => {
  assert.equal(projectWikiStage({ catalog_hash: 'catalog', counts: { company_wiki_projections: 0 } }, 1), 'pending')
  assert.equal(projectWikiStage({ counts: { company_wiki_projections: 1 } }, 2), 'partial')
  assert.equal(projectWikiStage({ counts: { company_wiki_projections: 2 } }, 2), 'ready')
})

test('material Wiki readiness prefers its own projection and then the document index entry', () => {
  assert.equal(materialWikiStage({ ...material, wiki_status: 'failed' }), 'failed')
  assert.equal(materialWikiStage({ ...material, wiki_path: 'company_wiki/finance/DOC-001.md' }), 'ready')
  assert.equal(materialWikiStage(material, { entry_type: 'company_wiki_projection' }), 'ready')
  assert.equal(materialWikiStage(material), 'pending')
})

test('project Milvus success cannot promote a material that has no Evidence', () => {
  const snapshot = 'a'.repeat(64)
  assert.equal(materialMilvusStage(undefined, { status: 'indexed', snapshot_hash: snapshot }, snapshot), 'pending')
  assert.equal(materialMilvusStage({ status: 'missing', items: 0 }, { status: 'indexed', snapshot_hash: snapshot }, snapshot), 'pending')
})

test('material Milvus readiness requires Evidence and the current receipt snapshot', () => {
  const snapshot = 'b'.repeat(64)
  const evidence = { status: 'ready', items: 3 }
  assert.equal(materialMilvusStage(evidence, { status: 'indexed', snapshot_hash: snapshot }, snapshot), 'indexed')
  assert.equal(materialMilvusStage(evidence, { status: 'unchanged', snapshot_hash: snapshot }, snapshot), 'indexed')
  assert.equal(materialMilvusStage(evidence, { status: 'indexed', snapshot_hash: 'c'.repeat(64) }, snapshot), 'stale')
  assert.equal(materialMilvusStage(evidence, { status: 'failed', snapshot_hash: snapshot }, snapshot), 'failed')
})
