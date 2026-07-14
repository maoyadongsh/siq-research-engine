import { useCallback, useEffect, useRef, useState } from 'react'
import { FastForward, Rewind, Volume2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'

import { createMeetingAudioTicket } from '../api'
import { parseMeetingDate } from '../formatters'

export function MeetingAudioPlayer({ meetingId, seekToMs }: { meetingId: string; seekToMs?: number | null }) {
  const audioRef = useRef<HTMLAudioElement>(null)
  const lastRetryRef = useRef(0)
  const retryCountRef = useRef(0)
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const refreshTicketRef = useRef<(preservePlayback?: boolean) => Promise<void>>(async () => undefined)
  const restoreRef = useRef<{ time: number; resume: boolean } | null>(null)
  const mountedRef = useRef(true)
  const [audioUrl, setAudioUrl] = useState('')
  const [expiresAt, setExpiresAt] = useState('')
  const [error, setError] = useState('')
  const [playbackRate, setPlaybackRate] = useState('1')

  const refreshTicket = useCallback(async (preservePlayback = false) => {
    const audio = audioRef.current
    const restoreTime = preservePlayback ? audio?.currentTime || 0 : 0
    const resume = Boolean(preservePlayback && audio && !audio.paused)
    if (retryTimerRef.current) clearTimeout(retryTimerRef.current)
    retryTimerRef.current = null
    try {
      const ticket = await createMeetingAudioTicket(meetingId)
      if (!mountedRef.current) return
      restoreRef.current = preservePlayback ? { time: restoreTime, resume } : null
      setAudioUrl(ticket.audio_url)
      setExpiresAt(ticket.expires_at)
      setError('')
      retryCountRef.current = 0
    } catch {
      if (!mountedRef.current) return
      retryCountRef.current += 1
      setError(retryCountRef.current < 10 ? '会议录音正在生成，播放器将自动加载。' : '会议录音暂时无法加载，正在继续重试。')
      const delay = Math.min(5_000, 500 * 2 ** Math.min(retryCountRef.current - 1, 4))
      retryTimerRef.current = setTimeout(
        () => void refreshTicketRef.current(preservePlayback),
        delay,
      )
    }
  }, [meetingId])

  useEffect(() => {
    refreshTicketRef.current = refreshTicket
  }, [refreshTicket])

  const skip = useCallback((seconds: number) => {
    const audio = audioRef.current
    if (!audio) return
    const upperBound = Number.isFinite(audio.duration) ? audio.duration : Number.POSITIVE_INFINITY
    audio.currentTime = Math.min(upperBound, Math.max(0, audio.currentTime + seconds))
  }, [])

  const changePlaybackRate = useCallback((value: string) => {
    setPlaybackRate(value)
    if (audioRef.current) audioRef.current.playbackRate = Number(value)
  }, [])

  useEffect(() => {
    mountedRef.current = true
    queueMicrotask(() => void refreshTicket())
    return () => {
      mountedRef.current = false
      if (retryTimerRef.current) clearTimeout(retryTimerRef.current)
      retryTimerRef.current = null
    }
  }, [refreshTicket])

  useEffect(() => {
    if (!audioUrl || !audioRef.current) return
    audioRef.current.load()
  }, [audioUrl])

  useEffect(() => {
    if (!expiresAt) return undefined
    const refreshInMs = Math.max(30_000, (parseMeetingDate(expiresAt)?.getTime() || Date.now()) - Date.now() - 60_000)
    const timer = setTimeout(() => void refreshTicket(true), refreshInMs)
    return () => clearTimeout(timer)
  }, [expiresAt, refreshTicket])

  useEffect(() => {
    if (seekToMs == null || !audioRef.current) return
    audioRef.current.currentTime = Math.max(0, seekToMs / 1000)
    void audioRef.current.play().catch(() => undefined)
  }, [seekToMs])

  return (
    <div className="min-w-0">
      <div className="grid min-w-0 gap-2 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center">
        <div className="flex min-w-0 items-center gap-3">
          <span className="premium-icon h-10 w-10 shrink-0 rounded-md"><Volume2 className="h-5 w-5" /></span>
          <audio
            ref={audioRef}
            controls
            preload="metadata"
            src={audioUrl || undefined}
            className="h-10 min-w-0 flex-1"
            aria-label="会议录音播放器"
            onLoadedMetadata={(event) => {
              const audio = event.currentTarget
              audio.playbackRate = Number(playbackRate)
              const restore = restoreRef.current
              restoreRef.current = null
              if (restore) {
                audio.currentTime = restore.time
                if (restore.resume) void audio.play().catch(() => undefined)
              }
              setError('')
            }}
            onCanPlay={() => setError('')}
            onError={() => {
              const now = Date.now()
              if (now - lastRetryRef.current < 5_000) {
                setError('会议录音暂时无法加载，正在继续重试。')
                return
              }
              lastRetryRef.current = now
              void refreshTicket(true)
            }}
          />
        </div>
        <div className="flex items-center gap-2 sm:justify-end">
          <Button type="button" size="icon-lg" variant="secondary" onClick={() => skip(-10)} aria-label="后退 10 秒" title="后退 10 秒" disabled={!audioUrl}>
            <Rewind />
          </Button>
          <Button type="button" size="icon-lg" variant="secondary" onClick={() => skip(10)} aria-label="前进 10 秒" title="前进 10 秒" disabled={!audioUrl}>
            <FastForward />
          </Button>
          <Select value={playbackRate} onValueChange={changePlaybackRate} disabled={!audioUrl}>
            <SelectTrigger className="h-11 w-24" aria-label="播放速度"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="0.75">0.75x</SelectItem>
              <SelectItem value="1">1.0x</SelectItem>
              <SelectItem value="1.25">1.25x</SelectItem>
              <SelectItem value="1.5">1.5x</SelectItem>
              <SelectItem value="2">2.0x</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>
      {error ? <p role="alert" className="mt-2 text-xs text-error">{error}</p> : null}
    </div>
  )
}
