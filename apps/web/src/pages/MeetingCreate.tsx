import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { ArrowLeft, AudioLines, CheckCircle2, Loader2, Mic2, MonitorUp, ShieldCheck, Volume2 } from 'lucide-react'

import { EmptyState, PageHeader, PageSection, PageShell, Surface } from '@/components/page'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { useToast } from '@/hooks/useToast'
import { createMeeting, getMeetingCapabilities, getMeetingModels } from '@/features/meeting-transcription/api'
import { MeetingModelSelector } from '@/features/meeting-transcription/components/MeetingModelSelector'
import { MeetingToggle } from '@/features/meeting-transcription/components/MeetingToggle'
import { describeMeetingMicrophoneError } from '@/features/meeting-transcription/audioCapture'
import { defaultMeetingTitle } from '@/features/meeting-transcription/formatters'
import { preferredMeetingModel } from '@/features/meeting-transcription/meetingModels'
import type { MeetingCapabilities, MeetingModel } from '@/features/meeting-transcription/types'

interface AudioDevice {
  deviceId: string
  label: string
}

export default function MeetingCreate() {
  const navigate = useNavigate()
  const { toast } = useToast()
  const [capabilities, setCapabilities] = useState<MeetingCapabilities | null>(null)
  const [models, setModels] = useState<MeetingModel[]>([])
  const [title, setTitle] = useState(() => defaultMeetingTitle())
  const [language, setLanguage] = useState('zh-CN')
  const [audioSource, setAudioSource] = useState('microphone')
  const [voiceprintEnabled, setVoiceprintEnabled] = useState(false)
  const [aiEnabled, setAiEnabled] = useState(true)
  const [modelRef, setModelRef] = useState('auto')
  const [cloudConfirmed, setCloudConfirmed] = useState(false)
  const [devices, setDevices] = useState<AudioDevice[]>([])
  const [deviceId, setDeviceId] = useState('default')
  const [previewing, setPreviewing] = useState(false)
  const [inputLevel, setInputLevel] = useState(0)
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const previewStreamRef = useRef<MediaStream | null>(null)
  const previewContextRef = useRef<AudioContext | null>(null)
  const previewFrameRef = useRef(0)

  const selectedModel = useMemo(() => models.find((model) => model.model_ref === modelRef), [modelRef, models])
  const supportedLanguages = capabilities?.asr?.languages?.length ? capabilities.asr.languages : ['zh-CN']
  const cloudSelected = aiEnabled && selectedModel?.locality === 'cloud'

  useEffect(() => {
    const controller = new AbortController()
    void getMeetingCapabilities(controller.signal)
      .then(async (capabilityPayload) => {
        const modelPayload = capabilityPayload.ai?.available
          ? await getMeetingModels(controller.signal).catch(() => [])
          : []
        setCapabilities(capabilityPayload)
        setModels(modelPayload)
        const firstAvailable = preferredMeetingModel(modelPayload)
        if (!capabilityPayload.ai?.available) setAiEnabled(false)
        else if (firstAvailable) setModelRef(firstAvailable.model_ref)
      })
      .catch((loadError) => {
        if (!controller.signal.aborted) setError(loadError instanceof Error ? loadError.message : '会议能力加载失败')
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    void navigator.mediaDevices?.enumerateDevices().then((allDevices) => {
      setDevices(allDevices.filter((device) => device.kind === 'audioinput').map((device, index) => ({
        deviceId: device.deviceId || 'default',
        label: device.label || `麦克风 ${index + 1}`,
      })))
    }).catch(() => undefined)
    return () => controller.abort()
  }, [])

  useEffect(() => () => {
    cancelAnimationFrame(previewFrameRef.current)
    previewStreamRef.current?.getTracks().forEach((track) => track.stop())
    void previewContextRef.current?.close()
  }, [])

  async function startPreview() {
    setError('')
    try {
      previewStreamRef.current?.getTracks().forEach((track) => track.stop())
      if (previewContextRef.current) await previewContextRef.current.close()
      const stream = await navigator.mediaDevices.getUserMedia({ audio: {
        deviceId: deviceId !== 'default' ? { exact: deviceId } : undefined,
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
      } })
      previewStreamRef.current = stream
      const context = new AudioContext()
      previewContextRef.current = context
      const analyser = context.createAnalyser()
      analyser.fftSize = 256
      context.createMediaStreamSource(stream).connect(analyser)
      const samples = new Uint8Array(analyser.frequencyBinCount)
      const update = () => {
        analyser.getByteFrequencyData(samples)
        let total = 0
        for (const sample of samples) total += sample
        setInputLevel(Math.min(1, total / samples.length / 96))
        previewFrameRef.current = requestAnimationFrame(update)
      }
      update()
      setPreviewing(true)
      const refreshed = await navigator.mediaDevices.enumerateDevices()
      setDevices(refreshed.filter((device) => device.kind === 'audioinput').map((device, index) => ({ deviceId: device.deviceId || 'default', label: device.label || `麦克风 ${index + 1}` })))
    } catch (previewError) {
      setPreviewing(false)
      setError(describeMeetingMicrophoneError(previewError).message)
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!title.trim()) {
      setError('请输入会议标题')
      return
    }
    if (capabilities?.asr?.available === false) {
      setError('实时识别服务不可用，当前不能开始会议')
      return
    }
    if (cloudSelected && !cloudConfirmed) {
      setError('请确认云端模型的数据边界')
      return
    }
    setSubmitting(true)
    setError('')
    try {
      const session = await createMeeting({
        title: title.trim(),
        language,
        audio_source: audioSource,
        voiceprint_enabled: voiceprintEnabled,
        ai_enabled: aiEnabled,
        model_selection: aiEnabled ? {
          mode: modelRef === 'auto' ? 'auto' : 'pinned',
          model_ref: modelRef === 'auto' ? null : modelRef,
          fallback_policy: 'disabled',
          cloud_data_boundary_confirmed: cloudSelected ? cloudConfirmed : false,
        } : { mode: 'none', model_ref: null, fallback_policy: 'disabled' },
      })
      try { sessionStorage.setItem(`siq-meeting-device:${session.id}`, deviceId === 'default' ? '' : deviceId) } catch { /* Storage can be unavailable. */ }
      previewStreamRef.current?.getTracks().forEach((track) => track.stop())
      toast({ title: '会议已创建', description: '进入工作台后，点击开始即可采集麦克风。', type: 'success' })
      navigate(`/meetings/${encodeURIComponent(session.id)}/live`)
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : '创建会议失败')
    } finally {
      setSubmitting(false)
    }
  }

  if (!loading && capabilities?.enabled === false) {
    return <PageShell variant="secondary"><PageSection><EmptyState icon={Mic2} title="会议转写暂未开放" action={<Button asChild variant="secondary"><Link to="/meetings">返回会议列表</Link></Button>} /></PageSection></PageShell>
  }

  return (
    <PageShell variant="secondary" className="space-y-5">
      <PageHeader
        icon={Mic2}
        eyebrow="New Meeting"
        title="新建实时会议"
        description="先确认麦克风和数据边界，再开始持续录音与实时转写。"
        actions={<Button asChild variant="secondary"><Link to="/meetings"><ArrowLeft />返回列表</Link></Button>}
      />

      {loading ? <PageSection><div className="h-80 animate-pulse rounded-md bg-muted/60" /></PageSection> : (
        <form onSubmit={submit} className="grid gap-5 xl:grid-cols-[minmax(0,1.3fr)_minmax(320px,0.7fr)]">
          <div className="space-y-5">
            <PageSection title="会议信息" description="标题和语言可在创建前确认。">
              <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_12rem]">
                <div><label htmlFor="meeting-title" className="text-sm font-semibold text-text">会议标题</label><Input id="meeting-title" value={title} onChange={(event) => setTitle(event.target.value)} className="mt-2 h-11" autoComplete="off" /></div>
                <div><label htmlFor="meeting-language" className="text-sm font-semibold text-text">识别语言</label><Select value={language} onValueChange={setLanguage}><SelectTrigger id="meeting-language" className="mt-2 h-11 w-full"><SelectValue /></SelectTrigger><SelectContent>{supportedLanguages.map((item) => <SelectItem key={item} value={item}>{item === 'zh-CN' ? '简体中文' : item === 'en-US' ? 'English' : item}</SelectItem>)}</SelectContent></Select></div>
              </div>
            </PageSection>

            <PageSection title="音频输入" description="首期优先采集麦克风；系统音频只在后端和浏览器同时支持时开放。">
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-3" role="group" aria-label="选择音频源">
                <Button type="button" variant={audioSource === 'microphone' ? 'default' : 'secondary'} onClick={() => setAudioSource('microphone')}><Mic2 />麦克风</Button>
                <Button type="button" variant={audioSource === 'tab' ? 'default' : 'secondary'} onClick={() => setAudioSource('tab')} disabled={!capabilities?.audio_sources?.tab}><MonitorUp />标签页音频</Button>
                <Button type="button" variant={audioSource === 'system' ? 'default' : 'secondary'} onClick={() => setAudioSource('system')} disabled={!capabilities?.audio_sources?.system}><AudioLines />系统音频</Button>
              </div>
              <div className="mt-5 grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto]">
                <div><label className="text-sm font-semibold text-text" htmlFor="meeting-device">输入设备</label><Select value={deviceId} onValueChange={setDeviceId}><SelectTrigger id="meeting-device" className="mt-2 h-11 w-full"><SelectValue placeholder="默认麦克风" /></SelectTrigger><SelectContent><SelectItem value="default">系统默认麦克风</SelectItem>{devices.filter((device) => device.deviceId !== 'default').map((device) => <SelectItem key={device.deviceId} value={device.deviceId}>{device.label}</SelectItem>)}</SelectContent></Select></div>
                <Button type="button" variant="secondary" className="self-end" onClick={() => void startPreview()} disabled={audioSource !== 'microphone'}><Volume2 />{previewing ? '重新检测' : '检测麦克风'}</Button>
              </div>
              <div className="mt-4" aria-label={`麦克风音量 ${Math.round(inputLevel * 100)}%`}>
                <div className="mb-1 flex items-center justify-between text-xs text-text-muted"><span>输入电平</span><span className="tabular-nums">{previewing ? `${Math.round(inputLevel * 100)}%` : '未检测'}</span></div>
                <div className="h-2 overflow-hidden rounded-full bg-muted"><div className="h-full origin-left bg-success transition-transform duration-150" style={{ transform: `scaleX(${inputLevel})` }} /></div>
              </div>
            </PageSection>
          </div>

          <div className="space-y-5">
            <PageSection title="识别与 AI" description="声纹识别和 AI 整理均可独立关闭。">
              <div className="space-y-3">
                <MeetingToggle id="voiceprint-enabled" checked={voiceprintEnabled} onChange={setVoiceprintEnabled} disabled={!capabilities?.voiceprint?.available} label="使用已授权声纹识别" description="只匹配你已明确授权且仍有效的个人声纹。" />
                <MeetingToggle id="ai-enabled" checked={aiEnabled} onChange={setAiEnabled} disabled={!capabilities?.ai?.available} label="AI 整理" description="异步生成实时要点与会后纪要，不阻塞转写。" />
                {aiEnabled ? <MeetingModelSelector models={models} value={modelRef} onChange={(value) => { setModelRef(value); setCloudConfirmed(false) }} /> : (
                  <Surface kind="muted" padding="sm"><p className="text-sm leading-6 text-text-muted">本场仅录音和转写，不运行 Hermes AI 任务。</p></Surface>
                )}
                {cloudSelected ? (
                  <label className="flex min-h-12 cursor-pointer items-start gap-3 rounded-md border border-warning/35 bg-warning-soft/45 p-3 text-sm leading-6 text-text">
                    <input type="checkbox" checked={cloudConfirmed} onChange={(event) => setCloudConfirmed(event.target.checked)} className="mt-1" />
                    我确认会议逐字稿文本可发送至该云端模型；音频和声纹不会发送。
                  </label>
                ) : null}
              </div>
            </PageSection>

            <PageSection compact>
              <div className="flex items-start gap-3 text-sm leading-6 text-text-muted"><ShieldCheck className="mt-0.5 h-5 w-5 shrink-0 text-success" /><p>录音开始前仍会请求浏览器麦克风权限。创建草稿不会采集或上传音频。</p></div>
              {error ? <p role="alert" className="mt-3 rounded-md bg-error-soft p-3 text-sm text-error">{error}</p> : null}
              <div className="mt-4 grid gap-2 sm:grid-cols-2 xl:grid-cols-1 2xl:grid-cols-2">
                <Button asChild type="button" variant="secondary"><Link to="/meetings">取消</Link></Button>
                <Button type="submit" disabled={submitting || capabilities?.asr?.available === false}>{submitting ? <Loader2 className="animate-spin" /> : <CheckCircle2 />}{submitting ? '创建中' : '创建并进入工作台'}</Button>
              </div>
            </PageSection>
          </div>
        </form>
      )}
    </PageShell>
  )
}
