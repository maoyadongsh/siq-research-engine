import { useState, type ReactNode } from 'react'
import AgentChatPanel, { type AgentChatPanelProps } from './AgentChatPanel'

interface PageWithAgentChatProps extends Omit<AgentChatPanelProps, 'collapsed' | 'onToggle'> {
  children: ReactNode
}

const VIEWPORT_HEIGHT = 'calc(100vh - 6rem - 1rem)'

export default function PageWithAgentChat({
  children,
  apiPrefix,
  title,
  description,
  quickQuestions,
  context,
}: PageWithAgentChatProps) {
  const [agentOpen, setAgentOpen] = useState(true)

  return (
    <div
      className="flex gap-5 overflow-hidden"
      style={{ height: VIEWPORT_HEIGHT }}
    >
      <div className="min-w-0 flex-1 overflow-y-auto pr-1 xl:pr-0">{children}</div>
      {agentOpen && (
        <button
          className="fixed inset-0 z-40 bg-slate-950/38 backdrop-blur-md xl:hidden"
          onClick={() => setAgentOpen(false)}
          aria-label="关闭助手"
        />
      )}
      <div className={`h-full shrink-0 xl:relative xl:z-auto ${agentOpen ? 'fixed bottom-0 right-0 top-[72px] z-50 xl:static' : ''}`}>
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
