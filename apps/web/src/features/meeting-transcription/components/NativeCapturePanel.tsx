import {
  CircleCheck,
  CloudUpload,
  Loader2,
  Pause,
  Play,
  RefreshCw,
  Server,
  Smartphone,
  Trash2,
  Volume2,
} from 'lucide-react'

import { Surface } from '@/components/page'
import { Button } from '@/components/ui/button'

import type { NativeMeetingCaptureState } from '../useNativeMeetingCapture'

interface NativeCapturePanelProps {
  state: NativeMeetingCaptureState
  canCleanup: boolean
  onRetryUploads(): void
  onTogglePlayback(): void
  onSeekPlayback(positionMs: number): void
  onDiscardLocal(): void
}

function captureLabel(state: NativeMeetingCaptureState) {
  if (state.cleanupReceipt) return '本地已清理'
  if (state.status?.state === 'recording') return '后台录音中'
  if (state.status?.state === 'paused') return '已暂停'
  if (state.status?.state === 'interrupted') return '系统中断'
  if (state.status?.state === 'stopping') return '正在封存'
  if (state.status?.state === 'stopped') return '本地已封存'
  if (state.status?.state === 'error') return '采集异常'
  return '准备中'
}

function ingestLabel(state: NativeMeetingCaptureState) {
  const ingest = state.operational?.ingest
  if (ingest === 'complete') return '已完整接收'
  if (ingest === 'gap') return '存在已确认缺口'
  if (ingest === 'verifying') return '服务端校验中'
  if (ingest === 'syncing') return `上传中 · ${state.status?.pendingUploadCount ?? 0} 批待传`
  if (ingest === 'pending_upload') return `等待网络 · ${state.status?.pendingUploadCount ?? 0} 批待传`
  return state.status?.state === 'recording' ? '同步等待中' : '尚未上传'
}

function serverLabel(state: NativeMeetingCaptureState) {
  const finalization = state.checkpoints?.finalization
  if (!finalization) return '等待检查点'
  if (finalization.serverPlaybackState === 'ready') return '音频已就绪'
  if (finalization.serverPlaybackState === 'failed') return '音频处理失败'
  if (finalization.serverPlaybackState === 'packaging') return '正在封装音频'
  if (finalization.ingestComplete) return '已接收，等待封装'
  if (finalization.serverPlaybackState === 'pending_upload') return '等待完整上传'
  return '等待音频封装'
}

function realtimeLabel(state: NativeMeetingCaptureState) {
  const stable = state.checkpoints?.realtime.stableOrdinal ?? 0
  const phase = state.operational?.realtime
  if (phase === 'active') return `批次同步 · ${stable} 句稳定`
  if (phase === 'waiting_for_ingest') return `等待上传 · ${stable} 句稳定`
  if (phase === 'recovering') return `字幕追赶中 · ${stable} 句稳定`
  return stable > 0 ? `${stable} 句已稳定` : '尚未同步'
}

function playbackLabel(state: NativeMeetingCaptureState) {
  if (state.serverSwitching) return '切换服务端音频'
  if (state.playback?.source === 'server') return state.playback.playing ? '服务端播放中' : '服务端音频'
  if (state.localAsset) return state.playback?.playing ? '本地播放中' : '本地音频可播'
  return '录音结束后可播'
}

function formatTime(milliseconds: number) {
  const seconds = Math.max(0, Math.floor(milliseconds / 1_000))
  const minutes = Math.floor(seconds / 60)
  return `${minutes}:${String(seconds % 60).padStart(2, '0')}`
}

function StatusItem({ icon: Icon, label, value }: {
  icon: typeof Smartphone
  label: string
  value: string
}) {
  return (
    <div className="min-w-0">
      <p className="flex items-center gap-1.5 text-text-muted"><Icon className="h-3.5 w-3.5" />{label}</p>
      <p className="mt-1 truncate font-medium text-text" title={value}>{value}</p>
    </div>
  )
}

export function NativeCapturePanel({
  state,
  canCleanup,
  onRetryUploads,
  onTogglePlayback,
  onSeekPlayback,
  onDiscardLocal,
}: NativeCapturePanelProps) {
  const durationMs = state.playback?.durationMs || state.localAsset?.durationMs || 0
  const positionMs = Math.min(state.playback?.positionMs ?? 0, durationMs)
  const hasPendingUploads = (state.status?.pendingUploadCount ?? 0) > 0
    || ['pending_upload', 'syncing', 'gap'].includes(state.operational?.ingest ?? '')

  return (
    <Surface kind="muted" padding="sm" className="space-y-3 text-xs">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <StatusItem icon={Smartphone} label="原生采集" value={captureLabel(state)} />
        <StatusItem icon={CloudUpload} label="批次上传" value={ingestLabel(state)} />
        <StatusItem icon={RefreshCw} label="字幕同步" value={realtimeLabel(state)} />
        <StatusItem icon={Server} label="服务端音频" value={serverLabel(state)} />
        <StatusItem icon={Volume2} label="录音回放" value={playbackLabel(state)} />
      </div>

      {state.localAsset || hasPendingUploads || state.cleanupReceipt ? (
        <div className="flex min-h-11 flex-wrap items-center gap-2 border-t border-border/70 pt-3">
          {state.localAsset ? (
            <>
              <Button
                type="button"
                variant="secondary"
                size="icon-sm"
                className="size-11 shrink-0"
                onClick={onTogglePlayback}
                aria-label={state.playback?.playing ? '暂停录音回放' : '播放录音'}
                title={state.playback?.playing ? '暂停录音回放' : '播放录音'}
              >
                {state.playback?.playing ? <Pause /> : <Play />}
              </Button>
              <input
                type="range"
                min={0}
                max={Math.max(1, durationMs)}
                step={1_000}
                value={positionMs}
                onChange={(event) => onSeekPlayback(Number(event.currentTarget.value))}
                className="h-11 min-w-32 flex-1 accent-primary"
                aria-label="录音播放位置"
              />
              <span className="w-[6.5rem] shrink-0 text-right font-mono tabular-nums text-text-muted">
                {formatTime(positionMs)} / {formatTime(durationMs)}
              </span>
            </>
          ) : null}
          {hasPendingUploads ? (
            <Button type="button" variant="secondary" className="min-h-11" onClick={onRetryUploads} disabled={state.busy}>
              {state.busy ? <Loader2 className="animate-spin" /> : <RefreshCw />}
              重试上传
            </Button>
          ) : null}
          {state.cleanupReceipt ? (
            <span className="ml-auto inline-flex min-h-11 items-center gap-1.5 text-success" title={state.cleanupReceipt.verifiedAt}>
              <CircleCheck className="h-4 w-4" />清理回执已验证
            </span>
          ) : canCleanup ? (
            <Button
              type="button"
              variant="secondary"
              className="ml-auto min-h-11"
              onClick={onDiscardLocal}
              disabled={state.busy}
            >
              <Trash2 />清理本地录音
            </Button>
          ) : null}
        </div>
      ) : null}
    </Surface>
  )
}
