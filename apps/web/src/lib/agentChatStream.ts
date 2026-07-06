import type { AgentProgress } from './agentChatTypes'
import { toolDisplayName } from './agentChatHistory'

export interface StreamApi {
  setCurrentSession(sessionId?: string | null): void
  setActiveRunId(runId: string | null): void
  startFirstEventTimer(): void
  clearFirstEventTimer(): void
  appendAssistantDelta(content: string): void
  flushAssistantDelta?(): void
  replaceAssistantContent(content: string): void
  updateAssistantProgress(progress: AgentProgress): void
  responseErrorMessage(res: Response, fallback: string): Promise<string>
}

export function createStreamConsumer(api: StreamApi) {
  async function consumeEventStream(res: Response) {
    if (!res.ok) throw new Error(await api.responseErrorMessage(res, `请求失败（HTTP ${res.status}）`))

    const reader = res.body?.getReader()
    if (!reader) throw new Error('不支持流式响应')

    const decoder = new TextDecoder()
    let buffer = ''
    let eventName = ''

    while (true) {
      const result = await reader.read()
      if (result.done) break
      buffer += decoder.decode(result.value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      for (const line of lines) {
        const trimmed = line.trim()
        if (!trimmed || trimmed.startsWith(':')) continue

        if (trimmed.startsWith('event:')) {
          eventName = trimmed.slice(6).trim()
          continue
        }

        if (trimmed.startsWith('data:')) {
          const data = trimmed.slice(5).trim()
          if (data === '[DONE]') continue
          try {
            const payload = JSON.parse(data)
            if (eventName === 'run' && payload.run_id) {
              api.setActiveRunId(payload.run_id)
              if (payload.session_id) api.setCurrentSession(payload.session_id)
              api.startFirstEventTimer()
              eventName = ''
              continue
            }
            if (eventName === 'progress') {
              if (payload?.title === '任务已启动') {
                api.startFirstEventTimer()
              } else {
                api.clearFirstEventTimer()
              }
              api.updateAssistantProgress(payload as AgentProgress)
              eventName = ''
              continue
            }
            if (eventName === 'replace' && typeof payload.content === 'string') {
              api.clearFirstEventTimer()
              api.flushAssistantDelta?.()
              api.replaceAssistantContent(payload.content)
              eventName = ''
              continue
            }
            if (eventName === 'tool') {
              api.clearFirstEventTimer()
              const displayTool = toolDisplayName(payload.tool, payload.preview)
              const toolProgress: AgentProgress = {
                status: payload.error ? 'error' : 'running',
                title: payload.status === 'completed' ? `${displayTool} 执行完成` : `正在执行 ${displayTool}`,
                detail: payload.preview || (payload.duration ? `耗时 ${payload.duration}s` : undefined),
                source: 'tool',
                tool: displayTool,
              }
              api.updateAssistantProgress(toolProgress)
              eventName = ''
              continue
            }
            if (eventName === 'reasoning' && payload.text) {
              api.clearFirstEventTimer()
              api.updateAssistantProgress({
                status: 'running',
                title: '正在推理',
                detail: String(payload.text).slice(0, 180),
                source: 'reasoning',
              })
              eventName = ''
              continue
            }
            if (eventName === 'done') {
              api.clearFirstEventTimer()
              api.flushAssistantDelta?.()
              if (typeof payload.content === 'string') {
                api.replaceAssistantContent(payload.content)
              }
              api.updateAssistantProgress({ status: 'completed', title: '任务完成', percent: 100, source: 'runtime' })
              eventName = ''
              continue
            }
            if (eventName === 'error') {
              api.clearFirstEventTimer()
              api.flushAssistantDelta?.()
              api.updateAssistantProgress({ status: 'error', title: '任务异常', detail: payload.message || payload.content, source: 'runtime' })
              eventName = ''
              continue
            }
            if (payload.content) {
              api.clearFirstEventTimer()
              api.appendAssistantDelta(payload.content)
              const inferred = inferProgressFromContent(payload.content)
              if (inferred) api.updateAssistantProgress(inferred)
            }
            eventName = ''
          } catch {
            if (data && data !== '[DONE]') {
              api.clearFirstEventTimer()
              api.appendAssistantDelta(data)
            }
            eventName = ''
          }
        }
      }
    }
    api.flushAssistantDelta?.()
  }

  return { consumeEventStream }
}

function inferProgressFromContent(content: string): AgentProgress | null {
  const lines = content.split('\n').slice(-12).reverse()
  for (const raw of lines) {
    const line = raw.trim()
    const match = line.match(/(?:\[[^\]]+\]\s*)?\[(\d{1,3})\/(\d{1,3})\]\s*(.+)/)
    if (!match) continue
    const current = Number(match[1])
    const total = Number(match[2])
    const body = match[3].replace(/\s+\[[█░▓▒#=\-\s]{3,}\]\s*/, ' · ')
    const [title, ...detailParts] = body.split(' · ')
    return {
      status: current >= total ? 'completed' : 'running',
      title: title.trim() || '正在执行任务',
      detail: detailParts.join(' · ').trim() || undefined,
      current,
      total,
      percent: total > 0 ? Math.max(0, Math.min(100, Math.round((current / total) * 100))) : undefined,
      source: 'agent_output',
    }
  }
  return null
}
