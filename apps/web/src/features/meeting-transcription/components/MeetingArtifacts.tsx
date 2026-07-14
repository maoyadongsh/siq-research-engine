import {
  AlertTriangle,
  CheckSquare,
  FileText,
  KeyRound,
  ListChecks,
  Loader2,
  MessageSquareQuote,
  RefreshCw,
} from 'lucide-react'

import { EmptyState, StatusBadge } from '@/components/page'
import { Button } from '@/components/ui/button'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'

import { formatMeetingDate } from '../formatters'
import {
  hasMeetingMinutesContent,
  parseMeetingMinutes,
  selectLatestMinutesArtifact,
  selectPreferredMinutesArtifact,
  type MeetingMinutesContent,
  type MeetingMinutesItem,
  type MeetingMinutesSectionKey,
} from '../meetingArtifacts'
import type { MeetingArtifact } from '../types'

const sectionLabels: Record<MeetingMinutesSectionKey, string> = {
  agenda_topics: '议题',
  chapters: '章节',
  decisions: '决定',
  open_questions: '待确认问题',
  risks: '风险',
  action_items: '待办',
  speaker_viewpoints: '发言人观点',
  keywords: '关键词',
}

interface EvidenceListProps {
  items: MeetingMinutesItem[]
  emptyLabel: string
  onEvidence?: (segmentId: string) => void
  evidenceLabel?: (segmentId: string) => string
}

function EvidenceList({ items, emptyLabel, onEvidence, evidenceLabel }: EvidenceListProps) {
  if (!items.length) {
    return <EmptyState icon={FileText} size="sm" title={emptyLabel} description="当前纪要版本没有这一类内容。" />
  }
  return (
    <div className="divide-y divide-border/70">
      {items.map((item, index) => (
        <article key={`${item.text}-${index}`} className="min-w-0 py-3 first:pt-0 last:pb-0">
          <div className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
            <p className="min-w-0 whitespace-pre-wrap break-words text-sm leading-7 text-text">{item.text}</p>
            {item.speaker || item.owner || item.due_date ? (
              <div className="flex shrink-0 flex-wrap gap-x-3 gap-y-1 text-xs leading-5 text-text-muted sm:max-w-[45%] sm:justify-end">
                {item.speaker ? <span>{item.speaker}</span> : null}
                {item.owner ? <span>负责人：{item.owner}</span> : null}
                {item.due_date ? <span>截止：{item.due_date}</span> : null}
                {item.status ? <span>{item.status === 'confirmed' ? '已确认' : '建议项'}</span> : null}
              </div>
            ) : null}
          </div>
          {item.source_segment_ids.length ? (
            <div className="mt-2 flex min-w-0 flex-wrap items-center gap-1 text-xs text-text-muted">
              <span className="mr-1">证据</span>
              {item.source_segment_ids.map((segmentId, sourceIndex) => onEvidence ? (
                <Button
                  key={segmentId}
                  type="button"
                  size="sm"
                  variant="ghost"
                  className="h-8 max-w-full px-2 font-mono text-xs"
                  onClick={() => onEvidence(segmentId)}
                >
                  {evidenceLabel?.(segmentId) || `片段 ${sourceIndex + 1}`}
                </Button>
              ) : <span key={segmentId} className="font-mono">{evidenceLabel?.(segmentId) || `片段 ${sourceIndex + 1}`}</span>)}
            </div>
          ) : null}
        </article>
      ))}
    </div>
  )
}

interface MeetingMinutesSectionProps {
  content: MeetingMinutesContent
  section: MeetingMinutesSectionKey
  onEvidence?: (segmentId: string) => void
  evidenceLabel?: (segmentId: string) => string
}

export function MeetingMinutesSection({ content, section, onEvidence, evidenceLabel }: MeetingMinutesSectionProps) {
  return (
    <EvidenceList
      items={content[section]}
      emptyLabel={`暂无${sectionLabels[section]}`}
      onEvidence={onEvidence}
      evidenceLabel={evidenceLabel}
    />
  )
}

interface MeetingArtifactsProps {
  artifacts: MeetingArtifact[]
  compact?: boolean
  onEvidence?: (segmentId: string) => void
  evidenceLabel?: (segmentId: string) => string
  onRegenerate?: (artifact: MeetingArtifact) => void
  regenerating?: boolean
}

