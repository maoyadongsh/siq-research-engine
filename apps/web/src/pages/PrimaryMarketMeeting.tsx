import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import {
  Activity,
  ArrowRightLeft,
  Bot,
  CheckCircle2,
  Eye,
  FileCheck2,
  GitBranch,
  History,
  Loader2,
  MessageSquareText,
  Network,
  Play,
  Plus,
  RefreshCw,
  ShieldAlert,
  Trash2,
  UsersRound,
  XCircle,
} from 'lucide-react'

import AgentProgressCard from '@/components/agent/AgentProgressCard'
import ClearChatConfirmDialog from '@/components/chat/ClearChatConfirmDialog'
import ChatComposer from '@/components/chat/ChatComposer'
import ChatHeader from '@/components/chat/ChatHeader'
import ChatMessageList, { type ChatQuickQuestion } from '@/components/chat/ChatMessageList'
import ChatShell from '@/components/chat/ChatShell'
import SessionHistoryList from '@/components/chat/SessionHistoryList'
import { EmptyState, PageHeader, PageSection, PageShell, StatusBadge, Surface } from '@/components/page'
import { Button } from '@/components/ui/button'
import type { DealSummary } from '@/lib/dealTypes'
import type { AgentAttachment, AgentMessage, AgentProgress, ChatSessionSummary, HistoryRecord } from '@/lib/useAgentChat'
import { buildAttachmentUploadItems, MAX_ATTACHMENTS, stripRenderedAttachmentMarkdown, validateAndSelectAttachments } from '@/lib/agentChatAttachments'
import { createStreamConsumer } from '@/lib/agentChatStream'
import { copyText } from '@/lib/clipboard'
import { displayLabelForPrompt } from '@/lib/quickQuestions'
import { useAutosizeTextarea } from '@/lib/useAutosizeTextarea'
import { useToast } from '@/hooks/useToast'
import {
  advancePrimaryMarketMeetingWorkflow,
  appendPrimaryMarketMeetingEvent,
  confirmPrimaryMarketDecision,
  createPrimaryMarketMeetingChatSession,
  deletePrimaryMarketMeetingChatSession,
  fetchPrimaryMarketAgents,
  fetchPrimaryMarketAudit,
  fetchPrimaryMarketMeetingAgentReadiness,
  fetchPrimaryMarketMeetingChatHistory,
  fetchPrimaryMarketMeetingChatSessions,
  fetchPrimaryMarketDecision,
  fetchPrimaryMarketDisputes,
  fetchPrimaryMarketEvidence,
  fetchPrimaryMarketMeetingTranscript,
  fetchPrimaryMarketPhaseArtifacts,
  fetchPrimaryMarketPreflight,
  fetchPrimaryMarketProject,
  fetchPrimaryMarketProjects,
  fetchPrimaryMarketR2AgentReports,
  fetchPrimaryMarketR3ReviewSummary,
  fetchPrimaryMarketStartupRetrieval,
  fetchPrimaryMarketWorkflow,
  preparePrimaryMarketMeetingAgent,
  preparePrimaryMarketMeetingCommittee,
  runPrimaryMarketMeetingR1Agent,
  runPrimaryMarketMeetingR1Serial,
  stopPrimaryMarketMeetingChat,
  streamPrimaryMarketMeetingChat,
  switchPrimaryMarketMeetingChatSession,
  uploadPrimaryMarketMeetingAttachments,
} from '@/features/primary-market/primaryMarketApi'
import type { PrimaryMarketMeetingAgentReadiness } from '@/features/primary-market/primaryMarketApi'
import {
  IC_AGENT_OPTIONS,
  R1_AGENT_SEQUENCE,
  agentLabel,
  buildMeetingEvents,
  coverageText,
  deriveAgentReadinessChips,
  deriveAgentReadinessLine,
  deriveCoordinatorNextActions,
  deriveAgentHandoffRows,
  deriveMeetingPreparationPlan,
  deriveMeetingAgentReadinessRows,
  deriveMeetingAgenda,
  deriveMeetingReceiptRows,
  deriveMeetingScoreRows,
  deriveMeetingScoringSummary,
  deriveR15DisputeBoard,
  deriveR2DeltaRows,
  deriveR3Timeline,
  deriveR4QualityObservability,
  deriveWorkflowPhaseObservability,
  dimensionLabel,
  formatTime,
  phaseLabel,
  primaryMarketMeetingIntro,
  primaryMarketMeetingQuickQuestions,
  sortedMissingDimensions,
  statusTone,
  text,
  type MeetingBundle,
  type MeetingEvent,
} from '@/features/primary-market/primaryMarketViewModel'

type SpeakerMode = 'single' | 'committee' | 'workflow'

function updateDealParam(setSearchParams: ReturnType<typeof useSearchParams>[1], dealId: string) {
  const next = new URLSearchParams()
  if (dealId) next.set('dealId', dealId)
  setSearchParams(next, { replace: true })
}

function meetingLane(mode: SpeakerMode, agentId: string) {
  if (mode === 'workflow') return 'workflow-main'
  if (mode === 'committee') return 'committee-main'
  return `agent-${agentId}`
}

function windowTitle(mode: SpeakerMode, agentId: string) {
  if (mode === 'workflow') return '总协调员工作流'
  if (mode === 'committee') return '全体委员会议'
  return agentLabel(agentId)
}

function sessionAgentForWindow(mode: SpeakerMode, agentId: string) {
  if (mode === 'workflow' || mode === 'committee') return 'siq_ic_master_coordinator'
  return agentId
}

function recentTranscriptText(events: MeetingEvent[], limit = 12) {
  const items = events
    .filter((event) => event.body.trim())
    .slice(-limit)
    .map((event) => {
      const body = event.body.replace(/\s+/g, ' ').slice(0, 520)
      return `${phaseLabel(event.phase)}｜${event.speaker}｜${event.title}：${body}`
    })
  return items.length ? items.join('\n') : '暂无可引用的会议纪要。'
}

function attachmentSummary(attachments: AgentAttachment[]) {
  if (!attachments.length) return '无'
  return attachments
    .map((item, index) => `${index + 1}. ${item.filename} (${item.kind}; ${item.content_type}; ${item.size} bytes)`)
    .join('\n')
}

function stringArray(value: unknown) {
  return Array.isArray(value) ? value.map((item) => String(item || '').trim()).filter(Boolean) : []
}

function workflowResultAction(result: Record<string, unknown> | undefined) {
  const nested = result?.action_result && typeof result.action_result === 'object' && !Array.isArray(result.action_result)
    ? result.action_result as Record<string, unknown>
    : null
  return String(result?.selected_action || nested?.workflow_action || result?.workflow_action || 'advance-next')
}

function resultRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

function resultBlockingReasons(result: unknown) {
  const record = resultRecord(result)
  return stringArray(record.blocking_reasons || record.blockingReasons)
}

function resultWarnings(result: unknown) {
  const record = resultRecord(result)
  return stringArray(record.warnings)
}

function resultArtifactPath(result: unknown) {
  const record = resultRecord(result)
  const nestedReport = resultRecord(record.report || record.r1_report || record.r1Report)
  const nestedArtifact = resultRecord(record.artifact || record.artifacts)
  return String(
    record.artifact_path
      || record.artifactPath
      || nestedReport.artifact_path
      || nestedReport.artifactPath
      || nestedArtifact.path
      || '',
  ).trim()
}

function serialAgentSummary(result: unknown) {
  const record = resultRecord(result)
  const executed = stringArray(record.executed_agent_ids || record.submitted_agent_ids)
  const planned = stringArray(record.planned_agent_ids)
  if (executed.length) return `executed=${executed.map(agentLabel).join(' / ')}`
  if (planned.length) return `planned=${planned.map(agentLabel).join(' / ')}`
  return `workflow_action=${String(record.workflow_action || 'run-r1-serial')}`
}

function agentIdFromDisplayPrefix(content: string) {
  const value = String(content || '').trim()
  return IC_AGENT_OPTIONS
    .filter((agent) => value.startsWith(`@${agent.label}`) || value.startsWith(`@${agent.value}`))
    .sort((a, b) => b.label.length - a.label.length)[0]?.value
}

function historyRecordToMessage(record: HistoryRecord, agentId?: string): AgentMessage {
  const role = record.role === 'assistant' ? 'assistant' : 'user'
  const attachments = record.attachments || undefined
  const strippedContent = stripRenderedAttachmentMarkdown(String(record.content || ''), attachments)
  return {
    role,
    content: role === 'user' ? displayLabelForPrompt(strippedContent) : strippedContent,
    createdAt: record.created_at || record.timestamp || undefined,
    attachments,
    agentId: role === 'assistant' ? agentId : undefined,
    agentName: role === 'assistant' && agentId ? agentLabel(agentId) : undefined,
  }
}

function busyMessage(agentId: string): AgentMessage {
  return {
    role: 'assistant',
    content: '',
    createdAt: new Date().toISOString(),
    streaming: true,
    agentId,
    agentName: agentLabel(agentId),
    progress: {
      status: 'running',
      title: `${agentLabel(agentId)} 正在发言`,
      detail: '正在读取项目上下文、附件和会议纪要。',
      source: 'primary-market',
    },
  }
}

function messageAgentId(message: AgentMessage, mode: SpeakerMode, selectedAgent: string) {
  if (message.agentId) return message.agentId
  if (mode === 'single') return selectedAgent
  return 'siq_ic_master_coordinator'
}

function messageIdentityMeta(message: AgentMessage, mode: SpeakerMode) {
  if (message.streaming) return '输出中'
  if (message.progress?.status === 'error') return '调用异常'
  if (message.progress?.status === 'stopped') return '已停止'
  if (mode === 'workflow') return '总协调员工作流'
  if (mode === 'committee') return '委员发言'
  return '一级市场智能体'
}

function IcMessageIdentity({
  message,
  mode,
  selectedAgent,
}: {
  message: AgentMessage
  mode: SpeakerMode
  selectedAgent: string
}) {
  if (message.role !== 'assistant') return null
  const id = messageAgentId(message, mode, selectedAgent)
  const name = message.agentName || agentLabel(id)
  return (
    <div className="primary-market-message-identity" title={id}>
      <span className="primary-market-message-avatar"><Bot className="h-3.5 w-3.5" /></span>
      <span className="primary-market-message-name">{name}</span>
      <span className="primary-market-message-meta">{messageIdentityMeta(message, mode)}</span>
    </div>
  )
}

function historyRecordsToMessages(records: HistoryRecord[], fallbackAgentId: string): AgentMessage[] {
  let nextAssistantAgent = fallbackAgentId
  return records.map((record) => {
    const role = record.role === 'assistant' ? 'assistant' : 'user'
    if (role === 'user') {
      nextAssistantAgent = agentIdFromDisplayPrefix(String(record.content || '')) || fallbackAgentId
      return historyRecordToMessage(record)
    }
    const message = historyRecordToMessage(record, nextAssistantAgent)
    nextAssistantAgent = fallbackAgentId
    return message
  })
}

