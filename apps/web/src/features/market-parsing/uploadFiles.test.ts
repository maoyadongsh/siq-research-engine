/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

const {
  MARKET_PARSING_MAX_UPLOAD_BATCH_BYTES,
  MARKET_PARSING_MAX_UPLOAD_FILE_BYTES,
  validateMarketParsingUploadFiles,
  validateUsSecUploadFiles,
} = await import('./uploadFiles.ts')

function file(name: string, size = 1024) {
  return { name, size } as File
}

test('validateMarketParsingUploadFiles accepts up to five pdf files', () => {
  const files = [
    file('one.pdf'),
    file('two.PDF'),
    file('three.pdf'),
    file('four.pdf'),
    file('five.pdf'),
  ]

  const result = validateMarketParsingUploadFiles(files)

  assert.equal(result.error, null)
  assert.deepEqual(result.files, files)
})

test('validateMarketParsingUploadFiles leaves empty input as a no-op', () => {
  assert.deepEqual(validateMarketParsingUploadFiles([]), {
    files: [],
    error: null,
  })
})

test('validateMarketParsingUploadFiles rejects more than five files', () => {
  const result = validateMarketParsingUploadFiles([
    file('one.pdf'),
    file('two.pdf'),
    file('three.pdf'),
    file('four.pdf'),
    file('five.pdf'),
    file('six.pdf'),
  ])

  assert.deepEqual(result, {
    files: [],
    error: '一次最多选择 5 个 PDF',
  })
})

test('validateMarketParsingUploadFiles rejects non-pdf files', () => {
  const result = validateMarketParsingUploadFiles([file('annual.docx')])

  assert.deepEqual(result, {
    files: [],
    error: '仅支持 PDF 文件',
  })
})

test('validateMarketParsingUploadFiles rejects files larger than 100 MB', () => {
  const result = validateMarketParsingUploadFiles([
    file('large.pdf', MARKET_PARSING_MAX_UPLOAD_FILE_BYTES + 1),
  ])

  assert.deepEqual(result, {
    files: [],
    error: '文件超过 100 MB: large.pdf',
  })
})

test('validateMarketParsingUploadFiles accepts exactly 100 MB', () => {
  const files = [file('limit.pdf', MARKET_PARSING_MAX_UPLOAD_FILE_BYTES)]
  const result = validateMarketParsingUploadFiles(files)

  assert.equal(result.error, null)
  assert.deepEqual(result.files, files)
})

test('validateMarketParsingUploadFiles rejects empty files', () => {
  assert.deepEqual(validateMarketParsingUploadFiles([file('empty.pdf', 0)]), {
    files: [],
    error: '文件不能为空: empty.pdf',
  })
})

test('validateMarketParsingUploadFiles rejects batches larger than 200 MB', () => {
  const result = validateMarketParsingUploadFiles([
    file('one.pdf', MARKET_PARSING_MAX_UPLOAD_FILE_BYTES),
    file('two.pdf', MARKET_PARSING_MAX_UPLOAD_FILE_BYTES),
    file('three.pdf', 1),
  ])

  assert.deepEqual(result, {
    files: [],
    error: '文件总大小超过 200 MB',
  })
})

test('validateMarketParsingUploadFiles accepts exactly 200 MB', () => {
  const files = [
    file('one.pdf', MARKET_PARSING_MAX_UPLOAD_FILE_BYTES),
    file('two.pdf', MARKET_PARSING_MAX_UPLOAD_BATCH_BYTES - MARKET_PARSING_MAX_UPLOAD_FILE_BYTES),
  ]

  assert.deepEqual(validateMarketParsingUploadFiles(files), { files, error: null })
})

test('validateUsSecUploadFiles accepts the supported SEC suffixes case-insensitively', () => {
  const files = ['pdf', 'html', 'htm', 'xhtml', 'xml', 'xbrl', 'zip'].map((suffix, index) => (
    file(`filing-${index}.${index % 2 ? suffix.toUpperCase() : suffix}`)
  ))

  for (const item of files) {
    assert.deepEqual(validateUsSecUploadFiles([item]), { files: [item], error: null })
  }
})

test('validateUsSecUploadFiles applies the shared count, size and empty-file limits', () => {
  assert.equal(validateUsSecUploadFiles(Array.from({ length: 6 }, (_, index) => file(`${index}.htm`))).error, '一次最多选择 5 个文件')
  assert.equal(validateUsSecUploadFiles([file('large.xml', MARKET_PARSING_MAX_UPLOAD_FILE_BYTES + 1)]).error, '文件超过 100 MB: large.xml')
  assert.equal(validateUsSecUploadFiles([file('empty.zip', 0)]).error, '文件不能为空: empty.zip')
  assert.equal(validateUsSecUploadFiles([
    file('one.htm', MARKET_PARSING_MAX_UPLOAD_FILE_BYTES),
    file('two.xml', MARKET_PARSING_MAX_UPLOAD_FILE_BYTES),
    file('three.zip', 1),
  ]).error, '文件总大小超过 200 MB')
})

test('validateUsSecUploadFiles rejects unsupported suffixes', () => {
  assert.deepEqual(validateUsSecUploadFiles([file('notes.txt')]), {
    files: [],
    error: '仅支持 PDF / HTML / XHTML / XML / XBRL / ZIP 文件: notes.txt',
  })
})
