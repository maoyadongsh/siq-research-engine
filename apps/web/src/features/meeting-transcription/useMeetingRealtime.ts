import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react'

import { listMeetingEvents } from './api'
import { createMeetingRealtimeState, meetingRealtimeReducer } from './eventReducer'
import { MeetingStreamTransport, type MeetingStreamConnectOptions } from './meetingStream'
import type { MeetingArtifact, MeetingSessionState, MeetingSpeakerTrack, MeetingTranscriptSegment } from './types'

export function useMeetingRealtime(meetingId: string) {
  const [state, dispatch] = useReducer(meetingRealtimeReducer, undefined, () => createMeetingRealtimeState())
  const [inputLevel, setInputLevel] = useState(0)
  const [streamError, setStreamError] = useState('')
  const transportRef = useRef<MeetingStreamTransport | null>(null)

  useEffect(() => {
    const transport = new MeetingStreamTransport(meetingId, {
      onEvent: (event) => dispatch({ type: 'event', event }),
      onStatus: (status) => dispatch({ type: 'connection', status }),
      onLevel: setInputLevel,
      onError: (error) => setStreamError(error.message),
      onRecovered: () => setStreamError(''),
      onInterrupted: () => dispatch({ type: 'hydrate', sessionState: 'interrupted' }),
    })
    transportRef.current = transport
    return () => {
      transportRef.current = null
      void transport.disconnect()
    }
  }, [meetingId])

  useEffect(() => {
    let hiddenAt = 0
    const markHidden = () => {
      if (!hiddenAt) hiddenAt = Date.now()
    }
    const recover = () => {
      if (document.visibilityState === 'hidden') {
        markHidden()
        return
      }
      if (!hiddenAt || Date.now() - hiddenAt < 2_000) return
      hiddenAt = 0
      void transportRef.current?.recoverAfterForeground()
    }
    document.addEventListener('visibilitychange', recover)
    window.addEventListener('pagehide', markHidden)
    window.addEventListener('pageshow', recover)
    return () => {
      document.removeEventListener('visibilitychange', recover)
      window.removeEventListener('pagehide', markHidden)
      window.removeEventListener('pageshow', recover)
    }
  }, [meetingId])

  useEffect(() => {
    if (!meetingId) return undefined
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout> | undefined
    let durableCursor = 0

    const poll = async () => {
      try {
        for (let page = 0; page < 5 && !controller.signal.aborted; page += 1) {
          const response = await listMeetingEvents(meetingId, durableCursor, controller.signal)
          for (const event of response.items) dispatch({ type: 'event', event })
          const lastCursor = response.items.at(-1)?.cursor
          if (lastCursor != null) durableCursor = Math.max(durableCursor, lastCursor)
          if (response.next_cursor == null) break
          durableCursor = Math.max(durableCursor, response.next_cursor)
        }
      } catch {
        // WebSocket remains the primary transcript path; the next poll retries AI events.
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(() => void poll(), 5_000)
      }
    }

    timer = setTimeout(() => void poll(), 1_000)
    return () => {
      controller.abort()
      if (timer) clearTimeout(timer)
    }
  }, [meetingId])

  const connect = useCallback(async (options: MeetingStreamConnectOptions = {}) => {
    setStreamError('')
    await transportRef.current?.connect(options)
  }, [])

  const prepareCapture = useCallback(async (options: MeetingStreamConnectOptions = {}) => {
    setStreamError('')
    await transportRef.current?.prepareCapture(options)
  }, [])

  const disconnect = useCallback(async () => {
    await transportRef.current?.disconnect()
  }, [])

  const pause = useCallback(async () => {
    await transportRef.current?.pause()
  }, [])

  const resume = useCallback(async () => {
    await transportRef.current?.resume()
  }, [])

  const stop = useCallback(async () => {
    return await transportRef.current?.stop() ?? false
  }, [])

  const hydrate = useCallback((payload: {
    segments?: MeetingTranscriptSegment[]
    speakers?: MeetingSpeakerTrack[]
    artifacts?: MeetingArtifact[]
    sessionState?: MeetingSessionState
  }) => {
    dispatch({ type: 'hydrate', ...payload })
  }, [])

  return useMemo(() => ({
    state,
    inputLevel,
    streamError,
    connect,
    prepareCapture,
    disconnect,
    pause,
    resume,
    stop,
    hydrate,
  }), [connect, disconnect, hydrate, inputLevel, pause, prepareCapture, resume, state, stop, streamError])
}