export default function PrimaryMarketMeeting() {
  const { toast } = useToast()
  const [searchParams, setSearchParams] = useSearchParams()
  const selectedDealId = searchParams.get('dealId') || ''
  const detailView = searchParams.get('view') || ''
  const detailsMode = ['readiness', 'workflow-status', 'handoff', 'quality', 'minutes'].includes(detailView)
  const [deals, setDeals] = useState<DealSummary[]>([])
  const [dealsLoading, setDealsLoading] = useState(true)
  const [dealsError, setDealsError] = useState('')
  const [bundle, setBundle] = useState<MeetingBundle>({})
  const [contextLoading, setContextLoading] = useState(false)
  const [transcriptLoading, setTranscriptLoading] = useState(false)
  const [error, setError] = useState('')
  const [partialErrors, setPartialErrors] = useState<Record<string, string>>({})
  const [selectedAgent, setSelectedAgent] = useState('siq_ic_master_coordinator')
  const [speakerMode, setSpeakerMode] = useState<SpeakerMode>('single')
  const [selectedSpeakerIds, setSelectedSpeakerIds] = useState<string[]>(R1_AGENT_SEQUENCE)
  const [meetingInput, setMeetingInput] = useState('')
  const [composing, setComposing] = useState(false)
  const [attachments, setAttachments] = useState<AgentAttachment[]>([])
  const [uploadingAttachments, setUploadingAttachments] = useState(false)
  const [chatBusy, setChatBusy] = useState('')
  const [taskBusy, setTaskBusy] = useState('')
  const [actionError, setActionError] = useState('')
  const [historyNotice, setHistoryNotice] = useState('')
  const [historyOpen, setHistoryOpen] = useState(false)
  const [clearConfirmOpen, setClearConfirmOpen] = useState(false)
  const [chatHistoryLoading, setChatHistoryLoading] = useState(false)
  const [sessionsLoading, setSessionsLoading] = useState(false)
  const [currentSessionByLane, setCurrentSessionByLane] = useState<Record<string, string | null>>({})
  const [chatMessagesByLane, setChatMessagesByLane] = useState<Record<string, AgentMessage[]>>({})
  const [chatSessionsByLane, setChatSessionsByLane] = useState<Record<string, ChatSessionSummary[]>>({})
  const [persistedEventsByLane, setPersistedEventsByLane] = useState<Record<string, MeetingEvent[]>>({})
  const [localEventsByLane, setLocalEventsByLane] = useState<Record<string, MeetingEvent[]>>({})
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const messagesEnd = useRef<HTMLDivElement>(null)
  const activeAbortRef = useRef<AbortController | null>(null)
  const activeRunIdRef = useRef<string | null>(null)
  const firstEventTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const abortRequestedRef = useRef(false)
  const activeLane = useMemo(() => meetingLane(speakerMode, selectedAgent), [speakerMode, selectedAgent])
  const activeSessionAgent = useMemo(() => sessionAgentForWindow(speakerMode, selectedAgent), [speakerMode, selectedAgent])
  const activeWindowTitle = windowTitle(speakerMode, selectedAgent)
  const currentSessionId = currentSessionByLane[activeLane] || null
  const selectedDeal = deals.find((deal) => deal.deal_id === selectedDealId) || bundle.detail?.summary || null
  useAutosizeTextarea(textareaRef, meetingInput)

  useEffect(() => {
    const controller = new AbortController()
    void (async () => {
      setDealsLoading(true)
      setDealsError('')
      try {
        const payload = await fetchPrimaryMarketProjects({}, controller.signal)
        const nextDeals = Array.isArray(payload.deals) ? payload.deals : []
        setDeals(nextDeals)
        if (!selectedDealId && nextDeals[0]?.deal_id) updateDealParam(setSearchParams, nextDeals[0].deal_id)
      } catch (err) {
        if (!controller.signal.aborted) setDealsError(err instanceof Error ? err.message : '项目列表加载失败')
      } finally {
        if (!controller.signal.aborted) setDealsLoading(false)
      }
    })()
    return () => controller.abort()
  }, [selectedDealId, setSearchParams])

  const loadMeetingContext = useCallback(async (signal?: AbortSignal) => {
    if (!selectedDealId) return
    setContextLoading(true)
    setError('')
    setPartialErrors({})
    try {
      const primaryPromise = Promise.allSettled([
        fetchPrimaryMarketProject(selectedDealId, signal),
        fetchPrimaryMarketWorkflow(selectedDealId, signal),
        fetchPrimaryMarketPreflight(selectedDealId, signal),
        fetchPrimaryMarketDisputes(selectedDealId, signal),
        fetchPrimaryMarketPhaseArtifacts(selectedDealId, signal),
        fetchPrimaryMarketR2AgentReports(selectedDealId, signal),
        fetchPrimaryMarketR3ReviewSummary(selectedDealId, signal),
        fetchPrimaryMarketAgents(selectedDealId, signal),
        fetchPrimaryMarketDecision(selectedDealId, signal),
        fetchPrimaryMarketAudit(selectedDealId, signal),
        fetchPrimaryMarketEvidence(selectedDealId, { limit: 12 }, signal),
        fetchPrimaryMarketMeetingAgentReadiness(selectedDealId, signal),
      ])
      const retrievalAgentIds = IC_AGENT_OPTIONS.map((agent) => agent.value)
      const receiptsPromise = Promise.allSettled(
        retrievalAgentIds.map((agentId) => fetchPrimaryMarketStartupRetrieval(selectedDealId, agentId, signal)),
      )
      const [[detail, workflow, preflight, disputes, phaseArtifacts, r2Reports, r3Review, agents, decision, audit, evidence, meetingReadiness], receiptResults] = await Promise.all([
        primaryPromise,
        receiptsPromise,
      ])
      if (detail.status === 'rejected') throw detail.reason
      const errors: Record<string, string> = {}
      const startupReceipts = Object.fromEntries(
        retrievalAgentIds.map((agentId, index) => [
          agentId,
          receiptResults[index]?.status === 'fulfilled' ? receiptResults[index].value : null,
        ]),
      )
      const next: MeetingBundle = {
        detail: detail.value,
        workflow: workflow.status === 'fulfilled' ? workflow.value : null,
        preflight: preflight.status === 'fulfilled' ? preflight.value.preflight : null,
        disputes: disputes.status === 'fulfilled' ? disputes.value : null,
        phaseArtifacts: phaseArtifacts.status === 'fulfilled' ? phaseArtifacts.value : null,
        r2Reports: r2Reports.status === 'fulfilled' ? r2Reports.value : null,
        r3Review: r3Review.status === 'fulfilled' ? r3Review.value : null,
        agents: agents.status === 'fulfilled' ? agents.value : null,
        decision: decision.status === 'fulfilled' ? decision.value : null,
        audit: audit.status === 'fulfilled' ? audit.value : null,
        evidence: evidence.status === 'fulfilled' ? evidence.value : null,
        meetingReadiness: meetingReadiness.status === 'fulfilled' ? meetingReadiness.value : null,
        startupReceipts,
      }
      const settled = { workflow, preflight, disputes, phaseArtifacts, r2Reports, r3Review, agents, decision, audit, evidence, meetingReadiness }
      for (const [key, result] of Object.entries(settled)) {
        if (result.status === 'rejected') errors[key] = result.reason instanceof Error ? result.reason.message : `${key} 加载失败`
      }
      receiptResults.forEach((result, index) => {
        if (result.status === 'rejected') {
          const agentId = retrievalAgentIds[index]
          errors[`receipt:${agentId}`] = result.reason instanceof Error ? result.reason.message : `${agentId} receipt 加载失败`
        }
      })
      setBundle(next)
      setPartialErrors(errors)
    } catch (err) {
      if (!signal?.aborted) setError(err instanceof Error ? err.message : '会议状态加载失败')
    } finally {
      if (!signal?.aborted) setContextLoading(false)
    }
  }, [selectedDealId])

  const loadTranscript = useCallback(async (lane: string, signal?: AbortSignal) => {
    if (!selectedDealId) return
    setTranscriptLoading(true)
    try {
      const transcript = await fetchPrimaryMarketMeetingTranscript(selectedDealId, { lane, limit: 160 }, signal)
      const transcriptEventIds = new Set(transcript.events.map((event) => event.id))
      setPersistedEventsByLane((current) => ({ ...current, [lane]: transcript.events }))
      setLocalEventsByLane((current) => ({
        ...current,
        [lane]: (current[lane] || []).filter((event) => !transcriptEventIds.has(event.id)),
      }))
    } catch (err) {
      if (!signal?.aborted) setActionError(err instanceof Error ? err.message : 'meeting transcript 加载失败')
    } finally {
      if (!signal?.aborted) setTranscriptLoading(false)
    }
  }, [selectedDealId])

  const loadChatHistory = useCallback(async (
    agentId = activeSessionAgent,
    lane = activeLane,
    sessionId?: string | null,
    signal?: AbortSignal,
  ) => {
    if (!selectedDealId) return []
    setChatHistoryLoading(true)
    try {
      const history = await fetchPrimaryMarketMeetingChatHistory(
        agentId,
        selectedDealId,
        { lane, sessionId, limit: 200 },
        signal,
      )
      const nextMessages = historyRecordsToMessages(history.messages, agentId)
      setCurrentSessionByLane((current) => ({ ...current, [lane]: history.sessionId || sessionId || current[lane] || null }))
      setChatMessagesByLane((current) => ({ ...current, [lane]: nextMessages }))
      return nextMessages
    } catch (err) {
      if (!signal?.aborted) setActionError(err instanceof Error ? err.message : '一级市场会话历史加载失败')
      return []
    } finally {
      if (!signal?.aborted) setChatHistoryLoading(false)
    }
  }, [activeLane, activeSessionAgent, selectedDealId])

  const loadChatSessions = useCallback(async (
    agentId = activeSessionAgent,
    lane = activeLane,
    signal?: AbortSignal,
  ) => {
    if (!selectedDealId) return []
    setSessionsLoading(true)
    try {
      const payload = await fetchPrimaryMarketMeetingChatSessions(agentId, selectedDealId, { lane, limit: 100 }, signal)
      setChatSessionsByLane((current) => ({ ...current, [lane]: payload.sessions }))
      const currentSession = payload.sessions.find((session) => session.current)?.session_id || null
      if (currentSession) setCurrentSessionByLane((current) => ({ ...current, [lane]: currentSession }))
      return payload.sessions
    } catch (err) {
      if (!signal?.aborted) setActionError(err instanceof Error ? err.message : '一级市场历史会话加载失败')
      return []
    } finally {
      if (!signal?.aborted) setSessionsLoading(false)
    }
  }, [activeLane, activeSessionAgent, selectedDealId])

  useEffect(() => {
    const controller = new AbortController()
    void (async () => {
      await Promise.resolve()
      if (controller.signal.aborted) return
      setBundle({})
      setPartialErrors({})
      setActionError('')
      setPersistedEventsByLane({})
      setLocalEventsByLane({})
      setCurrentSessionByLane({})
      setChatMessagesByLane({})
      setChatSessionsByLane({})
      setHistoryNotice('')
      setHistoryOpen(false)
      setMeetingInput('')
      setAttachments([])
      if (selectedDealId) await loadMeetingContext(controller.signal)
    })()
    return () => controller.abort()
  }, [selectedDealId, loadMeetingContext])

  useEffect(() => {
    const controller = new AbortController()
    void (async () => {
      await Promise.resolve()
      if (controller.signal.aborted) return
      setActionError('')
      if (selectedDealId) await loadTranscript(activeLane, controller.signal)
    })()
    return () => controller.abort()
  }, [selectedDealId, activeLane, loadTranscript])

  useEffect(() => {
    const controller = new AbortController()
    void (async () => {
      await Promise.resolve()
      if (controller.signal.aborted) return
      setActionError('')
      if (selectedDealId) await loadChatHistory(activeSessionAgent, activeLane, undefined, controller.signal)
    })()
    return () => controller.abort()
  }, [selectedDealId, activeLane, activeSessionAgent, loadChatHistory])

  useEffect(() => {
    messagesEnd.current?.scrollIntoView({ behavior: 'smooth' })
  }, [activeLane, chatMessagesByLane, chatBusy])

  const agenda = useMemo(() => deriveMeetingAgenda(bundle), [bundle])
  const baseEvents = useMemo(() => buildMeetingEvents(bundle), [bundle])
  const persistedEvents = useMemo(() => persistedEventsByLane[activeLane] || [], [activeLane, persistedEventsByLane])
  const persistedEventIds = useMemo(() => new Set(persistedEvents.map((event) => event.id)), [persistedEvents])
  const localEvents = useMemo(() => localEventsByLane[activeLane] || [], [activeLane, localEventsByLane])
  const visibleLocalEvents = useMemo(
    () => localEvents.filter((event) => !persistedEventIds.has(event.id)),
    [localEvents, persistedEventIds],
  )
  const laneEvents = useMemo(() => [...persistedEvents, ...visibleLocalEvents], [persistedEvents, visibleLocalEvents])
  const evidenceReport = bundle.evidence?.quality_report || null
  const missingDimensions = sortedMissingDimensions(evidenceReport)
  const receiptRows = useMemo(() => deriveMeetingReceiptRows(bundle), [bundle])
  const readinessRows = useMemo(() => deriveMeetingAgentReadinessRows(bundle), [bundle])
  const scoreRows = useMemo(() => deriveMeetingScoreRows(bundle), [bundle])
  const scoringSummary = useMemo(() => deriveMeetingScoringSummary(scoreRows), [scoreRows])
  const coordinatorActions = useMemo(() => deriveCoordinatorNextActions(bundle), [bundle])
  const preparationPlan = useMemo(() => deriveMeetingPreparationPlan(bundle), [bundle])
  const phaseObservability = useMemo(() => deriveWorkflowPhaseObservability(bundle), [bundle])
  const handoffRows = useMemo(() => deriveAgentHandoffRows(bundle), [bundle])
  const disputeBoard = useMemo(() => deriveR15DisputeBoard(bundle), [bundle])
  const r2DeltaRows = useMemo(() => deriveR2DeltaRows(bundle), [bundle])
  const r3Timeline = useMemo(() => deriveR3Timeline(bundle), [bundle])
  const r4Quality = useMemo(() => deriveR4QualityObservability(bundle), [bundle])
  const readinessChips = useMemo(() => deriveAgentReadinessChips(bundle, selectedAgent, speakerMode), [bundle, selectedAgent, speakerMode])
  const readinessLine = useMemo(
    () => selectedDealId ? deriveAgentReadinessLine(bundle, selectedAgent, speakerMode) : '',
    [bundle, selectedAgent, selectedDealId, speakerMode],
  )
  const currentPhase = bundle.workflow?.workflow.current_phase || selectedDeal?.current_phase || '-'
  const committeeAgentOptions = useMemo(() => IC_AGENT_OPTIONS.filter((agent) => agent.r1), [])
  const selectedSpeakerSet = useMemo(() => new Set(selectedSpeakerIds), [selectedSpeakerIds])
  const selectedReadinessRow = readinessRows.find((row) => row.agentId === selectedAgent)
  const selectedAgentCanRunR1 = R1_AGENT_SEQUENCE.includes(selectedAgent)
  const selectedAgentCanPrepare = preparationPlan.individualProfileIds.includes(selectedAgent)
  const selectedAgentReadyForR1 = selectedReadinessRow?.readyForFormalTask !== false
  const r1CommitteeReady = readinessRows
    .filter((row) => R1_AGENT_SEQUENCE.includes(row.agentId))
    .every((row) => row.readyForFormalTask !== false)
  const decisionContract = bundle.decision?.contract || null
  const humanConfirmation = decisionContract?.human_confirmation
  const humanStatus = String(humanConfirmation?.status || (humanConfirmation?.confirmed ? 'confirmed' : 'pending'))
  const humanConfirmed = humanConfirmation?.confirmed === true || ['confirmed', 'approved', 'overridden'].includes(humanStatus.toLowerCase())
  const decisionReadyForHuman = Boolean(bundle.decision?.report_path || decisionContract?.decision || decisionContract?.artifacts?.markdown?.available)
  const chatMessages = useMemo(() => chatMessagesByLane[activeLane] || [], [activeLane, chatMessagesByLane])
  const chatSessions = useMemo(
    () => (chatSessionsByLane[activeLane] || []).map((session) => ({
      ...session,
      current: Boolean(currentSessionId && session.session_id === currentSessionId) || session.current,
    })),
    [activeLane, chatSessionsByLane, currentSessionId],
  )
  const messages = useMemo(() => {
    const hasStreamingMessage = chatMessages.some((message) => message.role === 'assistant' && message.streaming)
    return chatBusy && !hasStreamingMessage ? [...chatMessages, busyMessage(chatBusy)] : chatMessages
  }, [chatMessages, chatBusy])

  const refreshMeeting = useCallback(async () => {
    await Promise.allSettled([
      loadMeetingContext(),
      loadTranscript(activeLane),
      loadChatHistory(activeSessionAgent, activeLane, currentSessionId),
      loadChatSessions(activeSessionAgent, activeLane),
    ])
  }, [activeLane, activeSessionAgent, currentSessionId, loadChatHistory, loadChatSessions, loadMeetingContext, loadTranscript])

  const applyMeetingReadiness = useCallback((readiness?: PrimaryMarketMeetingAgentReadiness | null) => {
    if (!readiness) return
    setBundle((current) => ({ ...current, meetingReadiness: readiness }))
  }, [])

  const appendMeetingEvent = useCallback((
    event: Omit<MeetingEvent, 'id' | 'createdAt'> & { id?: string; createdAt?: string | null },
    lane = activeLane,
  ) => {
    const stamp = Date.now()
    const next: MeetingEvent = {
      ...event,
      id: event.id || `meeting-${stamp}-${Math.random().toString(36).slice(2)}`,
      createdAt: event.createdAt || new Date(stamp).toISOString(),
    }
    setLocalEventsByLane((current) => ({ ...current, [lane]: [...(current[lane] || []), next] }))
    if (selectedDealId) {
      void appendPrimaryMarketMeetingEvent(selectedDealId, next, { lane })
        .then((saved) => {
          setPersistedEventsByLane((current) => {
            const existing = current[lane] || []
            if (existing.some((item) => item.id === saved.id)) return current
            return { ...current, [lane]: [...existing, saved] }
          })
          setLocalEventsByLane((current) => ({
            ...current,
            [lane]: (current[lane] || []).filter((item) => item.id !== saved.id),
          }))
        })
        .catch(() => {
          setActionError('会议事件已显示，但归档到项目包失败；请稍后刷新后重试。')
        })
    }
    return next
  }, [activeLane, selectedDealId])

  const toggleCommitteeSpeaker = (agentId: string) => {
    setSelectedSpeakerIds((current) => {
      if (current.includes(agentId)) return current.length <= 1 ? current : current.filter((item) => item !== agentId)
      return R1_AGENT_SEQUENCE.filter((item) => item === agentId || current.includes(item))
    })
  }

  const updateLastAssistantMessage = useCallback((lane: string, updater: (message: AgentMessage) => AgentMessage) => {
    setChatMessagesByLane((current) => {
      const rows = current[lane] || []
      const index = [...rows].reverse().findIndex((message) => message.role === 'assistant')
      if (index < 0) return current
      const targetIndex = rows.length - 1 - index
      return {
        ...current,
        [lane]: [
          ...rows.slice(0, targetIndex),
          updater(rows[targetIndex]),
          ...rows.slice(targetIndex + 1),
        ],
      }
    })
  }, [])

  const clearFirstEventTimer = useCallback(() => {
    if (firstEventTimerRef.current) {
      clearTimeout(firstEventTimerRef.current)
      firstEventTimerRef.current = null
    }
  }, [])

  const startFirstEventTimer = useCallback((lane: string) => {
    clearFirstEventTimer()
    firstEventTimerRef.current = setTimeout(() => {
      updateLastAssistantMessage(lane, (message) => ({
        ...message,
        progress: {
          status: 'running',
          title: '等待模型首轮输出',
          detail: '一级市场智能体正在读取项目上下文、附件和会议纪要。',
          percent: 8,
          source: 'runtime',
        },
      }))
    }, 8000)
  }, [clearFirstEventTimer, updateLastAssistantMessage])

  useEffect(() => () => clearFirstEventTimer(), [clearFirstEventTimer])

  const buildAgentPrompt = useCallback((agentId: string, userMessage: string, priorReplies: Array<{ agentId: string; reply: string }> = [], sentAttachments: AgentAttachment[] = []) => {
    const receipt = receiptRows.find((row) => row.agentId === agentId)
    const score = scoreRows.find((row) => row.agentId === agentId)
    const disputeCount = bundle.disputes?.counts?.disputes ?? bundle.disputes?.disputes?.length ?? 0
    const unresolvedCount = bundle.disputes?.counts?.unresolved ?? 0
    const recommendations = coordinatorActions.slice(0, 3).map((action) => `${action.phase}: ${action.label}`).join(' / ')
    const prior = priorReplies.length
      ? priorReplies.map((item) => `${agentLabel(item.agentId)}：${item.reply.slice(0, 700)}`).join('\n\n')
      : '暂无前序发言。'
    const recentTranscript = recentTranscriptText([...baseEvents, ...laneEvents])

    return [
      `你是 ${agentLabel(agentId)} (${agentId})，正在 SIQ 一级市场投研决策流程中发言。`,
      '请严格以该智能体身份回答，不要冒充其他委员；如果信息不足，明确指出所需材料、证据缺口或附件读取限制。',
      '输出要求：先给结论，再列 3-5 条关键理由；涉及事实时尽量引用 evidence id、文档名、附件名或当前已知产物；最后给下一步建议。',
      '',
      `项目：${selectedDeal?.company_name || selectedDealId || '未选择项目'} (${selectedDealId || '-'})`,
      `行业/阶段：${text(selectedDeal?.industry, '-')} / ${text(selectedDeal?.stage, '-')}`,
      `当前流程阶段：${phaseLabel(currentPhase)}；workflow status：${text(bundle.workflow?.workflow.status, '-')}`,
      `R0 preflight：${text(bundle.preflight?.status, '未加载')}；证据覆盖：${coverageText(evidenceReport)}；证据条目：${evidenceReport?.item_count ?? bundle.evidence?.total_item_count ?? 0}`,
      `缺失维度：${missingDimensions.length ? missingDimensions.map((dimension) => dimensionLabel(dimension)).join(' / ') : '无明显缺口'}`,
      `本智能体 Receipt：${receipt?.present ? 'present' : 'missing'}；hits=${receipt?.evidenceHits ?? 0}；warnings=${receipt?.warnings.join(' / ') || '-'}`,
      `本智能体 R1 报告：${score?.hasReport ? 'present' : 'missing'}；score=${score?.scoreText || '-'}；recommendation=${score?.recommendation || '-'}`,
      `R1 评分：${scoringSummary.scoredCount}/${scoringSummary.count} 已评分；均分=${scoringSummary.average ?? '-'}；支持/观察/反对=${scoringSummary.supportCount}/${scoringSummary.watchCount}/${scoringSummary.opposeCount}`,
      `R1.5 分歧：${disputeCount} 个；未解决=${unresolvedCount}`,
      `总协调员建议：${recommendations || '-'}`,
      `本次用户上传附件：\n${attachmentSummary(sentAttachments)}`,
      '',
      `最近会议纪要：\n${recentTranscript}`,
      '',
      `前序委员发言摘要：\n${prior}`,
      '',
      `人类主持人问题：${userMessage}`,
    ].join('\n')
  }, [baseEvents, bundle, coordinatorActions, currentPhase, evidenceReport, laneEvents, missingDimensions, receiptRows, scoreRows, scoringSummary, selectedDeal, selectedDealId])

  const sendMeetingMessage = async (overrideText?: string, displayText?: string) => {
    const content = (overrideText ?? meetingInput).trim()
    const messageAttachments = [...attachments]
    if ((!content && !messageAttachments.length) || !selectedDealId || chatBusy || uploadingAttachments) return
    const lane = activeLane
    const targets = speakerMode === 'workflow'
      ? ['siq_ic_master_coordinator']
      : speakerMode === 'committee'
        ? selectedSpeakerIds
        : [selectedAgent]
    if (!targets.length) {
      setActionError('请至少选择一个参会智能体。')
      return
    }

    const visibleContent = (displayText || content).trim() || '请分析这些附件'
    const promptContent = content || visibleContent
    const userCreatedAt = new Date().toISOString()
    appendMeetingEvent({
      phase: currentPhase,
      type: 'human_intervention',
      speaker: 'Human',
      title: speakerMode === 'workflow'
        ? '主持人交给总协调员'
        : speakerMode === 'committee'
          ? '主持人提问全体委员'
          : `主持人点名 ${agentLabel(selectedAgent)}`,
      body: visibleContent,
      tone: 'info',
      meta: lane,
      attachments: messageAttachments,
    }, lane)
    setChatMessagesByLane((current) => ({
      ...current,
      [lane]: [
        ...(current[lane] || []),
        { role: 'user', content: visibleContent, createdAt: userCreatedAt, attachments: messageAttachments },
      ],
    }))
    setMeetingInput('')
    setAttachments([])
    setActionError('')
    setHistoryNotice('')
    const abort = new AbortController()
    activeAbortRef.current = abort
    abortRequestedRef.current = false
    const priorReplies: Array<{ agentId: string; reply: string }> = []
    let effectiveSessionId = currentSessionId
    try {
      for (const agentId of targets) {
        if (abort.signal.aborted) break
        setChatBusy(agentId)
        let cancelPendingDeltaFlush = () => {}
        try {
          const basePrompt = buildAgentPrompt(agentId, promptContent, priorReplies, messageAttachments)
          const prompt = speakerMode === 'workflow'
            ? [
              basePrompt,
              '',
              '会议模式：总协调员工作流。',
              '请根据当前 R0-R4 状态判断下一步应该执行的投研决策动作，输出执行计划、风险门禁、需要人工确认的点。',
              '不要声称已经写入或已经执行；真正写入由现有 Workflow/投决页面完成。',
            ].join('\n')
            : basePrompt
          let reply = ''
          setChatMessagesByLane((current) => ({
            ...current,
            [lane]: [
              ...(current[lane] || []),
              {
                role: 'assistant',
                content: '',
                createdAt: new Date().toISOString(),
                streaming: true,
                agentId,
                agentName: agentLabel(agentId),
                progress: {
                  status: 'queued',
                  title: `${agentLabel(agentId)} 已提交`,
                  detail: '正在连接一级市场智能体',
                  percent: 0,
                  source: 'runtime',
                },
              },
            ],
          }))
          let deltaBuffer = ''
          let deltaFlushTimer: ReturnType<typeof setTimeout> | null = null
          const cancelDeltaFlush = () => {
            if (deltaFlushTimer) {
              clearTimeout(deltaFlushTimer)
              deltaFlushTimer = null
            }
          }
          cancelPendingDeltaFlush = cancelDeltaFlush
          const flushDeltaBuffer = () => {
            if (!deltaBuffer) return
            const chunk = deltaBuffer
            deltaBuffer = ''
            cancelDeltaFlush()
            updateLastAssistantMessage(lane, (message) => ({ ...message, content: `${message.content || ''}${chunk}` }))
          }
          const scheduleDeltaFlush = () => {
            if (deltaFlushTimer) return
            deltaFlushTimer = setTimeout(flushDeltaBuffer, 80)
          }
          const streamConsumer = createStreamConsumer({
            setCurrentSession: (sessionId) => {
              if (!sessionId) return
              effectiveSessionId = sessionId
              setCurrentSessionByLane((current) => ({ ...current, [lane]: sessionId }))
            },
            setActiveRunId: (runId) => { activeRunIdRef.current = runId },
            startFirstEventTimer: () => startFirstEventTimer(lane),
            clearFirstEventTimer,
            appendAssistantDelta: (delta) => {
              reply += delta
              deltaBuffer += delta
              scheduleDeltaFlush()
            },
            flushAssistantDelta: flushDeltaBuffer,
            replaceAssistantContent: (content) => {
              deltaBuffer = ''
              cancelDeltaFlush()
              reply = content
              updateLastAssistantMessage(lane, (message) => ({ ...message, content }))
            },
            updateAssistantProgress: (progress: AgentProgress) => {
              flushDeltaBuffer()
              updateLastAssistantMessage(lane, (message) => ({ ...message, progress }))
            },
            responseErrorMessage: async (res, fallback) => {
              try {
                const payload = await res.json()
                return payload?.detail || payload?.message || payload?.error || fallback
              } catch {
                return await res.text().catch(() => fallback) || fallback
              }
            },
          })
          const streamRes = await streamPrimaryMarketMeetingChat({
            agentId,
            agentLabel: agentLabel(agentId),
            message: prompt,
            displayMessage: `@${agentLabel(agentId)} ${visibleContent}`,
            dealId: selectedDeal?.deal_id || selectedDealId,
            companyName: selectedDeal?.company_name,
            lane,
            sessionId: effectiveSessionId,
            attachments: messageAttachments,
            signal: abort.signal,
          })
          await streamConsumer.consumeEventStream(streamRes)
          flushDeltaBuffer()
          clearFirstEventTimer()
          reply = reply.trim() || '智能体未返回内容。'
          updateLastAssistantMessage(lane, (message) => ({
            ...message,
            content: reply,
            streaming: false,
            agentId: message.agentId || agentId,
            agentName: message.agentName || agentLabel(agentId),
            progress: message.progress?.status === 'error'
              ? {
                ...message.progress,
                status: 'error',
              }
              : undefined,
          }))
          priorReplies.push({ agentId, reply })
          appendMeetingEvent({
            phase: currentPhase,
            type: 'agent_speech',
            speaker: agentLabel(agentId),
            title: speakerMode === 'workflow' ? '总协调员工作流建议' : speakerMode === 'committee' ? '委员顺序发言' : '点名发言',
            body: reply,
            tone: 'info',
            meta: `hermes:${agentId}`,
            agentId,
          }, lane)
        } catch (err) {
          cancelPendingDeltaFlush()
          const errorBody = abortRequestedRef.current
            ? '前端已停止等待本次智能体输出。若后台已完成，可稍后刷新会议纪要确认。'
            : err instanceof Error ? err.message : '智能体调用失败'
          setChatMessagesByLane((current) => ({
            ...current,
            [lane]: (() => {
              const rows = current[lane] || []
              const last = rows[rows.length - 1]
              const errorMessage: AgentMessage = {
                role: 'assistant',
                content: abortRequestedRef.current ? `[已停止] ${errorBody}` : `[错误] ${errorBody}`,
                createdAt: last?.createdAt || new Date().toISOString(),
                streaming: false,
                agentId,
                agentName: agentLabel(agentId),
                progress: abortRequestedRef.current
                  ? { status: 'stopped', title: '任务已停止', detail: errorBody, source: 'runtime' }
                  : { status: 'error', title: '智能体调用失败', detail: errorBody, source: 'runtime' },
              }
              if (last?.role === 'assistant' && last.streaming) return [...rows.slice(0, -1), errorMessage]
              return [...rows, errorMessage]
            })(),
          }))
          appendMeetingEvent({
            phase: currentPhase,
            type: 'system_blocking',
            speaker: agentLabel(agentId),
            title: abortRequestedRef.current ? '本次调用已停止' : '智能体调用失败',
            body: errorBody,
            tone: 'error',
            meta: `hermes:${agentId}`,
            agentId,
          }, lane)
          if (abortRequestedRef.current) break
        }
      }
    } finally {
      clearFirstEventTimer()
      setChatBusy('')
      activeAbortRef.current = null
      activeRunIdRef.current = null
      abortRequestedRef.current = false
      void loadTranscript(lane)
      void loadChatSessions(activeSessionAgent, lane)
    }
  }

  const stopChat = () => {
    abortRequestedRef.current = true
    const stopAgent = chatBusy || activeSessionAgent
    if (activeRunIdRef.current && selectedDealId) {
      void stopPrimaryMarketMeetingChat(stopAgent, selectedDealId, {
        lane: activeLane,
        sessionId: currentSessionId,
      }).catch((err) => {
        setActionError(err instanceof Error ? err.message : '停止智能体输出失败')
      })
    }
    activeAbortRef.current?.abort()
    clearFirstEventTimer()
    setChatBusy('')
  }

  const handleAttachmentChange = async (files: FileList | null) => {
    if (!files?.length) return
    if (!selectedDealId) {
      toast({ type: 'error', title: '请先选择项目' })
      return
    }
    let prepared: ReturnType<typeof buildAttachmentUploadItems> = []
    try {
      const selected = validateAndSelectAttachments(files, attachments.length)
      if (!selected.length) return
      prepared = buildAttachmentUploadItems(selected)
      const tempAttachments = prepared.map((item) => item.tempAttachment)
      const tempIds = new Set(tempAttachments.map((item) => item.id))
      setUploadingAttachments(true)
      setAttachments((current) => [...current, ...tempAttachments].slice(0, MAX_ATTACHMENTS))
      const payloadFiles = await Promise.all(prepared.map((item) => item.payloadPromise))
      const uploaded = await uploadPrimaryMarketMeetingAttachments(selectedDealId, payloadFiles)
      setAttachments((current) => current
        .flatMap((item) => {
          if (!tempIds.has(item.id)) return [item]
          const index = tempAttachments.findIndex((temp) => temp.id === item.id)
          return uploaded[index] ? [uploaded[index]] : []
        })
        .slice(0, MAX_ATTACHMENTS))
    } catch (err) {
      const tempIds = new Set(prepared.map((item) => item.tempAttachment.id))
      setAttachments((current) => current.filter((item) => !tempIds.has(item.id)))
      toast({
        type: 'error',
        title: '附件上传失败',
        description: err instanceof Error ? err.message : '请检查附件格式和大小。',
      })
    } finally {
      prepared.forEach((item) => URL.revokeObjectURL(item.previewUrl))
      setUploadingAttachments(false)
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }

  const copyMessage = async (content: string) => {
    if (await copyText(content)) {
      toast({ type: 'success', title: '消息已复制' })
    } else {
      toast({ type: 'error', title: '复制失败', description: '浏览器未授权剪贴板访问，请手动选中文本复制。' })
    }
  }

  const createNewChat = async () => {
    if (!selectedDealId || chatBusy) return
    const response = await createPrimaryMarketMeetingChatSession(activeSessionAgent, selectedDealId, { lane: activeLane })
    setCurrentSessionByLane((current) => ({ ...current, [activeLane]: response.sessionId }))
    setChatMessagesByLane((current) => ({ ...current, [activeLane]: [] }))
    setMeetingInput('')
    setAttachments([])
    setHistoryOpen(false)
    setHistoryNotice('已新建会话')
    toast({ type: 'success', title: '已新建会话' })
    await loadChatSessions(activeSessionAgent, activeLane)
  }

  const showHistory = async () => {
    if (!selectedDealId) return
    setHistoryOpen(true)
    setHistoryNotice('正在加载历史会话…')
    const list = await loadChatSessions(activeSessionAgent, activeLane)
    setHistoryNotice(list.length ? `已找到 ${list.length} 个历史会话` : '当前没有历史会话')
  }

  const openSession = async (sessionId: string) => {
    if (!selectedDealId || chatBusy) return
    await switchPrimaryMarketMeetingChatSession(activeSessionAgent, selectedDealId, sessionId, { lane: activeLane })
    setCurrentSessionByLane((current) => ({ ...current, [activeLane]: sessionId }))
    await loadChatHistory(activeSessionAgent, activeLane, sessionId)
    setHistoryOpen(false)
    setHistoryNotice('已打开历史会话')
    messagesEnd.current?.scrollIntoView({ behavior: 'smooth' })
  }

  const deleteHistory = async () => {
    if (!selectedDealId || chatBusy) return
    const response = await deletePrimaryMarketMeetingChatSession(activeSessionAgent, selectedDealId, {
      lane: activeLane,
      sessionId: currentSessionId,
    })
    setCurrentSessionByLane((current) => ({ ...current, [activeLane]: response.sessionId }))
    setChatMessagesByLane((current) => ({ ...current, [activeLane]: [] }))
    setHistoryOpen(false)
    setHistoryNotice('历史会话已删除')
    toast({ type: 'success', title: '历史会话已删除' })
    await loadChatSessions(activeSessionAgent, activeLane)
  }

  const prepareSelectedAgent = async () => {
    if (!selectedDealId || taskBusy) return
    if (!selectedAgentCanPrepare) {
      const message = `${agentLabel(selectedAgent)}不参与${preparationPlan.label}；请选择该阶段参与角色。`
      setActionError(message)
      toast({ type: 'warning', title: '当前角色无需准备', description: message })
      return
    }
    setTaskBusy('prepare-agent')
    setActionError('')
    try {
      const response = await preparePrimaryMarketMeetingAgent(selectedDealId, selectedAgent, {
        round_name: preparationPlan.roundName,
        limit: 10,
        include_vector: true,
        include_rerank: false,
      })
      applyMeetingReadiness(response.readiness)
      toast({
        type: response.skipped ? 'info' : 'success',
        title: response.skipped ? '当前智能体无需准备' : `${preparationPlan.roundName} 智能体准备完成`,
        description: response.reason || response.receipt?.receipt_id || `${preparationPlan.label} · ${agentLabel(selectedAgent)}`,
      })
      await refreshMeeting()
    } catch (err) {
      const message = err instanceof Error ? err.message : '准备智能体失败'
      setActionError(message)
      toast({ type: 'error', title: '准备智能体失败', description: message })
    } finally {
      setTaskBusy('')
    }
  }

  const prepareCommittee = async () => {
    if (!selectedDealId || taskBusy) return
    setTaskBusy('prepare-committee')
    setActionError('')
    try {
      const response = await preparePrimaryMarketMeetingCommittee(selectedDealId, {
        round_name: preparationPlan.roundName,
        limit: 10,
        include_vector: true,
        include_rerank: false,
        profile_ids: preparationPlan.profileIds,
      })
      applyMeetingReadiness(response.readiness)
      const failed = (response.results || []).filter((item) => item.status === 'failed')
      toast({
        type: failed.length ? 'warning' : 'success',
        title: failed.length ? `${preparationPlan.roundName} 参与角色部分准备失败` : `${preparationPlan.roundName} 参与角色准备完成`,
        description: `${Math.max((response.results || []).length - failed.length, 0)}/${preparationPlan.profileIds.length} completed`,
      })
      await refreshMeeting()
    } catch (err) {
      const message = err instanceof Error ? err.message : '准备全体委员失败'
      setActionError(message)
      toast({ type: 'error', title: '准备全体委员失败', description: message })
    } finally {
      setTaskBusy('')
    }
  }

  const advanceWorkflow = async (dryRun: boolean) => {
    if (!selectedDealId || taskBusy) return
    if (!dryRun && !window.confirm('确认执行下一步 Workflow？该操作可能写入正式投研产物。')) return
    setSpeakerMode('workflow')
    setTaskBusy(dryRun ? 'workflow-dry-run' : 'workflow-execute')
    setActionError('')
    try {
      const response = await advancePrimaryMarketMeetingWorkflow(selectedDealId, {
        dry_run: dryRun,
        allow_hermes: !dryRun,
        max_agents: 1,
        r3_skip: false,
        r3_skip_reason: null,
        r4_overwrite: false,
      })
      applyMeetingReadiness(response.readiness)
      const result = response.result
      const reasons = stringArray(result?.blocking_reasons)
      toast({
        type: reasons.length ? 'warning' : 'success',
        title: dryRun ? `预演 ${workflowResultAction(result)}` : `已执行 ${workflowResultAction(result)}`,
        description: reasons.length ? reasons.slice(0, 2).join(' / ') : selectedDeal?.company_name || selectedDealId,
      })
      await refreshMeeting()
    } catch (err) {
      const message = err instanceof Error ? err.message : dryRun ? '预演 Workflow 失败' : '执行 Workflow 失败'
      setActionError(message)
      toast({ type: 'error', title: dryRun ? '预演 Workflow 失败' : '执行 Workflow 失败', description: message })
    } finally {
      setTaskBusy('')
    }
  }

  const runCurrentAgentR1 = async (dryRun: boolean) => {
    if (!selectedDealId || taskBusy || !selectedAgentCanRunR1) return
    if (!dryRun && !selectedAgentReadyForR1) {
      const message = selectedReadinessRow?.blockingReasons?.join(' / ') || '当前智能体 readiness 未通过，先准备智能体或刷新状态。'
      setActionError(message)
      toast({ type: 'warning', title: '当前智能体尚未 ready', description: message })
      return
    }
    if (!dryRun && !window.confirm(`确认执行 ${agentLabel(selectedAgent)} 的正式 R1 任务？该操作可能写入 R1 产物。`)) return
    const lane = meetingLane('single', selectedAgent)
    setSpeakerMode('single')
    setTaskBusy(dryRun ? 'r1-agent-dry-run' : 'r1-agent-execute')
    setActionError('')
    try {
      const response = await runPrimaryMarketMeetingR1Agent(selectedDealId, selectedAgent, {
        round_name: 'R1',
        dry_run: dryRun,
        allow_hermes: !dryRun,
        lane,
      })
      const reasons = resultBlockingReasons(response)
      const warnings = resultWarnings(response)
      const artifactPath = resultArtifactPath(response)
      toast({
        type: reasons.length ? 'warning' : 'success',
        title: dryRun ? `预演 ${agentLabel(selectedAgent)} R1` : `${agentLabel(selectedAgent)} R1 已执行`,
        description: reasons[0] || warnings[0] || artifactPath || selectedDeal?.company_name || selectedDealId,
      })
      await refreshMeeting()
    } catch (err) {
      const message = err instanceof Error ? err.message : dryRun ? '预演当前智能体 R1 失败' : '执行当前智能体 R1 失败'
      setActionError(message)
      toast({ type: 'error', title: dryRun ? '预演当前 R1 失败' : '执行当前 R1 失败', description: message })
    } finally {
      setTaskBusy('')
    }
  }

  const runR1Serial = async () => {
    if (!selectedDealId || taskBusy) return
    if (!r1CommitteeReady) {
      const blocked = readinessRows
        .filter((row) => R1_AGENT_SEQUENCE.includes(row.agentId) && row.readyForFormalTask === false)
        .map((row) => row.label)
        .join(' / ')
      const message = blocked ? `${blocked} readiness 未通过，先准备全体委员或刷新状态。` : 'R1 串行 readiness 未通过。'
      setActionError(message)
      toast({ type: 'warning', title: 'R1 串行尚未 ready', description: message })
      return
    }
    if (!window.confirm('确认执行 R1 串行正式任务？该操作可能为多位委员写入 R1 产物。')) return
    setSpeakerMode('workflow')
    setTaskBusy('r1-serial-execute')
    setActionError('')
    try {
      const response = await runPrimaryMarketMeetingR1Serial(selectedDealId, {
        round_name: 'R1',
        dry_run: false,
        allow_hermes: true,
        max_agents: R1_AGENT_SEQUENCE.length,
        lane: 'workflow-main',
      })
      const reasons = resultBlockingReasons(response)
      const warnings = resultWarnings(response)
      toast({
        type: reasons.length ? 'warning' : 'success',
        title: 'R1 串行任务已执行',
        description: reasons[0] || warnings[0] || serialAgentSummary(response),
      })
      await refreshMeeting()
    } catch (err) {
      const message = err instanceof Error ? err.message : '执行 R1 串行失败'
      setActionError(message)
      toast({ type: 'error', title: '执行 R1 串行失败', description: message })
    } finally {
      setTaskBusy('')
    }
  }

  const writeHumanConfirmation = async (kind: 'preview' | 'confirm' | 'revision' | 'reject') => {
    if (!selectedDealId || taskBusy || !decisionReadyForHuman) return
    const dryRun = kind === 'preview'
    let status = kind === 'confirm' || kind === 'preview' ? 'confirmed' : kind === 'revision' ? 'needs_revision' : 'rejected'
    let reason = ''
    if (kind === 'revision' || kind === 'reject') {
      reason = window.prompt(kind === 'revision' ? '请填写要求修订的原因或补充项' : '请填写人工否决原因')?.trim() || ''
      if (!reason) {
        toast({ type: 'warning', title: '需要填写原因', description: '人工修订或否决必须留下审计原因。' })
        return
      }
    }
    if (kind === 'confirm' && !window.confirm('确认人工通过 R4 投决？该操作会写入投决确认和审计记录。')) return
    if (kind === 'preview') status = 'confirmed'
    setSpeakerMode('workflow')
    setTaskBusy(`human-${kind}`)
    setActionError('')
    try {
      const response = await confirmPrimaryMarketDecision(selectedDealId, {
        status,
        dry_run: dryRun,
        override_reason: reason || undefined,
        override_decision: undefined,
      })
      applyMeetingReadiness(response.readiness)
      const nextStatus = response.result?.human_confirmation?.status || status
      toast({
        type: dryRun ? 'info' : status === 'confirmed' ? 'success' : 'warning',
        title: dryRun ? '人工确认预演完成' : status === 'confirmed' ? '人工确认已通过' : '人工确认已写入',
        description: `${nextStatus}${response.result?.would_write ? ' · would_write' : ''}`,
      })
      await refreshMeeting()
    } catch (err) {
      const message = err instanceof Error ? err.message : '写入人工确认失败'
      setActionError(message)
      toast({ type: 'error', title: '人工确认失败', description: message })
    } finally {
      setTaskBusy('')
    }
  }

  const meetingQuickQuestionDefinitions = primaryMarketMeetingQuickQuestions(selectedAgent, speakerMode)
  const quickQuestions: ChatQuickQuestion[] = meetingQuickQuestionDefinitions.map((question) => ({
    key: `${activeLane}-${question.label}`,
    label: question.label,
    featured: question.featured,
    className: question.featured ? '' : 'text-primary',
    onClick: () => { sendMeetingMessage(question.prompt || question.label, question.label).catch(() => {}) },
  }))

  const emptyIntro = selectedDealId
    ? primaryMarketMeetingIntro(selectedAgent, speakerMode)
    : '请先选择一级市场项目，再进入对应智能体窗口发起对话。'

  const emptyDescription = (
    <div className="mb-6 flex max-w-xl flex-col items-center text-center">
      <p className="max-w-md text-base leading-7 text-text-muted">{emptyIntro}</p>
      {readinessLine ? <p className="mt-2 max-w-md text-xs text-text-muted">{readinessLine}</p> : null}
    </div>
  )

  return (
    <PageShell variant="secondary" className="space-y-5">
      <PageHeader
        icon={MessageSquareText}
        eyebrow="Primary Market IC Decision"
        title="投研决策"
        description="像主持投委会一样选择智能体窗口、上传材料、顺序发言，并把对话归档到投研决策记录。"
        actions={
          <Button type="button" variant="secondary" onClick={() => void refreshMeeting()} disabled={contextLoading || transcriptLoading || chatHistoryLoading || sessionsLoading || Boolean(taskBusy) || !selectedDealId}>
            {contextLoading || transcriptLoading || chatHistoryLoading || sessionsLoading ? <Loader2 className="animate-spin" /> : <RefreshCw />}
            刷新会议
          </Button>
        }
      />

      <PageSection title="会议控制台" compact>
        <div className="grid items-stretch gap-3 lg:grid-cols-[minmax(320px,1.35fr)_minmax(180px,0.65fr)_minmax(260px,0.9fr)]">
          <label className="flex min-w-0 flex-col justify-center rounded-md border border-border/70 bg-surface/70 px-4 py-3">
            <span className="mb-2 text-xs font-semibold text-text-muted">会议项目</span>
            <select
              value={selectedDealId}
              onChange={(event) => updateDealParam(setSearchParams, event.target.value)}
              disabled={dealsLoading || !deals.length || Boolean(chatBusy)}
              className="h-11 w-full min-w-0 rounded-md border border-input bg-background px-3 py-2 text-sm font-medium shadow-xs outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
              aria-label="选择会议项目"
            >
              <option value="">选择项目</option>
              {deals.map((deal) => <option key={deal.deal_id} value={deal.deal_id}>{deal.company_name || deal.deal_id}</option>)}
            </select>
            {dealsError ? <p className="text-xs text-destructive">{dealsError}</p> : null}
          </label>
          <Surface kind="muted" padding="sm" className="flex min-h-[92px] flex-col justify-center">
            <p className="text-xs font-medium text-text-muted">当前阶段</p>
            <p className="mt-2 text-lg font-semibold text-text">{phaseLabel(currentPhase)}</p>
            <p className="mt-1 truncate text-xs text-text-muted">{selectedDeal?.company_name || selectedDealId || '-'}</p>
          </Surface>
          <Surface kind="muted" padding="sm" className="flex min-h-[92px] flex-col justify-center">
            <p className="text-xs font-medium text-text-muted">决策窗口</p>
            <div className="mt-2 flex flex-wrap gap-2">
              <StatusBadge tone={chatBusy ? 'warning' : 'success'}>{chatBusy ? `${agentLabel(chatBusy)} 发言中` : activeWindowTitle}</StatusBadge>
              <StatusBadge tone="info">{activeLane}</StatusBadge>
            </div>
          </Surface>
        </div>
      </PageSection>

      {!selectedDealId ? (
        <PageSection>
          <EmptyState icon={MessageSquareText} title="请选择项目" description="选择项目后即可打开一级市场投研决策窗口。" />
        </PageSection>
      ) : error ? (
        <PageSection>
          <EmptyState title="会议状态加载失败" description={error} action={<Button onClick={() => void refreshMeeting()}>重试</Button>} />
        </PageSection>
      ) : contextLoading && !bundle.detail ? (
        <div className="grid gap-5 xl:grid-cols-[260px_minmax(0,1fr)]">
          <div className="h-96 animate-pulse rounded-lg bg-muted/60" />
          <div className="h-96 animate-pulse rounded-lg bg-muted/60" />
        </div>
      ) : (
        <div className={detailsMode ? 'space-y-4' : 'grid gap-5 xl:grid-cols-[280px_minmax(0,1fr)]'}>
          <div className={detailsMode ? 'hidden' : 'space-y-5'}>
            <PageSection title="R0-R4 议程" compact contentClassName="space-y-3">
              {agenda.map((item) => (
                <Surface key={item.phase} kind="row" padding="sm" className="relative overflow-hidden">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="font-semibold text-text">{item.label}</p>
                      {item.detail ? <p className="mt-1 break-words text-xs text-text-muted">{item.detail}</p> : null}
                    </div>
                    <StatusBadge tone={item.blocking ? 'error' : item.tone}>{text(item.status)}</StatusBadge>
                  </div>
                </Surface>
              ))}
            </PageSection>

            <PageSection title="快速入口" compact contentClassName="grid gap-2">
              <Button asChild variant="secondary" className="h-auto min-h-11 justify-between py-2.5">
                <Link to={`/primary-market/meeting?dealId=${encodeURIComponent(selectedDealId)}&view=readiness`}>
                  <span className="flex min-w-0 items-center gap-2"><Activity />智能体就绪与检索</span>
                  <StatusBadge tone={readinessRows.every((row) => row.readyForFormalTask) ? 'success' : 'warning'}>{readinessRows.filter((row) => row.readyForFormalTask).length}/{readinessRows.length}</StatusBadge>
                </Link>
              </Button>
              <Button asChild variant="secondary" className="h-auto min-h-11 justify-between py-2.5">
                <Link to={`/primary-market/meeting?dealId=${encodeURIComponent(selectedDealId)}&view=workflow-status`}>
                  <span className="flex min-w-0 items-center gap-2"><Network />正式工作流状态</span>
                  <StatusBadge tone={phaseObservability.some((row) => row.blocking) ? 'error' : 'info'}>{phaseObservability.filter((row) => row.blocking).length} 阻断</StatusBadge>
                </Link>
              </Button>
              <Button asChild variant="secondary" className="h-auto min-h-11 justify-between py-2.5">
                <Link to={`/primary-market/meeting?dealId=${encodeURIComponent(selectedDealId)}&view=handoff`}>
                  <span className="flex min-w-0 items-center gap-2"><ArrowRightLeft />智能体交接记录</span>
                  <StatusBadge tone={handoffRows.length ? 'success' : 'neutral'}>{handoffRows.length}</StatusBadge>
                </Link>
              </Button>
              <Button asChild variant="secondary" className="h-auto min-h-11 justify-between py-2.5">
                <Link to={`/primary-market/meeting?dealId=${encodeURIComponent(selectedDealId)}&view=quality`}>
                  <span className="flex min-w-0 items-center gap-2"><ShieldAlert />分歧与质量状态</span>
                  <StatusBadge tone={r4Quality.missingRequired.length ? 'error' : 'info'}>R1.5-R4</StatusBadge>
                </Link>
              </Button>
              <Button asChild variant="secondary" className="h-auto min-h-11 justify-between py-2.5">
                <Link to={`/primary-market/meeting?dealId=${encodeURIComponent(selectedDealId)}&view=minutes`}>
                  <span className="flex min-w-0 items-center gap-2"><MessageSquareText />会议纪要</span>
                  <StatusBadge tone="neutral">{laneEvents.length}</StatusBadge>
                </Link>
              </Button>
              <Button asChild variant="secondary" className="justify-start">
                <Link to={`/deals/${encodeURIComponent(selectedDealId)}/workflow`}><GitBranch />现有 Workflow</Link>
              </Button>
              <Button asChild variant="secondary" className="justify-start">
                <Link to={`/deals/${encodeURIComponent(selectedDealId)}/decision`}><FileCheck2 />现有投决页</Link>
              </Button>
            </PageSection>
          </div>

          <div className="space-y-4">
            {detailsMode ? <div className="mb-1 flex flex-wrap items-center justify-between gap-3 border-b border-border pb-4">
              <div><h2 className="text-lg font-semibold text-text">{detailView === 'minutes' ? '会议纪要' : '智能体与会议详情'}</h2><p className="mt-1 text-sm text-text-muted">按需查看会议状态，不干扰投研决策主流程。</p></div>
              <Button asChild variant="secondary"><Link to={`/primary-market/meeting?dealId=${encodeURIComponent(selectedDealId)}`}><MessageSquareText />返回投研决策</Link></Button>
            </div> : null}
            {detailsMode ? <div className="space-y-4">
            {Object.keys(partialErrors).length ? (
              <div className="rounded-lg border border-warning/30 bg-warning/5 p-3 text-sm text-warning">
                部分会议上下文加载失败：{Object.entries(partialErrors).map(([key, value]) => `${key}: ${value}`).join(' / ')}
              </div>
            ) : null}

            {detailView === 'readiness' ? <PageSection
              title="Agent Readiness 与双库检索"
              actions={<StatusBadge tone={readinessRows.every((row) => row.readyForFormalTask) ? 'success' : 'warning'}>{readinessRows.filter((row) => row.readyForFormalTask).length}/{readinessRows.length} ready</StatusBadge>}
            >
              <div className="divide-y divide-border/70">
                {readinessRows.map((row) => (
                  <div key={row.agentId} className="grid min-w-0 gap-3 py-4 first:pt-0 last:pb-0 lg:grid-cols-[minmax(150px,0.75fr)_minmax(180px,1fr)_minmax(180px,1fr)_minmax(170px,0.9fr)]">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-semibold text-text">{row.label}</p>
                      <p className="mt-1 truncate font-mono text-[11px] text-text-muted">{row.agentId}</p>
                      <div className="mt-2 flex flex-wrap gap-1.5">
                        <StatusBadge tone={row.runtimeTone}>gateway {row.runtimeHealth}</StatusBadge>
                        <StatusBadge tone={row.readyForFormalTask ? 'success' : 'error'}>{row.readyForFormalTask ? 'formal ready' : 'blocked'}</StatusBadge>
                        {row.stale ? <StatusBadge tone="error">stale</StatusBadge> : null}
                      </div>
                    </div>
                    <div className="min-w-0">
                      <p className="text-xs font-semibold text-text">项目 Evidence</p>
                      <p className="mt-1 break-all font-mono text-[11px] text-text-muted">{row.sharedCollection}</p>
                      <p className="mt-1 text-xs text-text-muted">{row.projectEvidenceHits} hits · snapshot {row.evidenceSnapshotHash ? row.evidenceSnapshotHash.slice(0, 12) : 'unavailable'}</p>
                    </div>
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <p className="text-xs font-semibold text-text">私有背景知识</p>
                        <StatusBadge tone={row.retrievalTone}>{row.retrievalStatus}</StatusBadge>
                      </div>
                      <p className="mt-1 break-all font-mono text-[11px] text-text-muted">{row.privateCollection}</p>
                      <p className="mt-1 text-xs text-text-muted">{row.backgroundKnowledgeHits} background hits</p>
                      {row.degradedReasons.length ? <p className="mt-1 break-words text-[11px] text-warning">{row.degradedReasons.join(' / ')}</p> : null}
                    </div>
                    <div className="min-w-0">
                      <p className="text-xs font-semibold text-text">Contract / Task / Quality</p>
                      <p className="mt-1 break-words text-[11px] text-text-muted">{row.contractVersion} · {row.phaseTaskStatus} · {row.qualityStatus}</p>
                      {row.capabilityRestrictions.length ? <p className="mt-1 break-words text-[11px] text-warning">限制：{row.capabilityRestrictions.join(' / ')}</p> : null}
                      {row.blockingReasons.length ? <p className="mt-1 break-words text-[11px] text-destructive">{row.blockingReasons.join(' / ')}</p> : null}
                    </div>
                  </div>
                ))}
              </div>
            </PageSection> : null}

            {detailView === 'workflow-status' ? <PageSection
              title="Hybrid DAG 正式工作流"
              actions={<StatusBadge tone={phaseObservability.some((row) => row.blocking) ? 'error' : 'info'}>{phaseObservability.filter((row) => row.blocking).length} blocking</StatusBadge>}
            >
              <div className="grid gap-2 md:grid-cols-2 2xl:grid-cols-3">
                {phaseObservability.map((row) => (
                  <div key={row.phase} className="min-w-0 rounded-md border border-border/70 bg-muted/20 p-3">
                    <div className="flex min-w-0 flex-wrap items-center gap-2">
                      <StatusBadge tone={row.tone}>{row.phase}</StatusBadge>
                      <p className="min-w-0 truncate text-sm font-semibold text-text">{row.label}</p>
                      <StatusBadge tone={row.blocking ? 'error' : row.tone}>{row.status}</StatusBadge>
                    </div>
                    <p className="mt-2 break-words text-xs text-text-muted">{row.detail}</p>
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {row.deterministicFallback ? <StatusBadge tone="warning">deterministic fallback</StatusBadge> : null}
                      <StatusBadge tone={row.generationMode === 'unavailable' ? 'warning' : row.deterministicFallback ? 'warning' : 'info'}>{row.generationMode}</StatusBadge>
                    </div>
                  </div>
                ))}
              </div>
            </PageSection> : null}

            {detailView === 'handoff' ? <PageSection
              title="结构化 Agent Handoff"
              actions={<StatusBadge tone={handoffRows.length ? 'success' : 'warning'}>{handoffRows.length} persisted</StatusBadge>}
            >
              {handoffRows.length ? (
                <div className="divide-y divide-border/70">
                  {handoffRows.map((row) => (
                    <div key={row.id} className="grid min-w-0 gap-2 py-3 first:pt-0 last:pb-0 md:grid-cols-[70px_minmax(0,1fr)_minmax(160px,0.8fr)] md:items-center">
                      <StatusBadge tone="info">{row.phase}</StatusBadge>
                      <div className="min-w-0">
                        <p className="truncate text-sm font-semibold text-text">{row.fromLabel} → {row.toLabel}</p>
                        <p className="mt-1 truncate font-mono text-[11px] text-text-muted">{row.id}</p>
                      </div>
                      <div className="min-w-0 text-[11px] text-text-muted">
                        <p className="truncate font-mono">digest {row.inputDigest || 'unavailable'}</p>
                        <p className="mt-1 truncate">run {row.workflowRunId || 'unavailable'}{row.createdAt ? ` · ${row.createdAt}` : ''}</p>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-sm text-text-muted">尚无已持久化 handoff；正式智能体任务只应读取经合同校验的 handoff 输入。</p>
              )}
            </PageSection> : null}

            {detailView === 'quality' ? <PageSection title="R1.5 分歧 · R2 Delta · R3 Timeline · R4 Quality" contentClassName="divide-y divide-border/70">
              <section className="pb-5">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h3 className="text-sm font-semibold text-text">R1.5 分歧与主席裁决</h3>
                  <StatusBadge tone={disputeBoard.some((item) => !item.resolved) ? 'warning' : disputeBoard.length ? 'success' : 'neutral'}>{disputeBoard.length} disputes</StatusBadge>
                </div>
                {disputeBoard.length ? (
                  <div className="mt-3 grid gap-2 lg:grid-cols-2">
                    {disputeBoard.map((item) => (
                      <div key={item.id} className="min-w-0 rounded-md border border-border/70 p-3">
                        <div className="flex min-w-0 flex-wrap items-center gap-2">
                          <StatusBadge tone={item.resolved ? 'success' : item.severity === 'critical' || item.severity === 'high' ? 'error' : 'warning'}>{item.severity}</StatusBadge>
                          <p className="min-w-0 flex-1 truncate text-sm font-semibold text-text">{item.topic}</p>
                          <StatusBadge tone={item.resolved ? 'success' : 'warning'}>{item.resolved ? 'resolved' : 'open'}</StatusBadge>
                        </div>
                        <p className="mt-2 text-xs text-text-muted">{item.positionCount} positions · ruling {item.ruling}</p>
                        <p className="mt-1 break-all text-[11px] text-text-muted">Evidence: {item.evidenceIds.join(' / ') || 'unavailable'}</p>
                        {item.followups.length ? <p className="mt-1 break-words text-[11px] text-warning">Follow-up: {item.followups.join(' / ')}</p> : null}
                        <div className="mt-2 flex flex-wrap gap-1.5">{item.fallback ? <StatusBadge tone="warning">deterministic fallback</StatusBadge> : null}<StatusBadge tone={item.generationMode === 'unavailable' ? 'warning' : 'info'}>{item.generationMode}</StatusBadge></div>
                      </div>
                    ))}
                  </div>
                ) : <p className="mt-2 text-xs text-text-muted">尚无结构化分歧产物</p>}
              </section>

              <section className="py-5">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h3 className="text-sm font-semibold text-text">R2 观点与评分 Delta</h3>
                  <StatusBadge tone={r2DeltaRows.some((item) => item.status === 'warn') ? 'warning' : r2DeltaRows.length ? 'success' : 'neutral'}>{r2DeltaRows.length} reports</StatusBadge>
                </div>
                {r2DeltaRows.length ? (
                  <div className="mt-3 divide-y divide-border/60">
                    {r2DeltaRows.map((row) => (
                      <div key={row.agentId} className="grid min-w-0 gap-2 py-3 first:pt-0 last:pb-0 sm:grid-cols-[minmax(130px,0.7fr)_minmax(170px,0.8fr)_minmax(0,1.4fr)]">
                        <div className="min-w-0"><p className="truncate text-xs font-semibold text-text">{row.label}</p><StatusBadge className="mt-1" tone={statusTone(row.status)}>{row.status}</StatusBadge></div>
                        <div className="text-xs text-text-muted">R1 {row.r1Score ?? '-'} → R2 {row.r2Score ?? '-'} <span className={row.scoreChange !== null && row.scoreChange < 0 ? 'text-destructive' : 'text-primary'}>({row.scoreChange === null ? '-' : row.scoreChange > 0 ? `+${row.scoreChange}` : row.scoreChange})</span><p className="mt-1">{row.revisions} revisions · {row.recommendation || 'no recommendation'}</p></div>
                        <p className="min-w-0 break-words text-xs leading-5 text-text-muted">{row.summary || 'Delta rationale unavailable'}</p>
                      </div>
                    ))}
                  </div>
                ) : <p className="mt-2 text-xs text-text-muted">尚无 R2 delta 数据</p>}
              </section>

              <section className="py-5">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h3 className="text-sm font-semibold text-text">R3 对抗时间线</h3>
                  <StatusBadge tone={bundle.r3Review?.skipped ? 'warning' : r3Timeline.length ? 'success' : 'neutral'}>{bundle.r3Review?.skipped ? 'skipped' : `${r3Timeline.length} turns`}</StatusBadge>
                </div>
                {bundle.r3Review?.skipped ? <p className="mt-2 break-words text-xs text-warning">{bundle.r3Review.skip_reason || 'skip reason unavailable'}</p> : null}
                {r3Timeline.length ? (
                  <div className="mt-3 space-y-2 border-l-2 border-border pl-4">
                    {r3Timeline.map((row) => (
                      <div key={row.id} className="relative min-w-0 rounded-md bg-muted/25 p-3 before:absolute before:-left-[21px] before:top-4 before:h-2.5 before:w-2.5 before:rounded-full before:bg-primary">
                        <div className="flex min-w-0 flex-wrap items-center gap-2"><StatusBadge tone={statusTone(row.status)}>{row.stance}</StatusBadge><span className="truncate text-xs font-semibold text-text">{row.label}</span><span className="text-[11px] text-text-muted">{row.challengeCount} challenges · {row.evidenceCount} Evidence</span>{row.fallback ? <StatusBadge tone="warning">deterministic fallback</StatusBadge> : null}</div>
                        <p className="mt-1 break-words text-xs leading-5 text-text-muted">{row.summary || 'Turn detail unavailable'}</p>
                      </div>
                    ))}
                  </div>
                ) : !bundle.r3Review?.skipped ? <p className="mt-2 text-xs text-text-muted">尚无可回放的 R3 turn</p> : null}
              </section>

              <section className="pt-5">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h3 className="text-sm font-semibold text-text">R4 报告质量与人工确认</h3>
                  <div className="flex flex-wrap gap-1.5"><StatusBadge tone={statusTone(r4Quality.qualityStatus)}>quality {r4Quality.qualityStatus}</StatusBadge><StatusBadge tone={statusTone(r4Quality.factcheckStatus)}>factcheck {r4Quality.factcheckStatus}</StatusBadge><StatusBadge tone={r4Quality.humanConfirmed ? 'success' : 'warning'}>human {r4Quality.humanStatus}</StatusBadge><StatusBadge tone={r4Quality.attestationStatus === 'bound' ? 'success' : r4Quality.attestationStatus === 'incomplete' ? 'error' : 'neutral'}>attestation {r4Quality.attestationStatus}</StatusBadge></div>
                </div>
                <div className="mt-3 flex flex-wrap gap-1.5">{r4Quality.fallback ? <StatusBadge tone="warning">deterministic fallback</StatusBadge> : null}<StatusBadge tone={r4Quality.generationMode === 'unavailable' ? 'warning' : 'info'}>{r4Quality.generationMode}</StatusBadge>{r4Quality.reportPath ? <StatusBadge tone="success">report available</StatusBadge> : <StatusBadge tone="warning">report unavailable</StatusBadge>}</div>
                {r4Quality.reportId || r4Quality.workflowRunId ? <p className="mt-2 break-all text-[11px] text-text-muted">{r4Quality.reportId || 'report unavailable'}{r4Quality.reportRevision ? ` r${r4Quality.reportRevision}` : ''}{r4Quality.workflowRunId ? ` · ${r4Quality.workflowRunId}` : ''}</p> : null}
                {r4Quality.missingRequired.length ? <p className="mt-2 break-words text-xs text-destructive">Required missing: {r4Quality.missingRequired.join(' / ')}</p> : null}
                {r4Quality.missingAdvisory.length ? <p className="mt-1 break-words text-xs text-warning">Advisory missing: {r4Quality.missingAdvisory.join(' / ')}</p> : null}
                {r4Quality.findings.length ? <div className="mt-2 grid gap-1">{r4Quality.findings.slice(0, 6).map((finding, index) => <p key={`${finding}-${index}`} className="break-words text-xs text-warning">{finding}</p>)}</div> : null}
              </section>
            </PageSection> : null}
            {detailView === 'minutes' ? <PageSection title="会议纪要" actions={<StatusBadge tone="neutral">{laneEvents.length} events</StatusBadge>}>
              {laneEvents.length ? <div className="space-y-2">{[...laneEvents].reverse().map((event) => (
                <div key={event.id} className="rounded-md border border-border/70 bg-surface/70 p-3">
                  <div className="flex min-w-0 flex-wrap items-center gap-2"><StatusBadge tone={event.tone}>{phaseLabel(event.phase)}</StatusBadge><span className="font-semibold text-text">{event.speaker}</span><span className="text-sm text-text-muted">{event.title}</span><span className="ml-auto text-xs text-text-muted">{formatTime(event.createdAt)}</span></div>
                  {event.body ? <p className="mt-2 whitespace-pre-line text-sm leading-6 text-text-muted">{event.body}</p> : null}
                </div>
              ))}</div> : <EmptyState icon={MessageSquareText} title="暂无会议纪要" description="会议发言和工作流事件将在这里集中展示。" />}
            </PageSection> : null}
            </div> : null}

            {!detailsMode ? <PageSection
              title="投委会会议室"
              actions={<StatusBadge tone={statusTone(bundle.workflow?.workflow.status)}>{text(bundle.workflow?.workflow.status, '未加载')}</StatusBadge>}
              contentClassName="space-y-4"
            >
              <div className="grid gap-3 xl:grid-cols-[minmax(220px,0.8fr)_minmax(0,1fr)]">
                <label className="min-w-0 space-y-1.5">
                  <span className="text-xs font-semibold uppercase tracking-wide text-text-muted">Agent</span>
                  <select
                    value={selectedAgent}
                    onChange={(event) => {
                      setSpeakerMode('single')
                      setSelectedAgent(event.target.value)
                    }}
                    className="h-10 w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-xs outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
                    disabled={chatBusy !== ''}
                  >
                    {IC_AGENT_OPTIONS.map((agent) => <option key={agent.value} value={agent.value}>{agent.label} ({agent.value})</option>)}
                  </select>
                </label>
                <div className="min-w-0 space-y-1.5">
                  <span className="text-xs font-semibold uppercase tracking-wide text-text-muted">Mode</span>
                  <div className="flex flex-wrap gap-2">
                    <Button type="button" size="sm" variant={speakerMode === 'single' ? 'default' : 'secondary'} onClick={() => setSpeakerMode('single')} disabled={chatBusy !== ''}>
                      <MessageSquareText />
                      点名对话
                    </Button>
                    <Button type="button" size="sm" variant={speakerMode === 'committee' ? 'default' : 'secondary'} onClick={() => setSpeakerMode('committee')} disabled={chatBusy !== ''}>
                      <UsersRound />
                      全体委员
                    </Button>
                    <Button type="button" size="sm" variant={speakerMode === 'workflow' ? 'default' : 'secondary'} onClick={() => setSpeakerMode('workflow')} disabled={chatBusy !== ''}>
                      <GitBranch />
                      总协调员工作流
                    </Button>
                  </div>
                </div>
              </div>

              {speakerMode === 'committee' ? (
                <div className="flex flex-wrap gap-2">
                  {committeeAgentOptions.map((agent) => {
                    const selected = selectedSpeakerSet.has(agent.value)
                    return (
                      <button
                        key={agent.value}
                        type="button"
                        onClick={() => toggleCommitteeSpeaker(agent.value)}
                        disabled={chatBusy !== ''}
                        className={`min-h-9 rounded-md border px-3 text-sm font-semibold transition-colors ${selected ? 'border-primary/40 bg-primary/10 text-primary' : 'border-border bg-muted/40 text-text-muted hover:bg-muted'}`}
                      >
                        {agent.label}
                      </button>
                    )
                  })}
                </div>
              ) : null}

              <div className="flex flex-col gap-3 border-y border-border/70 py-3 xl:flex-row xl:items-center xl:justify-between">
                <div className="flex min-w-0 flex-wrap gap-2">
                  {readinessChips.map((chip) => (
                    <span key={chip.id} title={chip.detail}>
                      <StatusBadge tone={chip.tone}>{chip.label}</StatusBadge>
                    </span>
                  ))}
                </div>
                <div className="flex flex-wrap gap-2">
                  <span title={preparationPlan.reason}>
                    <StatusBadge tone="info">检索目标 {preparationPlan.roundName} · {preparationPlan.profileIds.length} roles</StatusBadge>
                  </span>
                  <Button
                    type="button"
                    size="sm"
                    variant="secondary"
                    onClick={() => { prepareSelectedAgent().catch(() => {}) }}
                    disabled={Boolean(chatBusy || taskBusy || !selectedDealId || !selectedAgentCanPrepare)}
                    title={selectedAgentCanPrepare ? `${preparationPlan.label} · include_vector=true` : `${agentLabel(selectedAgent)}不参与${preparationPlan.label}`}
                  >
                    {taskBusy === 'prepare-agent' ? <Loader2 className="animate-spin" /> : <FileCheck2 />}
                    准备 {preparationPlan.roundName} 智能体
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="secondary"
                    onClick={() => { prepareCommittee().catch(() => {}) }}
                    disabled={Boolean(chatBusy || taskBusy || !selectedDealId || !preparationPlan.profileIds.length)}
                    title={`${preparationPlan.reason} include_vector=true`}
                  >
                    {taskBusy === 'prepare-committee' ? <Loader2 className="animate-spin" /> : <UsersRound />}
                    准备 {preparationPlan.roundName} 参与角色
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="secondary"
                    onClick={() => { runCurrentAgentR1(true).catch(() => {}) }}
                    disabled={Boolean(chatBusy || taskBusy || !selectedDealId || !selectedAgentCanRunR1)}
                    title={selectedAgentCanRunR1 ? '预演当前智能体 R1 正式任务' : '总协调员不执行 R1 agent 任务'}
                  >
                    {taskBusy === 'r1-agent-dry-run' ? <Loader2 className="animate-spin" /> : <Eye />}
                    预演当前 R1
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="secondary"
                    onClick={() => { runCurrentAgentR1(false).catch(() => {}) }}
                    disabled={Boolean(chatBusy || taskBusy || !selectedDealId || !selectedAgentCanRunR1 || !selectedAgentReadyForR1)}
                    title={selectedAgentReadyForR1 ? '执行当前智能体正式 R1 任务' : selectedReadinessRow?.blockingReasons.join(' / ') || '当前智能体 readiness 未通过'}
                  >
                    {taskBusy === 'r1-agent-execute' ? <Loader2 className="animate-spin" /> : <Play />}
                    执行当前 R1
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="secondary"
                    onClick={() => { runR1Serial().catch(() => {}) }}
                    disabled={Boolean(chatBusy || taskBusy || !selectedDealId || !r1CommitteeReady)}
                    title={r1CommitteeReady ? '执行 R1 串行正式任务' : 'R1 委员 readiness 未全部通过'}
                  >
                    {taskBusy === 'r1-serial-execute' ? <Loader2 className="animate-spin" /> : <UsersRound />}
                    执行 R1 串行
                  </Button>
                  <Button type="button" size="sm" variant="secondary" onClick={() => { advanceWorkflow(true).catch(() => {}) }} disabled={Boolean(chatBusy || taskBusy || !selectedDealId)}>
                    {taskBusy === 'workflow-dry-run' ? <Loader2 className="animate-spin" /> : <GitBranch />}
                    预演下一步（模型）
                  </Button>
                  <Button type="button" size="sm" variant="secondary" onClick={() => { advanceWorkflow(false).catch(() => {}) }} disabled={Boolean(chatBusy || taskBusy || !selectedDealId)}>
                    {taskBusy === 'workflow-execute' ? <Loader2 className="animate-spin" /> : <GitBranch />}
                    执行下一步（Hermes）
                  </Button>
                  <Button type="button" size="sm" variant="secondary" onClick={() => void refreshMeeting()} disabled={contextLoading || transcriptLoading || chatHistoryLoading || sessionsLoading || Boolean(taskBusy) || !selectedDealId}>
                    {contextLoading || transcriptLoading || chatHistoryLoading || sessionsLoading ? <Loader2 className="animate-spin" /> : <RefreshCw />}
                    刷新状态
                  </Button>
                </div>
              </div>

              {decisionReadyForHuman ? (
                <div className="flex flex-col gap-3 rounded-md border border-border/70 bg-muted/20 p-3 lg:flex-row lg:items-center lg:justify-between">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="text-sm font-semibold text-text">R4 人工确认</p>
                      <StatusBadge tone={humanConfirmed ? 'success' : statusTone(humanStatus)}>{humanStatus}</StatusBadge>
                    </div>
                    <p className="mt-1 truncate text-xs text-text-muted">{bundle.decision?.report_path || decisionContract?.artifacts?.markdown?.path || 'R4 decision ready'}</p>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <Button type="button" size="sm" variant="secondary" onClick={() => { writeHumanConfirmation('preview').catch(() => {}) }} disabled={Boolean(chatBusy || taskBusy || !selectedDealId)}>
                      {taskBusy === 'human-preview' ? <Loader2 className="animate-spin" /> : <Eye />}
                      预演
                    </Button>
                    <Button type="button" size="sm" variant="secondary" onClick={() => { writeHumanConfirmation('revision').catch(() => {}) }} disabled={Boolean(chatBusy || taskBusy || !selectedDealId || humanConfirmed)}>
                      {taskBusy === 'human-revision' ? <Loader2 className="animate-spin" /> : <ShieldAlert />}
                      要求修订
                    </Button>
                    <Button type="button" size="sm" variant="secondary" onClick={() => { writeHumanConfirmation('confirm').catch(() => {}) }} disabled={Boolean(chatBusy || taskBusy || !selectedDealId || humanConfirmed)}>
                      {taskBusy === 'human-confirm' ? <Loader2 className="animate-spin" /> : <CheckCircle2 />}
                      人工确认通过
                    </Button>
                    <Button type="button" size="sm" variant="secondary" onClick={() => { writeHumanConfirmation('reject').catch(() => {}) }} disabled={Boolean(chatBusy || taskBusy || !selectedDealId || humanConfirmed)}>
                      {taskBusy === 'human-reject' ? <Loader2 className="animate-spin" /> : <XCircle />}
                      人工否决
                    </Button>
                  </div>
                </div>
              ) : null}

              <ChatShell
                className="primary-market-meeting-chat chat-page-shell premium-shell h-[calc(100dvh-220px)] min-h-[620px] max-h-[980px] rounded-lg border border-border bg-white/68 lg:min-h-[720px]"
                header={
                  <ChatHeader
                    className="primary-market-meeting-chat-header gap-3 border-b border-border/80 bg-white/54 px-4 py-3 backdrop-blur"
                    leadingClassName="flex min-w-0 flex-1 items-center gap-3"
                    avatar={<Bot className="h-5 w-5" />}
                    avatarClassName="premium-icon h-10 w-10 shrink-0 rounded-xl"
                    title={<p className="truncate text-base font-semibold text-text">{activeWindowTitle}</p>}
                    subtitle={`Session: ${currentSessionId ? currentSessionId.slice(-16) : 'new'} · Lane: ${activeLane} · Hermes IC`}
                    actionsClassName="flex shrink-0 items-center justify-end gap-1 sm:gap-2"
                    actions={
                      <>
                        <StatusBadge tone={chatBusy ? 'warning' : 'success'}>{chatBusy ? `${agentLabel(chatBusy)} 输出中` : 'ready'}</StatusBadge>
                        <button
                          onClick={() => { createNewChat().catch((err) => setActionError(err instanceof Error ? err.message : '新建会话失败')) }}
                          disabled={Boolean(chatBusy)}
                          className="inline-flex h-11 w-11 min-w-11 items-center justify-center gap-1.5 rounded-xl border border-border bg-white/78 px-0 text-xs font-semibold text-text shadow-sm hover:bg-white disabled:opacity-50 sm:w-auto sm:px-3"
                          aria-label="新建会话"
                          title="新建会话"
                        >
                          <Plus className="h-3.5 w-3.5" /><span className="hidden sm:inline">新建会话</span>
                        </button>
                        <button
                          onClick={() => { showHistory().catch((err) => setActionError(err instanceof Error ? err.message : '查看历史失败')) }}
                          className="inline-flex h-11 w-11 min-w-11 items-center justify-center gap-1.5 rounded-xl border border-border bg-white/78 px-0 text-xs font-semibold text-text shadow-sm hover:bg-white sm:w-auto sm:px-3"
                          aria-label="查看历史"
                          title="查看历史"
                        >
                          <History className="h-3.5 w-3.5" /><span className="hidden sm:inline">查看历史</span>
                        </button>
                        <button
                          onClick={() => setClearConfirmOpen(true)}
                          disabled={Boolean(chatBusy)}
                          className="inline-flex h-11 w-11 min-w-11 items-center justify-center gap-1.5 rounded-xl border border-border bg-white/78 px-0 text-xs font-semibold text-text shadow-sm hover:bg-white disabled:opacity-50 sm:w-auto sm:px-3"
                          aria-label="删除历史"
                          title="删除历史"
                        >
                          <Trash2 className="h-3.5 w-3.5" /><span className="hidden sm:inline">删除历史</span>
                        </button>
                      </>
                    }
                  />
                }
                history={
                  <SessionHistoryList
                    sessions={chatSessions}
                    loading={sessionsLoading}
                    loaded
                    onSelect={(sessionId) => openSession(sessionId).catch((err) => setActionError(err instanceof Error ? err.message : '打开历史会话失败'))}
                    onClose={() => setHistoryOpen(false)}
                    open={historyOpen}
                  />
                }
                messages={
                  <ChatMessageList
                    messages={messages}
                    endRef={messagesEnd}
                    emptyAvatar={<div className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl border border-border bg-surface text-primary"><Bot className="h-7 w-7" /></div>}
                    emptyDescription={emptyDescription}
                    quickQuestions={quickQuestions}
                    quickQuestionClassName="primary-market-quick-question-cloud"
                    notice={chatHistoryLoading ? '正在同步会话历史…' : transcriptLoading ? '正在同步会议纪要…' : historyNotice || null}
                    onCopyMessage={copyMessage}
                    renderMessageHeader={(msg) => <IcMessageIdentity message={msg} mode={speakerMode} selectedAgent={selectedAgent} />}
                    renderProgress={(msg) => msg.progress && msg.progress.status !== 'completed' ? <AgentProgressCard progress={msg.progress} compact /> : null}
                    listClassName="chat-page-message-list primary-market-meeting-chat-list w-full"
                    messageGapClassName="space-y-4"
                  />
                }
                messagesClassName="chat-page-messages primary-market-meeting-chat-messages flex-1 overflow-y-auto px-4 py-4 sm:px-5 lg:px-6"
                composer={
                  <div className="chat-page-composer primary-market-meeting-composer w-full">
                    <ChatComposer
                      input={meetingInput}
                      setInput={setMeetingInput}
                      composing={composing}
                      setComposing={setComposing}
                      sending={Boolean(chatBusy)}
                      uploadingAttachments={uploadingAttachments}
                      attachments={attachments}
                      textareaRef={textareaRef}
                      fileInputRef={fileInputRef}
                      onSend={() => { sendMeetingMessage().catch(() => {}) }}
                      onStop={stopChat}
                      onNewChat={() => { createNewChat().catch((err) => setActionError(err instanceof Error ? err.message : '新建会话失败')) }}
                      onAttachmentChange={(files) => { handleAttachmentChange(files).catch(() => {}) }}
                      onRemoveAttachment={(id) => setAttachments((current) => current.filter((item) => item.id !== id))}
                      placeholder={speakerMode === 'workflow' ? '交给总协调员判断下一步 R0-R4 工作流...' : speakerMode === 'committee' ? '向全体委员提出同一个问题...' : `向 ${agentLabel(selectedAgent)} 提问...`}
                      showNewChat={false}
                    />
                  </div>
                }
                composerClassName="primary-market-meeting-composer-section chat-composer-section"
                clearDialog={
                  <ClearChatConfirmDialog
                    open={clearConfirmOpen}
                    disabled={Boolean(chatBusy)}
                    onOpenChange={setClearConfirmOpen}
                    onConfirm={deleteHistory}
                  />
                }
              />

              {actionError ? <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">{actionError}</div> : null}
            </PageSection> : null}
          </div>
        </div>
      )}
    </PageShell>
  )
}
