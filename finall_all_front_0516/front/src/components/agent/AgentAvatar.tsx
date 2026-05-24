import { useState } from 'react'

export type AgentAvatarKind = 'analysis' | 'factchecker' | 'tracking' | 'legal'
export type AgentAvatarState = 'idle' | 'thinking' | 'replying' | 'error'

interface AgentAvatarProps {
  kind: AgentAvatarKind
  state?: AgentAvatarState
  size?: 'sm' | 'md' | 'lg' | 'xl'
  label?: string
  className?: string
}

const avatarSrc: Record<AgentAvatarKind, string> = {
  analysis: '/pet/agent-drafts/finsight-analysis-avatar-animated-transparent.webp',
  factchecker: '/pet/agent-drafts/finsight-factchecker-avatar-animated-transparent.webp',
  tracking: '/pet/agent-drafts/finsight-tracking-avatar-animated-transparent.webp',
  legal: '/pet/agent-drafts/finsight-legal-avatar-animated-transparent.webp',
}

const sizeClass = {
  sm: 'agent-avatar-sm',
  md: 'agent-avatar-md',
  lg: 'agent-avatar-lg',
  xl: 'agent-avatar-xl',
}

export function agentKindFromApiPrefix(apiPrefix: string): AgentAvatarKind {
  if (apiPrefix.includes('factchecker')) return 'factchecker'
  if (apiPrefix.includes('tracking')) return 'tracking'
  if (apiPrefix.includes('legal')) return 'legal'
  return 'analysis'
}

export default function AgentAvatar({
  kind,
  state = 'idle',
  size = 'md',
  label = 'FinSight 智能体',
  className = '',
}: AgentAvatarProps) {
  const [imageReady, setImageReady] = useState(true)

  return (
    <span
      className={`agent-avatar agent-avatar-${kind} agent-avatar-${state} ${sizeClass[size]} ${imageReady ? 'agent-avatar-has-image' : 'agent-avatar-missing-image'} ${className}`}
      role="img"
      aria-label={label}
    >
      {imageReady && <span className="agent-avatar-aura" aria-hidden="true" />}
      {imageReady && (
        <img
          src={avatarSrc[kind]}
          className="agent-avatar-image"
          alt=""
          loading="lazy"
          draggable={false}
          onError={() => setImageReady(false)}
        />
      )}
      {imageReady && (
        <>
          <span className="agent-avatar-spark agent-avatar-spark-one" aria-hidden="true" />
          <span className="agent-avatar-spark agent-avatar-spark-two" aria-hidden="true" />
        </>
      )}
    </span>
  )
}
