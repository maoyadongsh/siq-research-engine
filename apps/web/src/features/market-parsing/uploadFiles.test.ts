/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { test } from 'node:test'

const {
  MARKET_PARSING_MAX_UPLOAD_FILE_BYTES,
  validateMarketParsingUploadFiles,
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
