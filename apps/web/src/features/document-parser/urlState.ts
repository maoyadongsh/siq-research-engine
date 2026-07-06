export interface DocumentParserTaskSearchParamsApplyResult {
  searchParams: URLSearchParams
  replace: boolean
}

function normalizeTaskId(value: string | null | undefined) {
  return String(value ?? '').trim()
}

export function applyDocumentParserTaskSearchParam(
  currentSearchParams: URLSearchParams,
  taskId: string | null | undefined,
  replace = true,
): DocumentParserTaskSearchParamsApplyResult {
  const nextTaskId = normalizeTaskId(taskId)
  const searchParams = new URLSearchParams(currentSearchParams)

  if (nextTaskId) searchParams.set('task', nextTaskId)
  else searchParams.delete('task')

  return { searchParams, replace }
}
