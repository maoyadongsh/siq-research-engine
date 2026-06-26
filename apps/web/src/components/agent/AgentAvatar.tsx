import { useState } from 'react'
import { type AgentAvatarKind, type AgentAvatarState } from './agentAvatarTypes'

export type { AgentAvatarKind, AgentAvatarState }

interface AgentAvatarProps {
  kind: AgentAvatarKind
  state?: AgentAvatarState
  size?: 'sm' | 'md' | 'lg' | 'xl'
  label?: string
  className?: string
}

const avatarSrc: Record<AgentAvatarKind, string> = {
  analysis: '/pet/agent-drafts/siq-analysis-avatar-animated-transparent.webp',
  factchecker: '/pet/agent-drafts/siq-factchecker-avatar-animated-transparent.webp',
  tracking: '/pet/agent-drafts/siq-tracking-avatar-animated-transparent.webp',
  legal: '/pet/agent-drafts/siq-legal-avatar-animated-transparent.webp',
}

const sizeClass = {
  sm: 'agent-avatar-sm',
  md: 'agent-avatar-md',
  lg: 'agent-avatar-lg',
  xl: 'agent-avatar-xl',
}

export default function AgentAvatar({
  kind,
  state = 'idle',
  size = 'md',
  label = 'SIQ 智能体',
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
