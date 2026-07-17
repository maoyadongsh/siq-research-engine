import { MessageCircle } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import AgentChatPanel, { type AgentChatPanelProps } from './AgentChatPanel'

interface PageWithAgentChatProps extends Omit<AgentChatPanelProps, 'collapsed' | 'onToggle'> {
  children: ReactNode
}

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
    <div className="agent-chat-page relative min-w-0">
      <div className="agent-chat-content min-w-0">{children}</div>
      {!agentOpen && (
        <button
          className="agent-chat-fab fixed bottom-5 right-5 z-40 flex h-12 min-w-12 items-center justify-center gap-2 rounded-full border border-[#0071e3] bg-[#ffffff] px-3 text-sm font-semibold text-text shadow-none"
          onClick={() => setAgentOpen(true)}
          aria-label={`打开${title}`}
        >
          <MessageCircle className="h-5 w-5 text-primary" />
          <span className="hidden lg:inline">{title}</span>
        </button>
      )}
      {agentOpen && (
        <button
          className="agent-chat-backdrop fixed inset-0 z-40 bg-black/20 backdrop-blur-sm"
          onClick={() => setAgentOpen(false)}
          aria-label="关闭助手"
        />
      )}
      <div className={`agent-chat-dock ${agentOpen ? 'is-open fixed z-50' : 'hidden'}`}>
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
