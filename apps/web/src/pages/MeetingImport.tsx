import { useEffect, useMemo, useRef, useState, type ChangeEvent, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import {
  ArrowLeft,
  CheckCircle2,
  FileAudio,
  FileUp,
  Loader2,
  RotateCcw,
  ShieldCheck,
  X,
} from 'lucide-react'

import { EmptyState, PageHeader, PageSection, PageShell, StatusBadge, Surface } from '@/components/page'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import {
  cancelMeetingImport,
  completeMeetingImport,
  createMeetingImport,
  getMeetingImport,
  putMeetingImportChunk,
  retryMeetingImport,
  sha256Blob,
} from '@/features/meeting-transcription/meetingImportApi'
import type {
  MeetingImportStatus,
  MeetingImportStep,
} from '@/features/meeting-transcription/meetingImportTypes'
import { getMeetingCapabilities, getMeetingModels } from '@/features/meeting-transcription/api'
import { MeetingModelSelector } from '@/features/meeting-transcription/components/MeetingModelSelector'
import { MeetingToggle } from '@/features/meeting-transcription/components/MeetingToggle'
import { preferredMeetingModel } from '@/features/meeting-transcription/meetingModels'
import type { MeetingCapabilities, MeetingModel } from '@/features/meeting-transcription/types'
import { useToast } from '@/hooks/useToast'

const ACTIVE_UPLOAD_KEY = 'siq-meeting-import-active'
const DEFAULT_CHUNK_SIZE = 4 * 1024 * 1024
const BUILD_ENABLED = import.meta.env.VITE_SIQ_MEETING_IMPORT_ENABLED === '1'
const ACCEPTED_AUDIO = '.wav,.flac,.mp3,.m4a,.webm,.ogg,audio/wav,audio/flac,audio/mpeg,audio/mp4,audio/webm,audio/ogg'

const STEP_LABELS: Record<MeetingImportStep, string> = {
  uploading: '上传录音',
  verifying: '校验文件',
  probing: '读取音频',
  transcoding: '标准化音频',
  persisting: '写入会议',
  finalizing: '生成逐字稿',
  reclustering: '区分发言人',
  minutes: '整理会议纪要',
  ready: '处理完成',
  failed: '处理失败',
  cancelled: '已取消',
}

const ERROR_LABELS: Record<string, string> = {
  MEETING_IMPORT_FILE_TOO_LARGE: '录音文件超过管理员设置的大小上限',
  MEETING_IMPORT_DURATION_EXCEEDED: '录音时长超过管理员设置的上限',
  MEETING_IMPORT_FORMAT_MISMATCH: '文件内容与扩展名不匹配',
  MEETING_IMPORT_MEDIA_INVALID: '无法读取该录音文件',
  MEETING_IMPORT_TRANSCODE_FAILED: '录音解码失败',
  MEETING_FINAL_ASR_UNAVAILABLE: '语音识别服务暂时不可用',
}

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  if (value < 1024 * 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MB`
  return `${(value / 1024 / 1024 / 1024).toFixed(2)} GB`
}

function importError(status: MeetingImportStatus) {
  if (!status.public_error_code) return '导入处理失败'
  return ERROR_LABELS[status.public_error_code] || `处理失败：${status.public_error_code}`
}

function fileMatches(file: File, status: MeetingImportStatus) {
  return file.name === status.filename && file.size === status.expected_size
}

function newIdempotencyKey() {
  return globalThis.crypto?.randomUUID?.() || `meeting-import-${Date.now()}-${Math.random()}`
}

export default function MeetingImport() {
  const navigate = useNavigate()
  const { toast } = useToast()
  const inputRef = useRef<HTMLInputElement | null>(null)
  const uploadAbortRef = useRef<AbortController | null>(null)
  const navigatedRef = useRef(false)
  const [capabilities, setCapabilities] = useState<MeetingCapabilities | null>(null)
  const [models, setModels] = useState<MeetingModel[]>([])
  const [file, setFile] = useState<File | null>(null)
  const [title, setTitle] = useState('导入的会议录音')
  const [language, setLanguage] = useState('zh-CN')
  const [voiceprintEnabled, setVoiceprintEnabled] = useState(false)
  const [aiEnabled, setAiEnabled] = useState(true)
  const [modelRef, setModelRef] = useState('auto')
  const [cloudConfirmed, setCloudConfirmed] = useState(false)
  const [status, setStatus] = useState<MeetingImportStatus | null>(null)
  const [loading, setLoading] = useState(BUILD_ENABLED)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState('')

  const capability = capabilities?.recording_import
  const selectedModel = useMemo(() => models.find((model) => model.model_ref === modelRef), [modelRef, models])
  const cloudSelected = aiEnabled && selectedModel?.locality === 'cloud'
  const processing = status != null && !['uploading', 'ready', 'failed', 'cancelled'].includes(status.state)
  const progress = Math.max(0, Math.min(100, Math.round((status?.upload_progress || 0) * 100)))

  useEffect(() => {
    if (!BUILD_ENABLED) return
    const controller = new AbortController()
    void getMeetingCapabilities(controller.signal)
      .then(async (payload) => {
        setCapabilities(payload)
        const modelPayload = payload.ai?.available ? await getMeetingModels(controller.signal).catch(() => []) : []
        setModels(modelPayload)
        if (!payload.ai?.available) setAiEnabled(false)
        const firstAvailable = preferredMeetingModel(modelPayload)
        if (firstAvailable) setModelRef(firstAvailable.model_ref)
        const activeId = localStorage.getItem(ACTIVE_UPLOAD_KEY)
        if (activeId) {
          const active = await getMeetingImport(activeId, controller.signal).catch(() => null)
          if (active) setStatus(active)
          else localStorage.removeItem(ACTIVE_UPLOAD_KEY)
        }
      })
      .catch((loadError) => {
        if (!controller.signal.aborted) setError(loadError instanceof Error ? loadError.message : '导入能力加载失败')
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [])

  useEffect(() => {
    if (!status || !processing) return
    const controller = new AbortController()
    const timer = window.setTimeout(() => {
      void getMeetingImport(status.id, controller.signal)
        .then(setStatus)
        .catch((pollError) => {
          if (!controller.signal.aborted) setError(pollError instanceof Error ? pollError.message : '处理状态更新失败')
        })
    }, 1800)
    return () => {
      controller.abort()
      window.clearTimeout(timer)
    }
  }, [processing, status])

  useEffect(() => {
    if (status?.state !== 'ready' || !status.meeting_id || navigatedRef.current) return
    navigatedRef.current = true
    localStorage.removeItem(ACTIVE_UPLOAD_KEY)
    toast({ title: '录音转写完成', description: '已进入会议详情。', type: 'success' })
    navigate(`/meetings/${encodeURIComponent(status.meeting_id)}`, { replace: true })
  }, [navigate, status, toast])

  function chooseFile(event: ChangeEvent<HTMLInputElement>) {
    const selected = event.target.files?.[0] || null
    if (!selected) return
    if (status?.can_resume && !fileMatches(selected, status)) {
      setError(`请选择原文件：${status.filename}（${formatBytes(status.expected_size)}）`)
      event.target.value = ''
      return
    }
    if (capability && selected.size > capability.max_file_bytes) {
      setError(`文件不能超过 ${formatBytes(capability.max_file_bytes)}`)
      event.target.value = ''
      return
    }
    setFile(selected)
    setError('')
    if (!status && title === '导入的会议录音') {
      setTitle(selected.name.replace(/\.[^.]+$/, '').slice(0, 200) || title)
    }
  }

  async function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!file) {
      setError(status?.can_resume ? '请重新选择原文件后继续上传' : '请选择会议录音')
      return
    }
    if (!title.trim()) {
      setError('请输入会议标题')
      return
    }
    if (status?.can_resume && !fileMatches(file, status)) {
      setError('所选文件与待续传录音不一致')
      return
    }
    if (cloudSelected && !cloudConfirmed) {
      setError('请确认云端模型的数据边界')
      return
    }
    const controller = new AbortController()
    uploadAbortRef.current = controller
    setUploading(true)
    setError('')
    try {
      let current = status
      if (!current) {
        const chunkSize = Math.min(
          DEFAULT_CHUNK_SIZE,
          capability?.max_chunk_bytes || DEFAULT_CHUNK_SIZE,
        )
        current = await createMeetingImport({
          filename: file.name,
          media_type: file.type || null,
          file_size: file.size,
          chunk_size: chunkSize,
          title: title.trim(),
          language,
          voiceprint_enabled: voiceprintEnabled,
          ai_enabled: aiEnabled,
          model_selection: aiEnabled ? {
            mode: modelRef === 'auto' ? 'auto' : 'pinned',
            model_ref: modelRef === 'auto' ? null : modelRef,
            fallback_policy: 'disabled',
            cloud_data_boundary_confirmed: cloudSelected ? cloudConfirmed : false,
          } : {
            mode: 'none',
            model_ref: null,
            fallback_policy: 'disabled',
          },
        }, newIdempotencyKey())
        localStorage.setItem(ACTIVE_UPLOAD_KEY, current.id)
        setStatus(current)
      }
      for (let ordinal = current.next_ordinal; ordinal < current.total_chunks; ordinal += 1) {
        const offset = ordinal * current.chunk_size
        const chunk = file.slice(offset, Math.min(file.size, offset + current.chunk_size))
        const digest = await sha256Blob(chunk)
        const result = await putMeetingImportChunk(
          current.id,
          ordinal,
          offset,
          chunk,
          digest,
          controller.signal,
        )
        current = {
          ...current,
          received_size: result.received_size,
          received_chunks: result.received_chunks,
          next_ordinal: result.next_ordinal,
          upload_progress: result.received_size / current.expected_size,
          updated_at: new Date().toISOString(),
        }
        setStatus(current)
      }
      current = await completeMeetingImport(current.id)
      setStatus(current)
      setFile(null)
      if (inputRef.current) inputRef.current.value = ''
    } catch (uploadError) {
      if (uploadError instanceof DOMException && uploadError.name === 'AbortError') return
      setError(uploadError instanceof Error ? uploadError.message : '录音上传失败')
    } finally {
      uploadAbortRef.current = null
      setUploading(false)
    }
  }

  async function cancel() {
    if (!status || !status.can_cancel) return
    uploadAbortRef.current?.abort()
    setUploading(false)
    try {
      const cancelled = await cancelMeetingImport(status.id)
      setStatus(cancelled)
      localStorage.removeItem(ACTIVE_UPLOAD_KEY)
      setFile(null)
      toast({ title: '导入已取消', type: 'success' })
    } catch (cancelError) {
      setError(cancelError instanceof Error ? cancelError.message : '取消失败')
    }
  }

  async function retry() {
    if (!status?.retryable) return
    setError('')
    try {
      const retried = await retryMeetingImport(status.id)
      setStatus(retried)
    } catch (retryError) {
      setError(retryError instanceof Error ? retryError.message : '重试失败')
    }
  }

  function resetImport() {
    setStatus(null)
    setFile(null)
    setError('')
    navigatedRef.current = false
    localStorage.removeItem(ACTIVE_UPLOAD_KEY)
    if (inputRef.current) inputRef.current.value = ''
  }

  if (!BUILD_ENABLED || (!loading && capability?.available === false)) {
    return (
      <PageShell variant="secondary">
        <PageSection>
          <EmptyState
            icon={FileAudio}
            title="录音导入暂未开放"
            action={<Button asChild variant="secondary"><Link to="/meetings">返回会议列表</Link></Button>}
          />
        </PageSection>
      </PageShell>
    )
  }

  return (
    <PageShell variant="secondary" className="space-y-5">
      <PageHeader
        icon={FileUp}
        eyebrow="Meeting Import"
        title="导入会议录音"
        description="上传完成后生成普通会议记录，并进入同一套逐字稿、发言人与纪要流程。"
        actions={<Button asChild variant="secondary"><Link to="/meetings"><ArrowLeft />返回列表</Link></Button>}
      />

      {loading ? <PageSection><div className="h-72 animate-pulse rounded-md bg-muted/60" /></PageSection> : (
        <form onSubmit={upload} className="grid min-w-0 gap-5 xl:grid-cols-[minmax(0,1.25fr)_minmax(300px,0.75fr)]">
          <div className="min-w-0 space-y-5">
            <PageSection title="录音文件" description="支持 WAV、FLAC、MP3、M4A、WebM 和 OGG。">
              <input ref={inputRef} type="file" accept={ACCEPTED_AUDIO} className="sr-only" onChange={chooseFile} />
              <button
                type="button"
                onClick={() => inputRef.current?.click()}
                disabled={uploading || processing}
                className="flex min-h-32 w-full min-w-0 flex-col items-center justify-center gap-3 rounded-md border border-dashed border-border bg-muted/25 px-4 py-6 text-center transition-colors duration-200 hover:border-primary/60 hover:bg-primary-soft/25 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary disabled:cursor-not-allowed disabled:opacity-60"
              >
                <FileAudio className="h-8 w-8 text-primary" />
                <span className="max-w-full break-all text-sm font-semibold text-text">
                  {file?.name || (status?.can_resume ? `重新选择 ${status.filename}` : '选择会议录音')}
                </span>
                <span className="text-xs text-text-muted">
                  {file ? formatBytes(file.size) : capability ? `单文件上限 ${formatBytes(capability.max_file_bytes)}` : '请选择音频文件'}
                </span>
              </button>
              {status?.can_resume && !file ? (
                <p className="mt-3 text-sm leading-6 text-warning">已恢复到第 {status.next_ordinal + 1} 个分片，选择同一文件后继续。</p>
              ) : null}
            </PageSection>

            <PageSection title="会议信息">
              <div className="grid min-w-0 gap-4 sm:grid-cols-[minmax(0,1fr)_12rem]">
                <div className="min-w-0">
                  <label htmlFor="import-title" className="text-sm font-semibold text-text">会议标题</label>
                  <Input id="import-title" value={title} onChange={(event) => setTitle(event.target.value)} disabled={status != null} className="mt-2 h-11" />
                </div>
                <div className="min-w-0">
                  <label htmlFor="import-language" className="text-sm font-semibold text-text">识别语言</label>
                  <Select value={language} onValueChange={setLanguage} disabled={status != null}>
                    <SelectTrigger id="import-language" className="mt-2 h-11 w-full"><SelectValue /></SelectTrigger>
                    <SelectContent><SelectItem value="zh-CN">简体中文</SelectItem></SelectContent>
                  </Select>
                </div>
              </div>
            </PageSection>

            {status ? (
              <PageSection
                title="导入进度"
                actions={<StatusBadge tone={status.state === 'failed' ? 'error' : status.state === 'ready' ? 'success' : 'info'}>{STEP_LABELS[status.step]}</StatusBadge>}
              >
                <div className="min-w-0">
                  <div className="mb-2 flex items-center justify-between gap-3 text-sm">
                    <span className="min-w-0 truncate text-text-muted">{status.filename}</span>
                    <span className="shrink-0 font-mono tabular-nums text-text">{progress}%</span>
                  </div>
                  <div className="h-2.5 overflow-hidden rounded-full bg-muted" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress}>
                    <div className="h-full bg-primary transition-[width] duration-200" style={{ width: `${progress}%` }} />
                  </div>
                  <div className="mt-3 flex flex-wrap gap-x-4 gap-y-2 text-xs text-text-muted">
                    <span>{formatBytes(status.received_size)} / {formatBytes(status.expected_size)}</span>
                    <span>{status.received_chunks} / {status.total_chunks} 个分片</span>
                    {status.detected_duration_ms != null ? <span>{Math.ceil(status.detected_duration_ms / 60_000)} 分钟</span> : null}
                  </div>
                  {status.state === 'failed' ? (
                    <div role="alert" className="mt-4 rounded-md bg-error-soft p-3 text-sm leading-6 text-error">
                      <p>{importError(status)}</p>
                      {status.public_error_code ? <p className="mt-1 font-mono text-xs">{status.public_error_code}</p> : null}
                    </div>
                  ) : null}
                </div>
              </PageSection>
            ) : null}
          </div>

          <div className="min-w-0 space-y-5">
            <PageSection title="识别与 AI" description="模型只处理稳定逐字稿，音频仍由语音识别服务处理。">
              <div className="space-y-3">
                <MeetingToggle id="import-voiceprint" checked={voiceprintEnabled} onChange={setVoiceprintEnabled} disabled={status != null || !capabilities?.voiceprint?.available} label="使用已授权声纹识别" description="匹配已授权且仍有效的个人声纹。" />
                <MeetingToggle id="import-ai" checked={aiEnabled} onChange={setAiEnabled} disabled={status != null || !capabilities?.ai?.available} label="AI 整理" description="生成会议纪要、观点与行动项。" />
                {aiEnabled ? <MeetingModelSelector models={models} value={modelRef} onChange={(value) => { setModelRef(value); setCloudConfirmed(false) }} disabled={status != null} /> : (
                  <Surface kind="muted" padding="sm"><p className="text-sm text-text-muted">仅生成逐字稿与发言人区分。</p></Surface>
                )}
                {cloudSelected ? (
                  <label className="flex min-h-12 cursor-pointer items-start gap-3 rounded-md border border-warning/35 bg-warning-soft/45 p-3 text-sm leading-6 text-text">
                    <input type="checkbox" checked={cloudConfirmed} onChange={(event) => setCloudConfirmed(event.target.checked)} disabled={status != null} className="mt-1" />
                    我确认逐字稿文本可发送至该云端模型；音频和声纹不会发送。
                  </label>
                ) : null}
              </div>
            </PageSection>

            <PageSection compact>
              <div className="flex items-start gap-3 text-sm leading-6 text-text-muted"><ShieldCheck className="mt-0.5 h-5 w-5 shrink-0 text-success" /><p>分片逐块校验，上传中断后可从已确认位置继续。</p></div>
              {error ? <p role="alert" className="mt-3 break-words rounded-md bg-error-soft p-3 text-sm leading-6 text-error">{error}</p> : null}
              <div className="mt-4 grid min-w-0 gap-2 sm:grid-cols-2 xl:grid-cols-1 2xl:grid-cols-2">
                {status?.state === 'cancelled' ? (
                  <Button type="button" variant="secondary" className="sm:col-span-2 xl:col-span-1 2xl:col-span-2" onClick={resetImport}><RotateCcw />导入另一份录音</Button>
                ) : status?.state === 'failed' && status.retryable ? (
                  <Button type="button" onClick={() => void retry()}><RotateCcw />重试处理</Button>
                ) : (
                  <Button type="submit" disabled={!file || uploading || processing || status?.state === 'ready'}>
                    {uploading || processing ? <Loader2 className="animate-spin" /> : status?.can_resume ? <RotateCcw /> : <FileUp />}
                    {uploading ? '上传中' : processing ? STEP_LABELS[status?.step || 'verifying'] : status?.can_resume ? '继续上传' : '开始导入'}
                  </Button>
                )}
                {status?.can_cancel ? (
                  <Button type="button" variant="secondary" onClick={() => void cancel()}><X />取消导入</Button>
                ) : status?.state === 'failed' ? (
                  <Button type="button" variant="secondary" onClick={resetImport}><FileUp />导入其他录音</Button>
                ) : status?.state === 'ready' && status.meeting_id ? (
                  <Button asChild><Link to={`/meetings/${encodeURIComponent(status.meeting_id)}`}><CheckCircle2 />打开会议</Link></Button>
                ) : (
                  <Button asChild type="button" variant="secondary"><Link to="/meetings">返回列表</Link></Button>
                )}
              </div>
            </PageSection>
          </div>
        </form>
      )}
    </PageShell>
  )
}
