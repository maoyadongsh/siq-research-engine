import test from 'node:test'
import assert from 'node:assert/strict'
import { ArtifactContentCache } from './artifactContentCache'

test('artifact content cache is LRU bounded by entry count', () => {
  const cache = new ArtifactContentCache(2, 100)
  cache.set('a', 'alpha')
  cache.set('b', 'beta')
  assert.equal(cache.get('a'), 'alpha')
  cache.set('c', 'charlie')

  assert.equal(cache.get('b'), undefined)
  assert.equal(cache.get('a'), 'alpha')
  assert.equal(cache.get('c'), 'charlie')
})

test('artifact content cache rejects a single oversized report and honors total size', () => {
  const cache = new ArtifactContentCache(3, 8)
  cache.set('oversized', '123456789')
  assert.equal(cache.get('oversized'), undefined)

  cache.set('a', '12345')
  cache.set('b', '6789')
  assert.equal(cache.get('a'), undefined)
  assert.equal(cache.get('b'), '6789')
})
