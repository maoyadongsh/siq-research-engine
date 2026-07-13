import { useEffect, useId, useRef } from 'react'
import type { KeyboardEvent, PointerEvent } from 'react'
import { LoaderCircle, Mic, Square } from 'lucide-react'
import {
  VOICE_CANCEL_DISTANCE_PX,
  formatVoiceDuration,
  type VoiceRecorderController,
} from './useVoiceRecorder'

interface VoiceInputButtonProps {
  recorder: VoiceRecorderController
  iconClassName?: string
  disabledReason?: string
}

function isRecordKey(key: string) {
  return key === ' ' || key === 'Enter'
}

export default function VoiceInputButton({ recorder, iconClassName, disabledReason }: VoiceInputButtonProps) {
  const statusId = useId()
  const pointerIdRef = useRef<number | null>(null)
  const pointerStartYRef = useRef(0)
  const pointerCancelRef = useRef(false)
  const keyboardActiveRef = useRef(false)
  const buttonRef = useRef<HTMLButtonElement | null>(null)

  const isPressing = recorder.status === 'requesting' || recorder.status === 'recording'
  const isTranscribing = recorder.status === 'transcribing'
  const showsStatus = isPressing || isTranscribing || recorder.status === 'error'

  const clearPointer = () => {
    const pointerId = pointerIdRef.current
    if (pointerId !== null && buttonRef.current?.hasPointerCapture(pointerId)) {
      buttonRef.current.releasePointerCapture(pointerId)
    }
    pointerIdRef.current = null
    pointerCancelRef.current = false
  }

  useEffect(() => {
    if (recorder.status === 'transcribing' || recorder.status === 'error') {
      clearPointer()
      keyboardActiveRef.current = false
    }
  }, [recorder.status])

  const handlePointerDown = (event: PointerEvent<HTMLButtonElement>) => {
    if (event.button !== 0 || !event.isPrimary || recorder.disabled || isTranscribing) return
    event.preventDefault()
    pointerIdRef.current = event.pointerId
    pointerStartYRef.current = event.clientY
    pointerCancelRef.current = false
    event.currentTarget.setPointerCapture(event.pointerId)
    void recorder.start()
  }

  const handlePointerMove = (event: PointerEvent<HTMLButtonElement>) => {
    if (pointerIdRef.current !== event.pointerId) return
    const shouldCancel = pointerStartYRef.current - event.clientY >= VOICE_CANCEL_DISTANCE_PX
    pointerCancelRef.current = shouldCancel
    recorder.setCancelArmed(shouldCancel)
  }

  const handlePointerUp = (event: PointerEvent<HTMLButtonElement>) => {
    if (pointerIdRef.current !== event.pointerId) return
    event.preventDefault()
    if (pointerCancelRef.current) recorder.cancel()
    else recorder.stop()
    clearPointer()
  }

  const handlePointerCancel = (event: PointerEvent<HTMLButtonElement>) => {
    if (pointerIdRef.current !== event.pointerId) return
    recorder.cancel()
    clearPointer()
  }

  const handleKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (event.key === 'Escape' && (keyboardActiveRef.current || isPressing)) {
      event.preventDefault()
      keyboardActiveRef.current = false
      recorder.cancel()
      return
    }
    if (!isRecordKey(event.key) || event.repeat || recorder.disabled || isTranscribing) return
    event.preventDefault()
    keyboardActiveRef.current = true
    void recorder.start()
  }

  const handleKeyUp = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (!isRecordKey(event.key) || !keyboardActiveRef.current) return
    event.preventDefault()
    keyboardActiveRef.current = false
    recorder.stop()
  }

  const handleBlur = () => {
    if (pointerIdRef.current !== null || keyboardActiveRef.current || isPressing) {
      keyboardActiveRef.current = false
      recorder.cancel()
      clearPointer()
    }
  }

  let statusText = ''
  if (recorder.status === 'requesting') statusText = '正在请求麦克风权限'
  if (recorder.status === 'recording') {
    statusText = recorder.cancelArmed
      ? '松开取消'
      : `${formatVoiceDuration(recorder.elapsedMs)} / ${formatVoiceDuration(recorder.maxDurationMs)} · 上滑取消`
  }
  if (recorder.status === 'transcribing') statusText = '正在转写语音'
  if (recorder.status === 'error') statusText = recorder.error?.message ?? '语音输入失败，请重试'

  const ariaLabel = isPressing
    ? recorder.cancelArmed ? '松开取消录音' : '松开发送语音'
    : isTranscribing ? '正在转写语音' : '按住说话'
  const buttonLabel = recorder.disabled && disabledReason ? disabledReason : ariaLabel

  return (
    <span className="chat-voice-input">
      <button
        ref={buttonRef}
        type="button"
        className={`chat-composer-tool chat-voice-button ${isPressing ? 'is-recording' : ''} ${isTranscribing ? 'is-transcribing' : ''}`.trim()}
        disabled={recorder.disabled || isTranscribing}
        aria-label={buttonLabel}
        aria-pressed={isPressing}
        aria-describedby={showsStatus ? statusId : undefined}
        title={buttonLabel}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerCancel={handlePointerCancel}
        onKeyDown={handleKeyDown}
        onKeyUp={handleKeyUp}
        onBlur={handleBlur}
        onContextMenu={(event) => event.preventDefault()}
      >
        {isTranscribing || recorder.status === 'requesting' ? (
          <LoaderCircle className={`${iconClassName ?? ''} chat-voice-spinner`.trim()} />
        ) : isPressing ? (
          <Square className={iconClassName} />
        ) : (
          <Mic className={iconClassName} />
        )}
      </button>
      {showsStatus && (
        <span
          id={statusId}
          className={`chat-voice-status ${recorder.cancelArmed ? 'is-canceling' : ''} ${recorder.status === 'error' ? 'is-error' : ''}`.trim()}
          role={recorder.status === 'error' ? 'alert' : 'status'}
          aria-live={recorder.status === 'error' ? 'assertive' : 'polite'}
        >
          {recorder.status === 'recording' && !recorder.cancelArmed && <span className="chat-voice-live-dot" aria-hidden="true" />}
          {isTranscribing && <LoaderCircle className="chat-voice-status-spinner" aria-hidden="true" />}
          <span>{statusText}</span>
        </span>
      )}
    </span>
  )
}
