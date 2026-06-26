import { useState } from 'react'

export type PetFairyState = 'idle' | 'thinking' | 'replying' | 'error'

interface PetFairyProps {
  state?: PetFairyState
  size?: 'sm' | 'md' | 'lg' | 'xl' | 'float'
  label?: string
  className?: string
  imageSrc?: string
}

const DEFAULT_IMAGE_SRC = '/pet/siq-avatar-animated.webp'

const sizeClass = {
  sm: 'pet-fairy-sm',
  md: 'pet-fairy-md',
  lg: 'pet-fairy-lg',
  xl: 'pet-fairy-xl',
  float: 'pet-fairy-float',
}

export default function PetFairy({
  state = 'idle',
  size = 'md',
  label = '财报问答助手',
  className = '',
  imageSrc = DEFAULT_IMAGE_SRC,
}: PetFairyProps) {
  const [imageReady, setImageReady] = useState(true)

  return (
    <div
      className={`pet-fairy ${sizeClass[size]} pet-fairy-${state} ${imageReady ? 'pet-fairy-has-image' : 'pet-fairy-missing-image'} ${className}`}
      role="img"
      aria-label={label}
    >
      {imageReady && <span className="pet-fairy-aura" aria-hidden="true" />}
      {imageReady && (
        <img
          src={imageSrc}
          className="pet-fairy-image"
          alt=""
          loading="lazy"
          decoding="async"
          draggable={false}
          onError={() => setImageReady(false)}
        />
      )}
      {imageReady && (
        <>
          <span className="pet-fairy-spark pet-fairy-spark-one" aria-hidden="true" />
          <span className="pet-fairy-spark pet-fairy-spark-two" aria-hidden="true" />
          <span className="pet-fairy-spark pet-fairy-spark-three" aria-hidden="true" />
        </>
      )}
    </div>
  )
}
