export interface TaskRequestToken {
  requestId: number
  taskId: string
}

export interface TaskRequestScope {
  begin: (taskId?: string | null) => TaskRequestToken
  invalidate: () => void
  isCurrent: (token: TaskRequestToken | null | undefined, currentTaskId?: string | null) => boolean
}

export function createTaskRequestScope(): TaskRequestScope {
  let latestRequestId = 0

  return {
    begin(taskId) {
      const normalizedTaskId = String(taskId || '').trim()
      latestRequestId += 1
      return { requestId: latestRequestId, taskId: normalizedTaskId }
    },
    invalidate() {
      latestRequestId += 1
    },
    isCurrent(token, currentTaskId) {
      return Boolean(
        token
        && token.requestId === latestRequestId
        && token.taskId === String(currentTaskId || '').trim(),
      )
    },
  }
}
