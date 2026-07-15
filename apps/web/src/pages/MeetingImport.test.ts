import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const source = readFileSync(new URL('./MeetingImport.tsx', import.meta.url), 'utf8')

test('failed post-processing can be abandoned for a new recording import', () => {
  assert.match(source, /status\?\.state === 'failed'[\s\S]*onClick=\{resetImport\}[\s\S]*导入其他录音/)
  assert.match(source, /function resetImport\(\)[\s\S]*setStatus\(null\)[\s\S]*localStorage\.removeItem\(ACTIVE_UPLOAD_KEY\)/)
})
