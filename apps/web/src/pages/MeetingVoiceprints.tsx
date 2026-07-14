import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  ArrowLeft,
  Fingerprint,
  Loader2,
  Pause,
  Play,
  RefreshCw,
  RotateCw,
  ShieldOff,
  Trash2,
  UserRound,
} from 'lucide-react'

import { EmptyState, PageHeader, PageSection, PageShell, StatusBadge } from '@/components/page'
import { Button } from '@/components/ui/button'
import { useToast } from '@/hooks/useToast'
import {
  deleteVoiceprint,
  listVoiceprints,
  pauseVoiceprint,
  reEnrollVoiceprint,
  resumeVoiceprint,
  revokeVoiceprintConsent,
} from '@/features/meeting-transcription/api'
import { formatMeetingDate, formatMeetingDuration } from '@/features/meeting-transcription/formatters'
import type { MeetingVoiceProfile } from '@/features/meeting-transcription/types'

function statusLabel(status: MeetingVoiceProfile['status']) {
  if (status === 'active') return '识别已启用'
  if (status === 'paused') return '已暂停'
  if (status === 'collecting') return '收集样本中'
  if (status === 'revoked') return '授权已撤销'
  return '已删除'
}

function qualityLabel(profile: MeetingVoiceProfile) {
  if (typeof profile.quality_summary === 'string') return profile.quality_summary
  if (profile.quality_summary && typeof profile.quality_summary === 'object') {
    const quality = profile.quality_summary as Record<string, unknown>
    return String(quality.label || quality.grade || quality.status || '已通过质量检查')
  }
  return profile.status === 'collecting' ? '继续收集清晰样本' : '暂无质量摘要'
}

