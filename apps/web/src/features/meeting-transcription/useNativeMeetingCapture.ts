import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { accessToken, resolveSiqApiUrl } from '@/shared/api/client'

import { createMeetingAudioTicket } from './api'
import type { MeetingCaptureCapabilityEnvelope, MeetingCaptureSelectionReason } from './captureAdapter'
import type {
  MeetingCaptureCheckpoints,
  MeetingCaptureListenerHandle,
  MeetingCaptureStatus,
  MeetingLocalPlaybackAsset,
  MeetingNativeCaptureCleanupReceipt,
  MeetingNativePlaybackStatus,
} from './nativeCapture'
import { loadNativeCaptureRuntime } from './nativeCaptureRuntime'
import {
  deriveNativeCaptureOperationalState,
  nativePlaybackToggleAction,
  recoverNativeCaptureAfterForeground,
  retainNativeRecoveryRequest,
  type NativeCaptureOperationalState,
} from './nativeCaptureState'
import {
  getOrCreateNativeCaptureIdentity,
  nativeCleanupReady,
  nativeMeetingApiBaseUrl,
  startNativeCaptureSession,
  validateNativeCleanupReceipt,
  type NativeCaptureAdapterWithReceipt,
} from './nativeCaptureSession'

export type NativeMeetingCaptureMode = 'unresolved' | 'web' | 'native'

export interface NativeMeetingCaptureState {
  mode: NativeMeetingCaptureMode
  bound: boolean
  selectionReason: MeetingCaptureSelectionReason | null
  status: MeetingCaptureStatus | null
  checkpoints: MeetingCaptureCheckpoints | null
  operational: NativeCaptureOperationalState | null
  localAsset: MeetingLocalPlaybackAsset | null
  playback: MeetingNativePlaybackStatus | null
  serverSwitching: boolean
  busy: boolean
  online: boolean
  error: string
  cleanupReceipt: MeetingNativeCaptureCleanupReceipt | null
}

const INITIAL_STATE: NativeMeetingCaptureState = {
  mode: 'unresolved',
  bound: false,
  selectionReason: null,
  status: null,
  checkpoints: null,
  operational: null,
  localAsset: null,
  playback: null,
  serverSwitching: false,
  busy: false,
  online: typeof navigator === 'undefined' ? true : navigator.onLine,
  error: '',
  cleanupReceipt: null,
}

function errorMessage(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback
}

function playbackChanged(
  current: MeetingNativePlaybackStatus | null,
  next: MeetingNativePlaybackStatus,
) {
  if (!current) return true
  return current.handle !== next.handle
    || current.source !== next.source
    || current.playing !== next.playing
    || current.durationMs !== next.durationMs
    || current.serverReady !== next.serverReady
    || Math.floor(current.positionMs / 1_000) !== Math.floor(next.positionMs / 1_000)
}

function absolutePlaybackUrl(value: string) {
  const url = new URL(resolveSiqApiUrl(value))
  const apiOrigin = new URL(resolveSiqApiUrl('/api')).origin
  if (url.origin !== apiOrigin || url.protocol !== 'https:') {
    throw new Error('服务端回放地址必须使用当前 HTTPS 会议服务。')
  }
  return url.toString()
}

