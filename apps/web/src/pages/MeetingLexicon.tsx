import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import {
  ArrowLeft,
  Check,
  Clock3,
  History,
  Languages,
  Loader2,
  Plus,
  RotateCcw,
  SearchCheck,
  Trash2,
  X,
} from 'lucide-react'

import { EmptyState, PageHeader, PageSection, PageShell, StatusBadge } from '@/components/page'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useToast } from '@/hooks/useToast'
import {
  activateLexiconVersion,
  confirmTermCandidate,
  createLexiconEntry,
  deleteLexiconEntry,
  getMeetingCapabilities,
  getMeetingLexicon,
  listLexiconVersions,
  listTermCandidates,
  rejectTermCandidate,
  updateLexiconEntry,
} from '@/features/meeting-transcription/api'
import { formatMeetingDate } from '@/features/meeting-transcription/formatters'
import type { MeetingLexicon, MeetingLexiconEntry, MeetingLexiconVersion, MeetingTermCandidate } from '@/features/meeting-transcription/types'

export default function MeetingLexicon() {
  const { toast } = useToast()
  const [searchParams] = useSearchParams()
  const meetingId = searchParams.get('meeting_id')?.trim() || ''
  const [lexicon, setLexicon] = useState<MeetingLexicon>({ entries: [], version: 0, language: 'zh-CN' })
  const [candidates, setCandidates] = useState<MeetingTermCandidate[]>([])
  const [versions, setVersions] = useState<MeetingLexiconVersion[]>([])
  const [term, setTerm] = useState('')
  const [misrecognitions, setMisrecognitions] = useState('')
  const [weight, setWeight] = useState('5')
  const [scope, setScope] = useState<MeetingLexiconEntry['scope']>('user_future_meetings')
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(true)
  const [busyKey, setBusyKey] = useState('')
  const [error, setError] = useState('')
  const [correctionLearningEnabled, setCorrectionLearningEnabled] = useState(false)

  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true)
    try {
      const [currentLexicon, candidateItems, versionItems, capabilities] = await Promise.all([
        getMeetingLexicon(meetingId || undefined, signal),
        listTermCandidates('', signal),
        listLexiconVersions(meetingId || undefined, signal),
        getMeetingCapabilities(signal),
      ])
      setLexicon({ ...currentLexicon, entries: Array.isArray(currentLexicon.entries) ? currentLexicon.entries : [] })
      setCandidates(candidateItems)
      setVersions(versionItems)
      setCorrectionLearningEnabled(Boolean(capabilities.correction_learning?.available))
      setError('')
    } catch (loadError) {
      if (!signal?.aborted) setError(loadError instanceof Error ? loadError.message : '个人术语加载失败')
    } finally {
      if (!signal?.aborted) setLoading(false)
    }
  }, [meetingId])

  useEffect(() => {
    const controller = new AbortController()
    queueMicrotask(() => {
      if (!controller.signal.aborted) void load(controller.signal)
    })
    return () => controller.abort()
  }, [load])

  const effectiveScope = !meetingId && scope === 'current_meeting' ? 'user_future_meetings' : scope

  const filteredEntries = useMemo(() => {
    const keyword = query.trim().toLowerCase()
    if (!keyword) return lexicon.entries
    return lexicon.entries.filter((entry) => [entry.canonical_term, ...(entry.misrecognitions || []), ...(entry.aliases || [])].some((value) => value.toLowerCase().includes(keyword)))
  }, [lexicon.entries, query])

  const pendingCandidates = candidates.filter((candidate) => candidate.status === 'pending')

  async function addEntry(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!term.trim()) return
    if (effectiveScope === 'current_meeting' && !meetingId) {
      setError('仅本场会议的术语必须从具体会议进入后添加')
      return
    }
    setBusyKey('create')
    setError('')
    try {
      await createLexiconEntry({
        canonical_term: term.trim(),
        misrecognitions: misrecognitions.split(/[，,]/).map((value) => value.trim()).filter(Boolean),
        language: 'zh-CN',
        weight: Math.max(0, Math.min(10, Number(weight) || 5)),
        scope: effectiveScope,
        meeting_id: effectiveScope === 'current_meeting' ? meetingId : undefined,
      })
      setTerm('')
      setMisrecognitions('')
      toast({ title: '个人术语已添加', description: '新词库版本已发布，后续会议会按作用域加载。', type: 'success' })
      await load()
    } catch (createError) {
      setError(createError instanceof Error ? createError.message : '新增术语失败')
    } finally {
      setBusyKey('')
    }
  }

  async function decide(candidate: MeetingTermCandidate, action: 'confirm' | 'reject') {
    setBusyKey(candidate.id)
    try {
      const updated = action === 'confirm' ? await confirmTermCandidate(candidate.id) : await rejectTermCandidate(candidate.id)
      setCandidates((current) => current.map((item) => item.id === updated.id ? updated : item))
      if (action === 'confirm') await load()
      toast({ title: action === 'confirm' ? '候选已确认' : '候选已拒绝', type: 'success' })
    } catch (decisionError) {
      toast({ title: '操作失败', description: decisionError instanceof Error ? decisionError.message : '请稍后重试', type: 'error' })
    } finally {
      setBusyKey('')
    }
  }

  async function toggleEntry(entry: MeetingLexiconEntry) {
    setBusyKey(entry.id)
    try {
      const updated = await updateLexiconEntry(entry.id, { status: entry.status === 'active' ? 'paused' : 'active' })
      setLexicon((current) => ({ ...current, entries: current.entries.map((item) => item.id === updated.id ? updated : item) }))
      toast({ title: updated.status === 'active' ? '术语已启用' : '术语已暂停', type: 'success' })
    } catch (updateError) {
      toast({ title: '更新失败', description: updateError instanceof Error ? updateError.message : '请稍后重试', type: 'error' })
    } finally {
      setBusyKey('')
    }
  }

  async function removeEntry(entry: MeetingLexiconEntry) {
    if (!window.confirm(`删除个人术语“${entry.canonical_term}”？历史逐字稿不会被改写。`)) return
    setBusyKey(entry.id)
    try {
      await deleteLexiconEntry(entry.id)
      setLexicon((current) => ({ ...current, entries: current.entries.filter((item) => item.id !== entry.id) }))
      toast({ title: '术语已删除', type: 'success' })
    } catch (deleteError) {
      toast({ title: '删除失败', description: deleteError instanceof Error ? deleteError.message : '请稍后重试', type: 'error' })
    } finally {
      setBusyKey('')
    }
  }

  async function activate(version: MeetingLexiconVersion) {
    if (version.version === lexicon.version || !window.confirm(`激活词库版本 v${version.version}？新会议和后续识别将使用该版本。`)) return
    setBusyKey(`version-${version.version}`)
    try {
      await activateLexiconVersion(version.version, undefined, meetingId || undefined)
      setLexicon((current) => ({ ...current, version: version.version }))
      setVersions((current) => current.map((item) => ({ ...item, active: item.version === version.version })))
      toast({ title: `已激活词库 v${version.version}`, type: 'success' })
    } catch (activateError) {
      toast({ title: '版本激活失败', description: activateError instanceof Error ? activateError.message : '请稍后重试', type: 'error' })
    } finally {
      setBusyKey('')
    }
  }

  return (
    <PageShell variant="secondary" className="space-y-5">
      <PageHeader
        icon={Languages}
        eyebrow="Personal Lexicon"
        title="个人术语"
        description="确认识别订正产生的候选，并管理后续会议使用的个人热词版本。"
        meta={<><StatusBadge tone="info">当前 v{lexicon.version}</StatusBadge>{meetingId ? <StatusBadge>本场会议</StatusBadge> : null}<StatusBadge tone={pendingCandidates.length ? 'warning' : 'neutral'}>{pendingCandidates.length} 个待确认</StatusBadge></>}
        actions={<Button asChild variant="secondary"><Link to="/meetings"><ArrowLeft />返回会议</Link></Button>}
      />

      {error ? <div role="alert" className="rounded-md bg-error-soft px-4 py-3 text-sm text-error">{error}</div> : null}

      <PageSection title="手动新增" description="误识别写法用逗号分隔；歧义映射不会自动做确定性替换。">
        <form onSubmit={addEntry} className="grid gap-3 lg:grid-cols-[minmax(180px,1fr)_minmax(220px,1.2fr)_8rem_14rem_auto] lg:items-end">
          <div><label htmlFor="lexicon-term" className="text-sm font-semibold text-text">正确术语</label><Input id="lexicon-term" value={term} onChange={(event) => setTerm(event.target.value)} className="mt-2 h-11" placeholder="例如：海光信息" /></div>
          <div><label htmlFor="lexicon-wrong" className="text-sm font-semibold text-text">常见误识别</label><Input id="lexicon-wrong" value={misrecognitions} onChange={(event) => setMisrecognitions(event.target.value)} className="mt-2 h-11" placeholder="海光新息, 海光信息" /></div>
          <div><label htmlFor="lexicon-weight" className="text-sm font-semibold text-text">权重</label><Input id="lexicon-weight" type="number" min="0" max="10" step="0.5" value={weight} onChange={(event) => setWeight(event.target.value)} className="mt-2 h-11" /></div>
          <div><label htmlFor="lexicon-scope" className="text-sm font-semibold text-text">作用域</label><Select value={effectiveScope} onValueChange={(value) => setScope(value as MeetingLexiconEntry['scope'])}><SelectTrigger id="lexicon-scope" className="mt-2 h-11 w-full"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="user_future_meetings">个人未来会议</SelectItem><SelectItem value="current_meeting" disabled={!meetingId}>仅本场会议</SelectItem></SelectContent></Select></div>
          <Button type="submit" className="h-11" disabled={!term.trim() || busyKey === 'create'}>{busyKey === 'create' ? <Loader2 className="animate-spin" /> : <Plus />}新增</Button>
        </form>
      </PageSection>

      <PageSection contentClassName="p-0 sm:p-0">
        <Tabs defaultValue="entries">
          <TabsList variant="line" className="w-full overflow-x-auto px-4 pt-3 sm:px-5"><TabsTrigger value="entries"><SearchCheck />有效术语</TabsTrigger><TabsTrigger value="candidates"><Clock3 />订正候选</TabsTrigger><TabsTrigger value="versions"><History />版本历史</TabsTrigger></TabsList>
          <TabsContent value="entries" className="p-4 sm:p-5">
            <div className="mb-4 flex justify-end"><Input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索术语或误识别" aria-label="搜索个人术语" className="max-w-xs" /></div>
            {loading ? <div className="h-48 animate-pulse rounded-md bg-muted/60" /> : filteredEntries.length === 0 ? <EmptyState icon={Languages} size="sm" title="暂无个人术语" description="从上方手动新增，或确认识别订正产生的候选。" /> : (
              <div className="divide-y divide-border/70">
                {filteredEntries.map((entry) => (
                  <div key={entry.id} className="flex flex-col gap-3 py-4 first:pt-0 last:pb-0 sm:flex-row sm:items-center">
                    <div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-2"><p className="text-sm font-semibold text-text">{entry.canonical_term}</p><StatusBadge tone={entry.status === 'active' ? 'success' : 'neutral'}>{entry.status === 'active' ? '已启用' : '已暂停'}</StatusBadge><StatusBadge>{entry.scope === 'current_meeting' ? '本场会议' : '未来会议'}</StatusBadge></div><p className="mt-1 break-words text-xs leading-5 text-text-muted">误识别：{entry.misrecognitions?.join('、') || '未设置'} · 权重 {entry.weight} · 命中 {entry.hit_count || 0} · 误触发 {entry.false_positive_count || 0}</p></div>
                    <div className="flex gap-2"><Button type="button" size="sm" variant="secondary" className="max-sm:h-11" onClick={() => void toggleEntry(entry)} disabled={busyKey === entry.id}>{entry.status === 'active' ? <X /> : <Check />}{entry.status === 'active' ? '暂停' : '启用'}</Button><Button type="button" size="icon-sm" variant="ghost" className="max-sm:size-11" onClick={() => void removeEntry(entry)} disabled={busyKey === entry.id} aria-label="删除术语"><Trash2 /></Button></div>
                  </div>
                ))}
              </div>
            )}
          </TabsContent>
          <TabsContent value="candidates" className="p-4 sm:p-5">
            {pendingCandidates.length === 0 ? <EmptyState icon={SearchCheck} size="sm" title="没有待确认候选" description="选择“识别错误”的有效订正会经过分类后出现在这里。" /> : <div className="divide-y divide-border/70">{pendingCandidates.map((candidate) => <div key={candidate.id} className="flex flex-col gap-3 py-4 first:pt-0 last:pb-0 sm:flex-row sm:items-center"><div className="min-w-0 flex-1"><p className="text-sm font-semibold text-text">{candidate.canonical_term}</p><p className="mt-1 text-xs leading-5 text-text-muted">误识别：{candidate.misrecognition || '未提供'} · {candidate.source_count} 次证据 / {candidate.distinct_meeting_count} 场会议{candidate.confidence != null ? ` · 置信度 ${Math.round(candidate.confidence * 100)}%` : ''}</p></div><div className="flex gap-2"><Button type="button" size="sm" onClick={() => void decide(candidate, 'confirm')} disabled={!correctionLearningEnabled || busyKey === candidate.id}><Check />确认</Button><Button type="button" size="sm" variant="secondary" onClick={() => void decide(candidate, 'reject')} disabled={busyKey === candidate.id}><X />拒绝</Button></div></div>)}</div>}
          </TabsContent>
          <TabsContent value="versions" className="p-4 sm:p-5">
            {versions.length === 0 ? <EmptyState icon={History} size="sm" title="暂无版本历史" /> : <div className="divide-y divide-border/70">{versions.map((version) => <div key={version.version} className="flex items-center gap-3 py-4 first:pt-0 last:pb-0"><span className="premium-icon h-10 w-10 rounded-md"><History className="h-4 w-4" /></span><div className="min-w-0 flex-1"><div className="flex items-center gap-2"><p className="text-sm font-semibold text-text">v{version.version}</p>{version.version === lexicon.version || version.active ? <StatusBadge tone="success">当前生效</StatusBadge> : null}</div><p className="mt-1 text-xs text-text-muted">{version.entry_count} 个术语 · {formatMeetingDate(version.created_at)}{version.change_reason ? ` · ${version.change_reason}` : ''}</p></div>{version.version !== lexicon.version ? <Button type="button" size="sm" variant="secondary" onClick={() => void activate(version)} disabled={busyKey === `version-${version.version}`}><RotateCcw />激活</Button> : null}</div>)}</div>}
          </TabsContent>
        </Tabs>
      </PageSection>
    </PageShell>
  )
}
