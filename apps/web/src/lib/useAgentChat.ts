import { useCallback, useEffect, useMemo, useSyncExternalStore } from 'react'
import type { UseAgentChatOptions } from './agentChatTypes'
import { agentChatAuthKey } from './agentChatAuth'
import { getAgentChatStore, resetAgentChatStores } from './agentChatStore'

export * from './agentChatTypes'
export { resetAgentChatStores }

export function useAgentChat(apiPrefix: string, options: UseAgentChatOptions = {}) {
  const { autoInitialize = true } = options
  const authKey = agentChatAuthKey()
  const store = useMemo(() => getAgentChatStore(apiPrefix, authKey), [apiPrefix, authKey])
  const snapshot = useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot)

  useEffect(() => {
    if (autoInitialize) store.initialize()
  }, [autoInitialize, store])

  const sendMessage = useCallback((text?: string, context?: import('./agentChatTypes').AgentChatContext, displayMessage?: string) => store.sendMessage(text, context, displayMessage), [store])
  const newChat = useCallback(() => store.newChat(), [store])
  const clearChat = useCallback(() => store.clearChat(), [store])
  const refreshHistory = useCallback(() => store.refreshHistory(), [store])
  const initialize = useCallback(() => store.initialize(), [store])
  const loadSessions = useCallback(() => store.loadSessions(), [store])
  const switchSession = useCallback((sessionId: string) => store.switchSession(sessionId), [store])
  const stop = useCallback(() => store.stop(), [store])
  const setInput = useCallback((value: string) => store.setInput(value), [store])
  const setComposing = useCallback((value: boolean) => store.setComposing(value), [store])
  const uploadAttachments = useCallback((files: FileList | File[]) => store.uploadAttachments(files), [store])
  const removeAttachment = useCallback((id: string) => store.removeAttachment(id), [store])

  return {
    messages: snapshot.messages,
    sessions: snapshot.sessions,
    loadingSessions: snapshot.loadingSessions,
    sessionsLoaded: snapshot.sessionsLoaded,
    attachments: snapshot.attachments,
    uploadingAttachments: snapshot.uploadingAttachments,
    input: snapshot.input,
    setInput,
    initialize,
    sending: snapshot.sending,
    composing: snapshot.composing,
    setComposing,
    sendMessage,
    newChat,
    refreshHistory,
    loadSessions,
    switchSession,
    clearChat,
    stop,
    uploadAttachments,
    removeAttachment,
  }
}
