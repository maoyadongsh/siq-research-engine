import { useCallback, useEffect, useRef, useState } from 'react'

export const VOICE_RECORDING_MIN_DURATION_MS = 500
export const VOICE_RECORDING_MAX_DURATION_MS = 60_000
export const VOICE_CANCEL_DISTANCE_PX = 56

export const VOICE_RECORDING_MIME_TYPES = [
  'audio/webm;codecs=opus',
  'audio/mp4',
  'audio/webm',
  'audio/ogg;codecs=opus',
] as const

export type VoiceRecorderStatus = 'idle' | 'requesting' | 'recording' | 'transcribing' | 'error'

export type VoiceRecorderErrorCode =
  | 'unsupported'
  | 'permission-denied'
  | 'device-not-found'
  | 'device-busy'
  | 'recording-failed'
  | 'too-short'
  | 'empty-recording'
  | 'transcription-failed'

export interface VoiceRecorderFailure {
  code: VoiceRecorderErrorCode
  message: string
  cause?: unknown
}

export interface VoiceRecording {
  blob: Blob
  durationMs: number
  mimeType: string
  fileExtension: string
  suggestedFilename: string
}

export interface UseVoiceRecorderOptions {
  onRecordingComplete: (recording: VoiceRecording) => void | Promise<void>
  onError?: (failure: VoiceRecorderFailure) => void
  disabled?: boolean
  minDurationMs?: number
  maxDurationMs?: number
}

export interface VoiceRecorderController {
  status: VoiceRecorderStatus
  elapsedMs: number
  maxDurationMs: number
  cancelArmed: boolean
  error: VoiceRecorderFailure | null
  disabled: boolean
  start: () => Promise<void>
  stop: () => void
  cancel: () => void
  setCancelArmed: (armed: boolean) => void
}

type FinalAction = 'send' | 'cancel'

export function selectVoiceRecorderMimeType(isTypeSupported: (mimeType: string) => boolean): string {
  return VOICE_RECORDING_MIME_TYPES.find((mimeType) => isTypeSupported(mimeType)) ?? ''
}

export function voiceRecordingExtension(mimeType: string): string {
  const normalized = mimeType.toLowerCase()
  if (normalized.includes('mp4')) return 'm4a'
  if (normalized.includes('ogg')) return 'ogg'
  if (normalized.includes('wav')) return 'wav'
  return 'webm'
}

export function formatVoiceDuration(durationMs: number): string {
  const totalSeconds = Math.max(0, Math.floor(durationMs / 1_000))
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
}

function stopMediaStream(stream: MediaStream | null) {
  stream?.getTracks().forEach((track) => track.stop())
}

function microphoneFailure(error: unknown): VoiceRecorderFailure {
  const name = error instanceof DOMException ? error.name : ''
  if (name === 'NotAllowedError' || name === 'SecurityError') {
    return { code: 'permission-denied', message: '未获得麦克风权限', cause: error }
  }
  if (name === 'NotFoundError' || name === 'DevicesNotFoundError') {
    return { code: 'device-not-found', message: '未检测到可用麦克风', cause: error }
  }
  if (name === 'NotReadableError' || name === 'TrackStartError') {
    return { code: 'device-busy', message: '麦克风正被其他应用占用', cause: error }
  }
  return { code: 'recording-failed', message: '无法开始录音，请重试', cause: error }
}