export default function MeetingVoiceprints() {
  const { toast } = useToast()
  const [profiles, setProfiles] = useState<MeetingVoiceProfile[]>([])
  const [loading, setLoading] = useState(true)
  const [busyKey, setBusyKey] = useState('')
  const [error, setError] = useState('')

  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true)
    try {
      setProfiles(await listVoiceprints(signal))
      setError('')
    } catch (loadError) {
      if (!signal?.aborted) setError(loadError instanceof Error ? loadError.message : '声纹列表加载失败')
    } finally {
      if (!signal?.aborted) setLoading(false)
    }
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    queueMicrotask(() => {
      if (!controller.signal.aborted) void load(controller.signal)
    })
    return () => controller.abort()
  }, [load])

  const activeCount = useMemo(() => profiles.filter((profile) => profile.status === 'active').length, [profiles])

  async function run(profile: MeetingVoiceProfile, action: 'pause' | 'resume' | 're-enroll' | 'revoke') {
    if (action === 'revoke' && !window.confirm(`撤销“${profile.display_name}”的声纹授权？未来会议将不再匹配此身份。`)) return
    setBusyKey(profile.id)
    try {
      const updated = action === 'pause'
        ? await pauseVoiceprint(profile.id)
        : action === 'resume'
          ? await resumeVoiceprint(profile.id)
          : action === 're-enroll'
            ? await reEnrollVoiceprint(profile.id)
            : await revokeVoiceprintConsent(profile.id)
      setProfiles((current) => current.map((item) => item.id === updated.id ? updated : item))
      toast({ title: action === 'revoke' ? '授权已撤销' : action === 're-enroll' ? '已进入重新采集' : action === 'pause' ? '未来识别已暂停' : '未来识别已恢复', type: 'success' })
    } catch (actionError) {
      toast({ title: '操作失败', description: actionError instanceof Error ? actionError.message : '请稍后重试', type: 'error' })
    } finally {
      setBusyKey('')
    }
  }

  async function remove(profile: MeetingVoiceProfile) {
    if (!window.confirm(`永久删除“${profile.display_name}”的声纹模板？此操作不可撤销，历史逐字稿不会被删除。`)) return
    setBusyKey(profile.id)
    try {
      await deleteVoiceprint(profile.id)
      setProfiles((current) => current.filter((item) => item.id !== profile.id))
      toast({ title: '声纹已删除', type: 'success' })
    } catch (deleteError) {
      toast({ title: '删除失败', description: deleteError instanceof Error ? deleteError.message : '请稍后重试', type: 'error' })
    } finally {
      setBusyKey('')
    }
  }

  return (
    <PageShell variant="secondary" className="space-y-5">
      <PageHeader
        icon={Fingerprint}
        eyebrow="Voiceprint Privacy"
        title="声纹管理"
        description="管理你明确授权的个人声纹模板、未来识别状态和撤销操作。"
        meta={<><StatusBadge tone="info">{profiles.length} 个身份</StatusBadge><StatusBadge tone="success">{activeCount} 个已启用</StatusBadge></>}
        actions={<div className="flex gap-2"><Button asChild variant="secondary"><Link to="/meetings"><ArrowLeft />返回会议</Link></Button><Button type="button" variant="secondary" onClick={() => void load()}><RefreshCw />刷新</Button></div>}
      />

      {error ? <div role="alert" className="rounded-md bg-error-soft px-4 py-3 text-sm text-error">{error}</div> : null}

      <PageSection title="已授权身份" description="页面只展示质量和授权摘要，不返回音频切片或声纹向量。">
        {loading ? <div className="h-56 animate-pulse rounded-md bg-muted/60" /> : profiles.length === 0 ? (
          <EmptyState icon={Fingerprint} title="暂无已授权声纹" description="在会议工作台命名发言人时，可明确选择“保存声纹用于未来识别”。" action={<Button asChild><Link to="/meetings/new">开始会议</Link></Button>} />
        ) : (
          <div className="divide-y divide-border/70">
            {profiles.map((profile) => (
              <article key={profile.id} className="py-5 first:pt-0 last:pb-0">
                <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
                  <div className="flex min-w-0 gap-3">
                    <span className="premium-icon h-11 w-11 shrink-0 rounded-md"><UserRound className="h-5 w-5" /></span>
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2"><h3 className="text-base font-semibold text-text">{profile.display_name}</h3><StatusBadge tone={profile.status === 'active' ? 'success' : profile.status === 'collecting' ? 'warning' : 'neutral'}>{statusLabel(profile.status)}</StatusBadge><StatusBadge>个人私有</StatusBadge></div>
                      <p className="mt-1 break-all font-mono text-xs text-text-muted">{profile.id}</p>
                      <div className="mt-3 grid gap-x-6 gap-y-2 text-xs leading-5 text-text-muted sm:grid-cols-2 xl:grid-cols-4">
                        <span>样本：{profile.sample_count} 段 / {formatMeetingDuration(profile.effective_duration_ms)}</span>
                        <span>质量：{qualityLabel(profile)}</span>
                        <span>编码器：{[profile.encoder_name, profile.encoder_version].filter(Boolean).join(' ') || '未生成'}</span>
                        <span>创建：{formatMeetingDate(profile.created_at)}</span>
                        <span>最近匹配：{formatMeetingDate(profile.last_matched_at)}</span>
                        <span>用途：未来会议发言人识别</span>
                        <span>政策：{profile.consent?.policy_version || '未记录'}</span>
                        <span>授权：{formatMeetingDate(profile.consent?.granted_at)}</span>
                      </div>
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-2 lg:max-w-sm lg:justify-end">
                    {profile.status === 'active' ? <Button type="button" size="sm" variant="secondary" className="max-sm:h-11" onClick={() => void run(profile, 'pause')} disabled={busyKey === profile.id}><Pause />暂停识别</Button> : profile.status === 'paused' ? <Button type="button" size="sm" variant="secondary" className="max-sm:h-11" onClick={() => void run(profile, 'resume')} disabled={busyKey === profile.id}><Play />恢复识别</Button> : null}
                    {!['revoked', 'deleted'].includes(profile.status) ? <Button type="button" size="sm" variant="secondary" className="max-sm:h-11" onClick={() => void run(profile, 're-enroll')} disabled={busyKey === profile.id}><RotateCw />重新采集</Button> : null}
                    {!['revoked', 'deleted'].includes(profile.status) ? <Button type="button" size="sm" variant="outline" className="max-sm:h-11" onClick={() => void run(profile, 'revoke')} disabled={busyKey === profile.id}><ShieldOff />撤销授权</Button> : null}
                    <Button type="button" size="icon-sm" variant="ghost" className="max-sm:size-11" onClick={() => void remove(profile)} disabled={busyKey === profile.id} aria-label="删除声纹">{busyKey === profile.id ? <Loader2 className="animate-spin" /> : <Trash2 />}</Button>
                  </div>
                </div>
              </article>
            ))}
          </div>
        )}
      </PageSection>
    </PageShell>
  )
}
