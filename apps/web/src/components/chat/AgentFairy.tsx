import { useState } from 'react'

export type AgentFairyState = 'idle' | 'thinking' | 'replying' | 'error'

interface AgentFairyProps {
  state?: AgentFairyState
  size?: 'sm' | 'md' | 'lg' | 'xl' | 'float'
  label?: string
  className?: string
  imageSrc?: string
}

const DEFAULT_IMAGE_SRC = '/agent/siq-avatar-animated.webp'

const sizeClass = {
  sm: 'agent-fairy-sm',
  md: 'agent-fairy-md',
  lg: 'agent-fairy-lg',
  xl: 'agent-fairy-xl',
  float: 'agent-fairy-float',
}

export default function AgentFairy({
  state = 'idle',
  size = 'md',
  label = '财报问答助手',
  className = '',
  imageSrc = DEFAULT_IMAGE_SRC,
}: AgentFairyProps) {
  const [imageReady, setImageReady] = useState(true)

  return (
    <div
      className={`agent-fairy ${sizeClass[size]} agent-fairy-${state} ${imageReady ? 'agent-fairy-has-image' : 'agent-fairy-missing-image'} ${className}`}
      role="img"
      aria-label={label}
    >
      {imageReady && <span className="agent-fairy-aura" aria-hidden="true" />}
      {imageReady && (
        <img
          src={imageSrc}
          className="agent-fairy-image"
          alt=""
          loading="lazy"
          decoding="async"
          draggable={false}
          onError={() => setImageReady(false)}
        />
      )}
      {imageReady && (
        <>
          <span className="agent-fairy-spark agent-fairy-spark-one" aria-hidden="true" />
          <span className="agent-fairy-spark agent-fairy-spark-two" aria-hidden="true" />
          <span className="agent-fairy-spark agent-fairy-spark-three" aria-hidden="true" />
        </>
      )}
    </div>
  )
}
