export interface SearchDownloadLogEntry {
  time: string
  msg: string
  type: string
}

export function getSearchDownloadVisibleLogs(logs: SearchDownloadLogEntry[], limit = 200) {
  return logs.slice(-limit)
}

export function hasSearchDownloadProblemLogs(logs: SearchDownloadLogEntry[]) {
  return logs.some((log) => log.type === 'error' || log.type === 'warn')
}
