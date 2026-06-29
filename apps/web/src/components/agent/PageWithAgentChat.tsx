import { MessageCircle } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import AgentChatPanel, { type AgentChatPanelProps } from './AgentChatPanel'

interface PageWithAgentChatProps extends Omit<AgentChatPanelProps, 'collapsed' | 'onToggle'> {
  children: ReactNode
}

const VIEWPORT_HEIGHT = 'calc(100dvh - var(--app-topbar-height) - var(--app-content-y))'
export default function PageWithAgentChat({
  children,
  apiPrefix,
  title,
  description,
  quickQuestions,
  context,
}: PageWithAgentChatProps) {
  const [agentOpen, setAgentOpen] = useState(false)

  return (
    <div
      className="agent-chat-page relative flex gap-5 overflow-hidden"
      style={{ height: VIEWPORT_HEIGHT }}
    >
      <div className="agent-chat-content min-w-0 flex-1 overflow-y-auto pr-1 xl:pr-0">{children}</div>
      {!agentOpen && (
        <button
          className="agent-chat-fab fixed bottom-4 right-4 z-40 flex h-14 min-w-14 items-center justify-center gap-2 rounded-[var(--radius-panel)] border border-white/80 bg-white px-4 text-sm font-semibold text-text shadow-[0_16px_44px_rgba(15,23,42,0.16)] backdrop-blur [@media(min-width:640px)]:hidden"
          onClick={() => setAgentOpen(true)}
          aria-label={`打开${title}`}
        >
          <MessageCircle className="h-5 w-5 text-primary" />
          <span className="hidden lg:inline">{title}</span>
        </button>
      )}
      {agentOpen && (
        <button
          className="agent-chat-backdrop fixed inset-0 z-40 bg-slate-950/38 backdrop-blur-md [@media(min-width:640px)]:hidden"
          onClick={() => setAgentOpen(false)}
          aria-label="关闭助手"
        />
      )}
      <div className={`agent-chat-dock h-full shrink-0 [@media(min-width:640px)]:relative [@media(min-width:640px)]:z-auto ${agentOpen ? 'is-open fixed z-50 [@media(min-width:640px)]:static' : 'is-closed hidden [@media(min-width:640px)]:block'}`}>
        <AgentChatPanel
          apiPrefix={apiPrefix}
          title={title}
          description={description}
          quickQuestions={quickQuestions}
          context={context}
          collapsed={!agentOpen}
          onToggle={() => setAgentOpen((o) => !o)}
        />
      </div>
    </div>
  )
}