export function useVoiceRecorder({
  onRecordingComplete,
  onError,
  disabled = false,
  minDurationMs = VOICE_RECORDING_MIN_DURATION_MS,
  maxDurationMs = VOICE_RECORDING_MAX_DURATION_MS,
}: UseVoiceRecorderOptions): VoiceRecorderController {
  const safeMinDurationMs = Math.max(0, minDurationMs)
  const safeMaxDurationMs = Math.max(safeMinDurationMs, maxDurationMs)
  const [status, setStatus] = useState<VoiceRecorderStatus>('idle')
  const [elapsedMs, setElapsedMs] = useState(0)
  const [cancelArmed, setCancelArmedState] = useState(false)
  const [error, setError] = useState<VoiceRecorderFailure | null>(null)

  const mountedRef = useRef(false)
  const statusRef = useRef<VoiceRecorderStatus>('idle')
  const disabledRef = useRef(disabled)
  const completeRef = useRef(onRecordingComplete)
  const errorCallbackRef = useRef(onError)
  const recorderRef = useRef<MediaRecorder | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const chunksRef = useRef<BlobPart[]>([])
  const requestRef = useRef(0)
  const wantsRecordingRef = useRef(false)
  const startedAtRef = useRef(0)
  const finalActionRef = useRef<FinalAction>('cancel')
  const stoppingRef = useRef(false)
  const recorderErrorRef = useRef<VoiceRecorderFailure | null>(null)
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const deadlineRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const finishRef = useRef<(action: FinalAction) => void>(() => undefined)

  const updateStatus = useCallback((nextStatus: VoiceRecorderStatus) => {
    statusRef.current = nextStatus
    if (mountedRef.current) setStatus(nextStatus)
  }, [])

  const clearTimers = useCallback(() => {
    if (intervalRef.current !== null) clearInterval(intervalRef.current)
    if (deadlineRef.current !== null) clearTimeout(deadlineRef.current)
    intervalRef.current = null
    deadlineRef.current = null
  }, [])

  const reportFailure = useCallback((failure: VoiceRecorderFailure) => {
    if (!mountedRef.current) return
    setError(failure)
    setCancelArmedState(false)
    updateStatus('error')
    errorCallbackRef.current?.(failure)
  }, [updateStatus])

  const resetResources = useCallback(() => {
    clearTimers()
    stopMediaStream(streamRef.current)
    streamRef.current = null
    recorderRef.current = null
    chunksRef.current = []
    startedAtRef.current = 0
    stoppingRef.current = false
    recorderErrorRef.current = null
  }, [clearTimers])

  const finish = useCallback((action: FinalAction) => {
    wantsRecordingRef.current = false
    setCancelArmedState(false)

    if (statusRef.current === 'requesting') {
      requestRef.current += 1
      resetResources()
      setElapsedMs(0)
      updateStatus('idle')
      return
    }

    const recorder = recorderRef.current
    if (!recorder || stoppingRef.current || recorder.state === 'inactive') return

    finalActionRef.current = action
    stoppingRef.current = true
    clearTimers()
    if (action === 'send' && mountedRef.current) {
      setElapsedMs(Math.min(safeMaxDurationMs, Math.max(0, performance.now() - startedAtRef.current)))
    }
    recorder.stop()
  }, [clearTimers, resetResources, safeMaxDurationMs, updateStatus])

  const start = useCallback(async () => {
    if (disabledRef.current || statusRef.current !== 'idle' && statusRef.current !== 'error') return

    if (typeof MediaRecorder === 'undefined' || !navigator.mediaDevices?.getUserMedia) {
      reportFailure({ code: 'unsupported', message: '当前浏览器不支持语音录制' })
      return
    }

    const requestId = requestRef.current + 1
    requestRef.current = requestId
    wantsRecordingRef.current = true
    finalActionRef.current = 'cancel'
    recorderErrorRef.current = null
    chunksRef.current = []
    setError(null)
    setElapsedMs(0)
    setCancelArmedState(false)
    updateStatus('requesting')

    let stream: MediaStream
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
        video: false,
      })
    } catch (cause) {
      if (requestId !== requestRef.current || !mountedRef.current) return
      wantsRecordingRef.current = false
      reportFailure(microphoneFailure(cause))
      return
    }

    if (requestId !== requestRef.current || !wantsRecordingRef.current || !mountedRef.current || disabledRef.current) {
      stopMediaStream(stream)
      return
    }

    const selectedMimeType = selectVoiceRecorderMimeType((mimeType) => MediaRecorder.isTypeSupported(mimeType))
    let recorder: MediaRecorder
    try {
      recorder = selectedMimeType
        ? new MediaRecorder(stream, { mimeType: selectedMimeType })
        : new MediaRecorder(stream)
    } catch (cause) {
      try {
        recorder = new MediaRecorder(stream)
      } catch {
        stopMediaStream(stream)
        wantsRecordingRef.current = false
        reportFailure({ code: 'unsupported', message: '浏览器无法创建音频录制器', cause })
        return
      }
    }

    streamRef.current = stream
    recorderRef.current = recorder
    chunksRef.current = []
    stoppingRef.current = false

    recorder.ondataavailable = (event) => {
      if (event.data.size > 0) chunksRef.current.push(event.data)
    }

    recorder.onerror = (event) => {
      recorderErrorRef.current = {
        code: 'recording-failed',
        message: '录音中断，请重试',
        cause: event,
      }
      finishRef.current('cancel')
    }

    recorder.onstop = () => {
      const action = finalActionRef.current
      const recordedChunks = chunksRef.current
      const recordingFailure = recorderErrorRef.current
      const durationMs = Math.min(safeMaxDurationMs, Math.max(0, performance.now() - startedAtRef.current))
      const mimeType = recorder.mimeType || selectedMimeType || recordedChunks.find((chunk) => chunk instanceof Blob)?.type || 'audio/webm'

      resetResources()
      if (!mountedRef.current) return

      if (recordingFailure) {
        reportFailure(recordingFailure)
        return
      }
      if (action === 'cancel') {
        setElapsedMs(0)
        updateStatus('idle')
        return
      }
      if (durationMs < safeMinDurationMs) {
        reportFailure({ code: 'too-short', message: '录音时间太短，请重新输入' })
        return
      }

      const blob = new Blob(recordedChunks, { type: mimeType })
      if (blob.size === 0) {
        reportFailure({ code: 'empty-recording', message: '没有录到声音，请重试' })
        return
      }

      const fileExtension = voiceRecordingExtension(mimeType)
      updateStatus('transcribing')
      void Promise.resolve().then(() => completeRef.current({
        blob,
        durationMs,
        mimeType,
        fileExtension,
        suggestedFilename: `voice-${Date.now()}.${fileExtension}`,
      })).then(() => {
        if (!mountedRef.current) return
        setElapsedMs(0)
        updateStatus('idle')
      }).catch((cause: unknown) => {
        reportFailure({ code: 'transcription-failed', message: '语音转写失败，请重试', cause })
      })
    }

    try {
      recorder.start(250)
    } catch (cause) {
      resetResources()
      wantsRecordingRef.current = false
      reportFailure({ code: 'recording-failed', message: '无法开始录音，请重试', cause })
      return
    }

    startedAtRef.current = performance.now()
    updateStatus('recording')
    intervalRef.current = setInterval(() => {
      if (!mountedRef.current) return
      setElapsedMs(Math.min(safeMaxDurationMs, Math.max(0, performance.now() - startedAtRef.current)))
    }, 100)
    deadlineRef.current = setTimeout(() => {
      if (mountedRef.current) setElapsedMs(safeMaxDurationMs)
      finishRef.current('send')
    }, safeMaxDurationMs)
  }, [reportFailure, resetResources, safeMaxDurationMs, safeMinDurationMs, updateStatus])

  const stop = useCallback(() => finishRef.current('send'), [])
  const cancel = useCallback(() => finishRef.current('cancel'), [])
  const setCancelArmed = useCallback((armed: boolean) => {
    if (statusRef.current === 'requesting' || statusRef.current === 'recording') {
      setCancelArmedState(armed)
    }
  }, [])

  useEffect(() => {
    completeRef.current = onRecordingComplete
    errorCallbackRef.current = onError
    disabledRef.current = disabled
  }, [disabled, onError, onRecordingComplete])

  useEffect(() => {
    finishRef.current = finish
  }, [finish])

  useEffect(() => {
    mountedRef.current = true

    const cancelActiveRecording = () => {
      if (statusRef.current === 'requesting' || statusRef.current === 'recording') {
        finishRef.current('cancel')
      }
    }
    const handleVisibilityChange = () => {
      if (document.visibilityState === 'hidden') cancelActiveRecording()
    }

    window.addEventListener('blur', cancelActiveRecording)
    document.addEventListener('visibilitychange', handleVisibilityChange)
    return () => {
      mountedRef.current = false
      requestRef.current += 1
      wantsRecordingRef.current = false
      finalActionRef.current = 'cancel'
      clearTimers()
      const recorder = recorderRef.current
      if (recorder && recorder.state !== 'inactive') recorder.stop()
      stopMediaStream(streamRef.current)
      window.removeEventListener('blur', cancelActiveRecording)
      document.removeEventListener('visibilitychange', handleVisibilityChange)
    }
  }, [clearTimers])

  useEffect(() => {
    if (disabled && (statusRef.current === 'requesting' || statusRef.current === 'recording')) {
      finishRef.current('cancel')
    }
  }, [disabled])

  return {
    status,
    elapsedMs,
    maxDurationMs: safeMaxDurationMs,
    cancelArmed,
    error,
    disabled,
    start,
    stop,
    cancel,
    setCancelArmed,
  }
}