export function MeetingArtifacts({
  artifacts,
  compact = false,
  onEvidence,
  evidenceLabel,
  onRegenerate,
  regenerating = false,
}: MeetingArtifactsProps) {
  const selected = selectPreferredMinutesArtifact(artifacts)
  const latest = selectLatestMinutesArtifact(artifacts)

  if (!selected) {
    return <EmptyState icon={FileText} size="sm" title="暂未生成" description="稳定逐字稿累积后会异步更新。" />
  }
  if (!selected.content_json && !selected.content_text) {
    return selected.state === 'failed'
      ? <EmptyState icon={AlertTriangle} size="sm" title="本次生成失败" description="逐字稿仍已保存，可重新生成纪要。" />
      : <div className="flex min-h-32 items-center justify-center gap-2 text-sm text-text-muted"><Loader2 className="h-4 w-4 animate-spin" />正在生成纪要</div>
  }

  const content = parseMeetingMinutes(selected.content_json)
  const generatedAt = selected.generated_at || selected.updated_at || selected.created_at
  const newerPending = latest && latest.id !== selected.id && latest.state === 'generating'
  const newerFailed = latest && latest.id !== selected.id && latest.state === 'failed'
  const structured = hasMeetingMinutesContent(content)

  return (
    <div className="min-w-0">
      <div className="mb-4 flex min-w-0 flex-wrap items-center gap-2 border-b border-border/70 pb-3">
        <StatusBadge tone={selected.artifact_type === 'final_minutes' ? 'success' : 'info'}>
          {selected.artifact_type === 'final_minutes' ? '最终纪要' : '滚动纪要'} v{selected.version}
        </StatusBadge>
        {selected.state === 'stale' ? <StatusBadge tone="warning">基于旧版逐字稿</StatusBadge> : null}
        {newerPending ? <span className="inline-flex items-center gap-1 text-xs text-text-muted"><Loader2 className="h-3.5 w-3.5 animate-spin" />新版本生成中</span> : null}
        {newerFailed ? <span className="inline-flex items-center gap-1 text-xs text-error"><AlertTriangle className="h-3.5 w-3.5" />新版本生成失败</span> : null}
        {selected.state === 'stale' && onRegenerate ? (
          <Button type="button" size="sm" variant="secondary" className="ml-auto max-sm:h-11" onClick={() => onRegenerate(selected)} disabled={regenerating || newerPending}>
            {regenerating || newerPending ? <Loader2 className="animate-spin" /> : <RefreshCw />}重新生成
          </Button>
        ) : null}
      </div>

      {structured ? (
        <Tabs defaultValue="summary" className="min-w-0">
          <TabsList variant="line" className="w-full justify-start overflow-x-auto [&_[data-slot=tabs-trigger]]:flex-none">
            <TabsTrigger value="summary"><FileText />摘要</TabsTrigger>
            <TabsTrigger value="decisions"><ListChecks />决定</TabsTrigger>
            <TabsTrigger value="actions"><CheckSquare />待办</TabsTrigger>
            <TabsTrigger value="viewpoints"><MessageSquareQuote />观点</TabsTrigger>
            <TabsTrigger value="keywords"><KeyRound />关键词</TabsTrigger>
          </TabsList>
          <TabsContent value="summary" className={compact ? 'pt-3' : 'pt-4'}>
            {content.overview ? <p className="whitespace-pre-wrap break-words text-sm leading-7 text-text">{content.overview}</p> : null}
            {(['agenda_topics', 'chapters', 'open_questions', 'risks'] as const).map((section) => content[section].length ? (
              <section key={section} className="mt-5 border-t border-border/70 pt-4">
                <h4 className="mb-3 text-sm font-semibold text-text">{sectionLabels[section]}</h4>
                <MeetingMinutesSection content={content} section={section} onEvidence={onEvidence} evidenceLabel={evidenceLabel} />
              </section>
            ) : null)}
          </TabsContent>
          <TabsContent value="decisions" className={compact ? 'pt-3' : 'pt-4'}><MeetingMinutesSection content={content} section="decisions" onEvidence={onEvidence} evidenceLabel={evidenceLabel} /></TabsContent>
          <TabsContent value="actions" className={compact ? 'pt-3' : 'pt-4'}><MeetingMinutesSection content={content} section="action_items" onEvidence={onEvidence} evidenceLabel={evidenceLabel} /></TabsContent>
          <TabsContent value="viewpoints" className={compact ? 'pt-3' : 'pt-4'}><MeetingMinutesSection content={content} section="speaker_viewpoints" onEvidence={onEvidence} evidenceLabel={evidenceLabel} /></TabsContent>
          <TabsContent value="keywords" className={compact ? 'pt-3' : 'pt-4'}><MeetingMinutesSection content={content} section="keywords" onEvidence={onEvidence} evidenceLabel={evidenceLabel} /></TabsContent>
        </Tabs>
      ) : (
        <div className="whitespace-pre-wrap break-words text-sm leading-7 text-text">{selected.content_text}</div>
      )}
      {generatedAt ? <p className="mt-4 border-t border-border/70 pt-3 text-xs text-text-muted">更新于 {formatMeetingDate(generatedAt)}</p> : null}
    </div>
  )
}
