/// <reference types="node" />

import { strict as assert } from 'node:assert'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'

const componentDir = dirname(fileURLToPath(import.meta.url))
const taskListSource = readFileSync(resolve(componentDir, 'PdfTaskList.tsx'), 'utf-8')
const taskHookSource = readFileSync(resolve(componentDir, '../../pages/pdf/usePdfTasks.ts'), 'utf-8')
const pageSource = readFileSync(resolve(componentDir, '../../pages/MarketParsingPage.tsx'), 'utf-8')

test('PdfTaskList renders explicit loading, error, and empty states with refresh controls', () => {
  assert.match(taskListSource, /tasksLoading: boolean/)
  assert.match(taskListSource, /tasksError: string \| null/)
  assert.match(taskListSource, /正在加载最近任务/)
  assert.match(taskListSource, /最近任务加载失败/)
  assert.match(taskListSource, /暂无最近任务/)
  assert.match(taskListSource, /RefreshCw/)
  assert.doesNotMatch(taskListSource, /if \(tasks\.length === 0\) return null/)
})

test('PDF task loading exposes failures and only commits the latest list request', () => {
  assert.match(taskHookSource, /const \[tasksLoading, setTasksLoading\] = useState\(true\)/)
  assert.match(taskHookSource, /const \[tasksError, setTasksError\] = useState<string \| null>\(null\)/)
  assert.match(taskHookSource, /loadTasksApi\(\{ signal: request\.signal \}\)/)
  assert.match(taskHookSource, /if \(!tasksRequestScope\.isCurrent\(request\)\) return/)
  assert.match(taskHookSource, /setTasksError\(visibleErrorMessage\(error,/)
  assert.doesNotMatch(taskHookSource, /ignore task list load errors/)
  assert.match(pageSource, /tasksLoading=\{tasks\.tasksLoading\}/)
  assert.match(pageSource, /tasksError=\{tasks\.tasksError\}/)
})