export function useNativeMeetingCapture(meetingId: string) {
  const [state, setState] = useState<NativeMeetingCaptureState>(INITIAL_STATE)
  const stateRef = useRef(state)
  const adapterRef = useRef<NativeCaptureAdapterWithReceipt | null>(null)
  const selectionPendingRef = useRef<Promise<boolean> | null>(null)
  const listenersRef = useRef<MeetingCaptureListenerHandle[]>([])
  const switchPendingRef = useRef(false)
  const foregroundRecoveryPendingRef = useRef(false)
  const foregroundRecoveryRequestedRef = useRef(false)
  const lastSwitchFailureRef = useRef(0)

  const updateState = useCallback((update: (current: NativeMeetingCaptureState) => NativeMeetingCaptureState) => {
    setState((current) => {
      const next = update(current)
      stateRef.current = next
      return next
    })
  }, [])

  useEffect(() => {
    stateRef.current = state
  }, [state])

  const select = useCallback((capabilities: MeetingCaptureCapabilityEnvelope | null): Promise<boolean> => {
    if (adapterRef.current) return Promise.resolve(true)
    if (stateRef.current.mode === 'web') return Promise.resolve(false)
    if (selectionPendingRef.current) return selectionPendingRef.current
    const pending = (async () => {
      try {
        const selection = await loadNativeCaptureRuntime(capabilities)
        if (!selection.adapter) {
          updateState((current) => ({
            ...current,
            mode: 'web',
            selectionReason: selection.reason,
            error: '',
          }))
          return false
        }
        const adapter = selection.adapter as NativeCaptureAdapterWithReceipt
        adapterRef.current = adapter
        const recovered = await adapter.recoverPendingCaptures().catch(() => [])
        const recoveredStatus = recovered.find((candidate) => candidate.meetingId === meetingId) ?? null
        updateState((current) => ({
          ...current,
          mode: 'native',
          bound: false,
          selectionReason: selection.reason,
          status: recoveredStatus,
          error: '',
        }))
        return true
      } catch {
        updateState((current) => ({
          ...current,
          mode: 'web',
          selectionReason: 'native_plugin_unavailable',
          error: '',
        }))
        return false
      }
    })()
    selectionPendingRef.current = pending
    void pending.finally(() => {
      if (selectionPendingRef.current === pending) selectionPendingRef.current = null
    })
    return pending
  }, [meetingId, updateState])

  const refresh = useCallback(async () => {
    const adapter = adapterRef.current
    if (!adapter) return
    if (!stateRef.current.bound) {
      const recovered = await adapter.recoverPendingCaptures().catch(() => [])
      const recoveredStatus = recovered.find((candidate) => candidate.meetingId === meetingId) ?? null
      updateState((current) => ({ ...current, status: recoveredStatus ?? current.status }))
      return
    }
    try {
      const [status, checkpoints, localAsset] = await Promise.all([
        adapter.getStatus(),
        adapter.getCheckpoints(),
        adapter.getLocalPlaybackAsset(),
      ])
      updateState((current) => ({
        ...current,
        status,
        checkpoints,
        localAsset: localAsset ?? current.localAsset,
      }))
    } catch (error) {
      updateState((current) => ({
        ...current,
        error: errorMessage(error, '原生采集状态读取失败'),
      }))
    }
  }, [meetingId, updateState])

  const start = useCallback(async (streamEpoch: number) => {
    const adapter = adapterRef.current
    if (!adapter) throw new Error('iOS 原生采集适配器尚未选择。')
    updateState((current) => ({ ...current, busy: true, error: '' }))
    try {
      const identity = getOrCreateNativeCaptureIdentity(meetingId)
      const result = await startNativeCaptureSession({
        adapter,
        meetingId,
        streamEpoch,
        apiBaseUrl: nativeMeetingApiBaseUrl(resolveSiqApiUrl('/api/meetings/v1')),
        identity,
        userBearerToken: accessToken(),
        expectedCaptureId: stateRef.current.bound ? null : stateRef.current.status?.captureId,
      })
      updateState((current) => ({ ...current, bound: true, status: result.status, busy: false }))
      await refresh()
      return result.status
    } catch (error) {
      updateState((current) => ({
        ...current,
        busy: false,
        error: errorMessage(error, 'iOS 原生采集启动失败'),
      }))
      throw error
    }
  }, [meetingId, refresh, updateState])

  const pause = useCallback(async () => {
    const adapter = adapterRef.current
    if (!adapter) throw new Error('iOS 原生采集尚未启动。')
    updateState((current) => ({ ...current, busy: true, error: '' }))
    try {
      const status = await adapter.pause('user')
      updateState((current) => ({ ...current, status, busy: false }))
      return status
    } catch (error) {
      updateState((current) => ({ ...current, busy: false, error: errorMessage(error, '暂停原生采集失败') }))
      throw error
    }
  }, [updateState])

  const resume = useCallback(async () => {
    const adapter = adapterRef.current
    if (!adapter) throw new Error('iOS 原生采集尚未启动。')
    updateState((current) => ({ ...current, busy: true, error: '' }))
    try {
      const status = await adapter.resume()
      updateState((current) => ({ ...current, status, busy: false }))
      return status
    } catch (error) {
      updateState((current) => ({ ...current, busy: false, error: errorMessage(error, '恢复原生采集失败') }))
      throw error
    }
  }, [updateState])

  const stop = useCallback(async () => {
    const adapter = adapterRef.current
    if (!adapter) throw new Error('iOS 原生采集尚未启动。')
    updateState((current) => ({ ...current, busy: true, error: '' }))
    try {
      const stopped = await adapter.stop()
      updateState((current) => ({
        ...current,
        status: stopped.status,
        localAsset: stopped.playbackAsset ?? current.localAsset,
        busy: false,
      }))
      await refresh()
      return stopped
    } catch (error) {
      updateState((current) => ({ ...current, busy: false, error: errorMessage(error, '封存原生录音失败') }))
      throw error
    }
  }, [refresh, updateState])

  const retryUploads = useCallback(async () => {
    const adapter = adapterRef.current
    if (!adapter) return
    updateState((current) => ({ ...current, busy: true, error: '' }))
    try {
      const status = await adapter.retryPendingUploads()
      updateState((current) => ({ ...current, status, busy: false }))
      await refresh()
    } catch (error) {
      updateState((current) => ({ ...current, busy: false, error: errorMessage(error, '重试上传失败') }))
    }
  }, [refresh, updateState])

  const recoverAfterForeground = useCallback(async () => {
    const adapter = adapterRef.current
    if (
      !adapter
      || !stateRef.current.bound
      || stateRef.current.mode !== 'native'
      || !foregroundRecoveryRequestedRef.current
      || foregroundRecoveryPendingRef.current
    ) return
    foregroundRecoveryPendingRef.current = true
    try {
      const result = await recoverNativeCaptureAfterForeground({
        getStatus: () => adapter.getStatus(),
        getCheckpoints: () => adapter.getCheckpoints(),
        retryPendingUploads: () => adapter.retryPendingUploads(),
        rollover: async () => {
          const rollover = await adapter.rollover()
          return { streamEpoch: rollover.streamEpoch }
        },
      })
      updateState((current) => ({
        ...current,
        status: result.status,
        checkpoints: result.checkpoints,
        error: '',
      }))
      foregroundRecoveryRequestedRef.current = retainNativeRecoveryRequest(result.outcome)
      if (result.outcome === 'rolled_over') await refresh()
    } catch (error) {
      foregroundRecoveryRequestedRef.current = true
      updateState((current) => ({
        ...current,
        error: errorMessage(error, '原生采集前台恢复失败，录音与待传批次已保留。'),
      }))
    } finally {
      foregroundRecoveryPendingRef.current = false
    }
  }, [refresh, updateState])

  const togglePlayback = useCallback(async () => {
    const adapter = adapterRef.current
    const asset = stateRef.current.localAsset
    if (!adapter || !asset) return
    try {
      const action = nativePlaybackToggleAction(stateRef.current.playback)
      let playback: MeetingNativePlaybackStatus
      if (action === 'pause') playback = await adapter.pausePlayback()
      else if (action === 'resume') playback = await adapter.resumePlayback()
      else playback = await adapter.playLocalPlayback(asset.handle)
      updateState((current) => ({ ...current, playback, error: '' }))
    } catch (error) {
      updateState((current) => ({ ...current, error: errorMessage(error, '录音播放失败') }))
    }
  }, [updateState])

  const seekPlayback = useCallback(async (positionMs: number) => {
    const adapter = adapterRef.current
    if (!adapter || !Number.isFinite(positionMs)) return
    try {
      const playback = await adapter.seekPlayback(Math.max(0, Math.round(positionMs)))
      updateState((current) => ({ ...current, playback, error: '' }))
    } catch (error) {
      updateState((current) => ({ ...current, error: errorMessage(error, '录音定位失败') }))
    }
  }, [updateState])

  const discardLocal = useCallback(async () => {
    const adapter = adapterRef.current
    const current = stateRef.current
    if (!adapter || !current.status || !nativeCleanupReady(current.status, current.checkpoints)) return null
    updateState((value) => ({ ...value, busy: true, error: '' }))
    try {
      const received = await adapter.discardLocalCaptureWithReceipt(true)
      const receipt = validateNativeCleanupReceipt(received, current.status, current.checkpoints)
      updateState((value) => ({
        ...value,
        busy: false,
        cleanupReceipt: receipt,
        localAsset: null,
        playback: null,
      }))
      return receipt
    } catch (error) {
      updateState((value) => ({ ...value, busy: false, error: errorMessage(error, '本地录音清理失败') }))
      return null
    }
  }, [updateState])

  useEffect(() => {
    if (state.mode !== 'native' || !adapterRef.current) return undefined
    let disposed = false
    const register = async () => {
      const adapter = adapterRef.current
      if (!adapter) return
      const handles = await Promise.all([
        adapter.addListener('capture.started', (status) => updateState((current) => ({ ...current, status }))),
        adapter.addListener('capture.resumed', (status) => updateState((current) => ({ ...current, status }))),
        adapter.addListener('capture.stopped', (status) => updateState((current) => ({ ...current, status }))),
        adapter.addListener('capture.progress', (progress) => updateState((current) => current.status ? ({
          ...current,
          status: {
            ...current.status,
            recordedThroughSample: progress.recordedThroughSample,
            manifestRevision: progress.manifestRevision,
            pendingUploadCount: progress.pendingUploadCount,
          },
        }) : current)),
        adapter.addListener('capture.checkpoint', (checkpoints) => updateState((current) => ({ ...current, checkpoints }))),
        adapter.addListener('local.playback.ready', (localAsset) => updateState((current) => ({ ...current, localAsset }))),
        adapter.addListener('capture.error', (event) => updateState((current) => ({ ...current, error: `原生采集异常：${event.code}` }))),
      ])
      if (disposed) await Promise.all(handles.map((handle) => handle.remove()))
      else listenersRef.current = handles
    }
    void register().catch((error) => updateState((current) => ({
      ...current,
      error: errorMessage(error, '原生采集事件监听失败'),
    })))
    return () => {
      disposed = true
      const handles = listenersRef.current
      listenersRef.current = []
      void Promise.all(handles.map((handle) => handle.remove()))
    }
  }, [state.mode, updateState])

  useEffect(() => {
    if (state.mode !== 'native') return undefined
    const poll = async () => {
      await refresh()
      if (
        foregroundRecoveryRequestedRef.current
        && document.visibilityState === 'visible'
        && stateRef.current.online
      ) await recoverAfterForeground()
    }
    void poll()
    const interval = window.setInterval(() => void poll(), 2_000)
    return () => window.clearInterval(interval)
  }, [recoverAfterForeground, refresh, state.mode])

  useEffect(() => {
    const online = () => {
      updateState((current) => ({ ...current, online: true }))
      void recoverAfterForeground()
    }
    const offline = () => updateState((current) => ({ ...current, online: false }))
    window.addEventListener('online', online)
    window.addEventListener('offline', offline)
    return () => {
      window.removeEventListener('online', online)
      window.removeEventListener('offline', offline)
    }
  }, [recoverAfterForeground, updateState])

  useEffect(() => {
    const onVisibilityChange = () => {
      if (document.visibilityState === 'hidden') {
        if (stateRef.current.mode === 'native' && stateRef.current.bound) {
          foregroundRecoveryRequestedRef.current = true
        }
        return
      }
      void recoverAfterForeground()
    }
    document.addEventListener('visibilitychange', onVisibilityChange)
    return () => document.removeEventListener('visibilitychange', onVisibilityChange)
  }, [recoverAfterForeground])

  useEffect(() => {
    if (!state.playback?.playing || !adapterRef.current) return undefined
    const interval = window.setInterval(() => {
      const adapter = adapterRef.current
      if (!adapter) return
      void adapter.getPlaybackStatus().then((playback) => {
        updateState((current) => playbackChanged(current.playback, playback)
          ? { ...current, playback }
          : current)
      }).catch(() => undefined)
    }, 1_000)
    return () => window.clearInterval(interval)
  }, [state.playback?.playing, updateState])

  useEffect(() => {
    const adapter = adapterRef.current
    const checkpoints = state.checkpoints
    const asset = state.localAsset
    if (
      !adapter
      || checkpoints?.finalization.serverPlaybackState !== 'ready'
      || !asset
      || state.playback?.source === 'server'
      || state.serverSwitching
      || switchPendingRef.current
      || Date.now() - lastSwitchFailureRef.current < 15_000
    ) return
    switchPendingRef.current = true
    updateState((current) => ({ ...current, serverSwitching: true }))
    void createMeetingAudioTicket(meetingId)
      .then((ticket) => adapter.switchToServerPlayback(asset.handle, absolutePlaybackUrl(ticket.audio_url)))
      .then((playback) => {
        lastSwitchFailureRef.current = 0
        updateState((current) => ({ ...current, playback, serverSwitching: false, error: '' }))
      })
      .catch((error) => {
        lastSwitchFailureRef.current = Date.now()
        updateState((current) => ({
          ...current,
          serverSwitching: false,
          error: errorMessage(error, '服务端回放切换失败，本地回放仍可使用。'),
        }))
      })
      .finally(() => { switchPendingRef.current = false })
  }, [meetingId, state.checkpoints, state.localAsset, state.playback?.source, state.serverSwitching, updateState])

  const operational = useMemo(() => {
    if (!state.status || !state.checkpoints) return null
    return deriveNativeCaptureOperationalState({
      status: state.status,
      checkpoints: state.checkpoints,
      online: state.online,
    })
  }, [state.checkpoints, state.online, state.status])

  return {
    state: { ...state, operational },
    select,
    start,
    pause,
    resume,
    stop,
    refresh,
    retryUploads,
    recoverAfterForeground,
    togglePlayback,
    seekPlayback,
    discardLocal,
    canCleanup: nativeCleanupReady(state.status, state.checkpoints),
  }
}
