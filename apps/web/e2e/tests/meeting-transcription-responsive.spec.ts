import { expect, test, type Page, type Route, type TestInfo } from '@playwright/test'

import { e2eUser } from '../support/mockApi'
import { TRANSCRIPT_MAX_RENDERED_SEGMENTS } from '../../src/features/meeting-transcription/transcriptVirtualization'

const viewports = [
  { name: 'mobile-375', width: 375, height: 812, workspace: 'mobile' },
  { name: 'tablet-768', width: 768, height: 1024, workspace: 'mobile' },
  { name: 'compact-desktop-1024', width: 1024, height: 768, workspace: 'compact' },
  { name: 'desktop-1440', width: 1440, height: 900, workspace: 'wide' },
  { name: 'wide-1920', width: 1920, height: 1080, workspace: 'wide' },
] as const

const meeting = {
  id: 'meeting-review-1',
  owner_user_id: 1,
  title: '产品与投研周会',
  language: 'zh-CN',
  state: 'stopped',
  postprocess_state: 'succeeded',
  audio_source: 'microphone',
  voiceprint_enabled: true,
  ai_enabled: true,
  selection_mode: 'pinned',
  requested_model_ref: 'meeting:local:test123456789',
  fallback_policy: 'disabled',
  settings_version: 2,
  version: 4,
  stream_epoch: 1,
  last_audio_sequence: 24,
  last_segment_ordinal: 2,
  active_lexicon_version: 3,
  started_at: '2026-07-13T01:00:00Z',
  stopped_at: '2026-07-13T01:42:00Z',
  created_at: '2026-07-13T00:58:00Z',
  updated_at: '2026-07-13T01:45:00Z',
  duration_ms: 2_520_000,
  speaker_count: 2,
  participant_count: 2,
  model_label: '本地会议模型',
  model_locality: 'local',
}

const liveMeeting = {
  ...meeting,
  id: 'meeting-live-1',
  title: '实时经营分析会',
  state: 'draft',
  postprocess_state: 'not_started',
  started_at: null,
  stopped_at: null,
  duration_ms: 0,
  version: 1,
  stream_epoch: 0,
  last_audio_sequence: -1,
}

const speakers = [
  {
    id: 'speaker-1',
    meeting_id: meeting.id,
    anonymous_label: '发言人 1',
    display_name: '张明',
    label_source: 'manual',
    version: 2,
  },
  {
    id: 'speaker-2',
    meeting_id: meeting.id,
    anonymous_label: '发言人 2',
    display_name: '李然',
    label_source: 'voiceprint_confirmed',
    version: 1,
  },
]

const segments = [
  {
    id: 'segment-1',
    meeting_id: meeting.id,
    ordinal: 1,
    utterance_id: 'utterance-1',
    start_ms: 12_000,
    end_ms: 18_000,
    speaker_track_id: 'speaker-1',
    speaker_display_name: '张明',
    raw_text: '本季度收入质量明显改善。',
    asr_final_text: '本季度收入质量明显改善。',
    display_text: '本季度收入质量明显改善。',
    revision_no: 1,
    text_state: 'stable',
    human_locked: false,
  },
  {
    id: 'segment-2',
    meeting_id: meeting.id,
    ordinal: 2,
    utterance_id: 'utterance-2',
    start_ms: 22_000,
    end_ms: 29_000,
    speaker_track_id: 'speaker-2',
    speaker_display_name: '李然',
    raw_text: '下周完成客户留存率复核。',
    asr_final_text: '下周完成客户留存率复核。',
    display_text: '下周完成客户留存率复核。',
    revision_no: 2,
    text_state: 'human_verified',
    human_locked: true,
  },
]

const minutesArtifact = {
  id: 'artifact-minutes-1',
  meeting_id: meeting.id,
  artifact_type: 'final_minutes',
  version: 1,
  state: 'ready',
  content_text: '收入质量改善；下周复核客户留存率。',
  content_json: {
    schema_version: 'siq.meeting.final_minutes.v1',
    overview: '收入质量改善，下周继续复核客户留存率。',
    agenda_topics: [],
    chapters: [],
    decisions: [{ text: '继续执行当前收入质量方案。', source_segment_ids: ['segment-1'] }],
    open_questions: [],
    risks: [],
    action_items: [{ text: '完成客户留存率复核。', owner: '李然', due_date: '2026-07-20', status: 'confirmed', source_segment_ids: ['segment-2'] }],
    speaker_viewpoints: [{ text: '本季度收入质量明显改善。', speaker: '张明', source_segment_ids: ['segment-1'] }],
    keywords: [{ text: '客户留存率', source_segment_ids: ['segment-2'] }],
  },
  generated_at: '2026-07-13T01:45:00Z',
}

function json(body: unknown, status = 200) {
  return { status, contentType: 'application/json', body: JSON.stringify(body) }
}

function silentWav(durationMs = 250) {
  const dataSize = Math.round(16_000 * 2 * durationMs / 1_000)
  const buffer = Buffer.alloc(44 + dataSize)
  buffer.write('RIFF', 0)
  buffer.writeUInt32LE(36 + dataSize, 4)
  buffer.write('WAVEfmt ', 8)
  buffer.writeUInt32LE(16, 16)
  buffer.writeUInt16LE(1, 20)
  buffer.writeUInt16LE(1, 22)
  buffer.writeUInt32LE(16_000, 24)
  buffer.writeUInt32LE(32_000, 28)
  buffer.writeUInt16LE(2, 32)
  buffer.writeUInt16LE(16, 34)
  buffer.write('data', 36)
  buffer.writeUInt32LE(dataSize, 40)
  return buffer
}

interface MeetingMockOptions {
  audioTicketFailures?: number
  transcriptSegments?: Array<Record<string, unknown>>
  lastSegmentOrdinal?: number
  durableEvents?: Array<Record<string, unknown> & { cursor: number }>
}

async function mockMeetingApis(page: Page, options: MeetingMockOptions = {}) {
  let audioTicketAttempts = 0
  const transcriptSegments = options.transcriptSegments || segments
  const lastSegmentOrdinal = options.lastSegmentOrdinal ?? Number(transcriptSegments.at(-1)?.ordinal || 0)
  const reviewMeeting = { ...meeting, last_segment_ordinal: lastSegmentOrdinal }
  const currentLiveMeeting = { ...liveMeeting, last_segment_ordinal: lastSegmentOrdinal }
  const requestCounts = {
    capabilities: 0,
    transcriptAfterOrdinals: [] as number[],
    speakerRenameBodies: [] as Array<Record<string, unknown>>,
  }
  await page.addInitScript((user) => {
    window.localStorage.setItem('access_token', 'playwright-token')
    window.localStorage.setItem('user', JSON.stringify(user))
    window.localStorage.setItem('theme', 'light')
  }, e2eUser)

  await page.route('**/*', async (route: Route) => {
    const request = route.request()
    const requestUrl = new URL(request.url())
    const path = requestUrl.pathname
    if (!path.startsWith('/api/')) {
      await route.continue()
      return
    }
    if (path === '/api/auth/me') return route.fulfill(json(e2eUser))
    if (path === '/api/meetings/v1/capabilities') {
      requestCounts.capabilities += 1
      return route.fulfill(json({
        schema_version: 'meeting.v1',
        enabled: true,
        configuration_errors: [],
        audio: { codec: 'pcm_s16le', sample_rate: 16_000, channels: 1 },
        asr: { available: true, languages: ['zh-CN'], timestamps: true, speaker_tracks: true },
        voiceprint: { available: true, scope: 'user_private', auto_match: false },
        ai: { available: true, model_catalog_runtime: true },
        recording_import: {
          available: true,
          formats: ['wav', 'flac', 'mp3', 'm4a', 'webm', 'ogg'],
          resumable: true,
          max_file_bytes: 4_294_967_296,
          max_duration_seconds: 14_400,
          min_chunk_bytes: 262_144,
          max_chunk_bytes: 16_777_216,
        },
        limits: { max_duration_seconds: 14_400, max_chunk_bytes: 262_144, reconnect_window_seconds: 60 },
        supported_audio_sources: ['microphone', 'import'],
      }))
    }
    if (path === '/api/meetings/v1/sessions' && request.method() === 'GET') {
      return route.fulfill(json({ items: [currentLiveMeeting, reviewMeeting], total: 2, offset: 0, limit: 20 }))
    }
    if (path === `/api/meetings/v1/sessions/${meeting.id}`) return route.fulfill(json(reviewMeeting))
    if (path === `/api/meetings/v1/sessions/${liveMeeting.id}`) return route.fulfill(json(currentLiveMeeting))
    if (request.method() === 'PATCH' && new RegExp('/segments/[^/]+/speaker$').test(path)) {
      const body = request.postDataJSON() as { display_name: string; scope: 'segment' | 'speaker'; expected_speaker_version: number }
      requestCounts.speakerRenameBodies.push(body)
      const segmentId = decodeURIComponent(path.split('/').at(-2) || '')
      const currentSegment = transcriptSegments.find((item) => item.id === segmentId) || transcriptSegments[0]
      const source = speakers.find((item) => item.id === currentSegment?.speaker_track_id) || speakers[0]
      const sourceSegmentCount = transcriptSegments.filter((item) => item.speaker_track_id === source.id).length
      const updatedSource = { ...source, version: source.version + 1 }
      if (body.scope === 'speaker') {
        const renamed = { ...updatedSource, display_name: body.display_name, label_source: 'manual' }
        return route.fulfill(json({
          operation: 'rename_speaker',
          scope: 'speaker',
          affected_segment_count: sourceSegmentCount,
          event_id: 'speaker-rename-all',
          event_cursor: 9,
          tracks: [renamed],
          segment: { ...currentSegment, speaker_label: body.display_name },
        }))
      }
      const target = {
        ...source,
        id: `manual-${segmentId}`,
        anonymous_label: '发言人 3',
        display_name: body.display_name,
        label_source: 'manual',
        version: 1,
      }
      return route.fulfill(json({
        operation: 'rename_segment',
        scope: 'segment',
        affected_segment_count: 1,
        event_id: 'speaker-rename-one',
        event_cursor: 10,
        tracks: [updatedSource, target],
        segment: {
          ...currentSegment,
          speaker_track_id: target.id,
          speaker_label: body.display_name,
        },
      }))
    }
    if (path.endsWith('/transcript')) {
      const afterOrdinal = Number(requestUrl.searchParams.get('after_ordinal') || 0)
      const limit = Number(requestUrl.searchParams.get('limit') || 200)
      requestCounts.transcriptAfterOrdinals.push(afterOrdinal)
      const remaining = transcriptSegments.filter((segment) => Number(segment.ordinal) > afterOrdinal)
      const items = remaining.slice(0, limit)
      const nextOrdinal = remaining.length > items.length ? Number(items.at(-1)?.ordinal) : null
      return route.fulfill(json({ items, next_ordinal: nextOrdinal }))
    }
    if (path.endsWith('/speakers')) return route.fulfill(json(speakers))
    if (path.endsWith('/artifacts')) return route.fulfill(json([minutesArtifact]))
    if (path.endsWith('/jobs')) return route.fulfill(json([]))
    if (path.endsWith('/exports')) return route.fulfill(json([]))
    if (path.endsWith('/events')) {
      const afterCursor = Number(requestUrl.searchParams.get('after_cursor') || 0)
      const items = (options.durableEvents || []).filter((event) => event.cursor > afterCursor)
      return route.fulfill(json({ items, next_cursor: null }))
    }
    if (path.endsWith('/audio-ticket')) {
      audioTicketAttempts += 1
      if (audioTicketAttempts <= (options.audioTicketFailures || 0)) {
        return route.fulfill(json({ detail: { code: 'AUDIO_NOT_AVAILABLE' } }, 404))
      }
      return route.fulfill(json({
        ticket: 'playback-ticket',
        purpose: 'meeting_audio_playback',
        expires_at: '2099-01-01T00:00:00Z',
        audio_url: '/api/meetings/v1/sessions/meeting-review-1/audio?ticket=playback-ticket',
      }))
    }
    if (path === '/api/meetings/v1/models') {
      return route.fulfill(json({ items: [{
        model_ref: 'meeting:local:test123456789',
        label: '本地会议模型',
        provider_label: '本机运行时',
        locality: 'local',
        configured: true,
        available: true,
        capabilities: ['text', 'structured_json'],
        data_boundary: 'local',
      }] }))
    }
    if (path.endsWith('/audio') && requestUrl.searchParams.has('ticket')) {
      return route.fulfill({ status: 200, contentType: 'audio/wav', body: silentWav() })
    }
    return route.fulfill(json({ items: [] }))
  })

  return requestCounts
}

test('meeting player automatically becomes playable after delayed WAV finalization', async ({ page }) => {
  const ticketStatuses: number[] = []
  page.on('response', (response) => {
    if (new URL(response.url()).pathname.endsWith('/audio-ticket')) ticketStatuses.push(response.status())
  })
  await mockMeetingApis(page, { audioTicketFailures: 2 })

  await page.goto(`/meetings/${meeting.id}`)
  const player = page.getByLabel('会议录音播放器')
  await expect(player).toBeVisible()
  await expect.poll(() => ticketStatuses, { timeout: 10_000 }).toEqual([404, 404, 200])
  await expect(player).toHaveAttribute('src', /playback-ticket/)
  await expect.poll(
    () => player.evaluate((audio: HTMLAudioElement) => audio.duration),
    { timeout: 10_000 },
  ).toBeGreaterThan(0)
  await expect(page.getByText(/HTTP 404/)).toHaveCount(0)
})

async function expectNoHorizontalOverflow(page: Page) {
  const layout = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    viewportWidth: window.innerWidth,
    bodyWidth: document.body.getBoundingClientRect().width,
  }))
  expect(layout.scrollWidth).toBeLessThanOrEqual(layout.viewportWidth + 1)
  expect(layout.bodyWidth).toBeLessThanOrEqual(layout.viewportWidth + 1)
}

async function capture(page: Page, testInfo: TestInfo, name: string) {
  await page.screenshot({ path: testInfo.outputPath(`${name}.png`), fullPage: true })
}

test('desktop navigation exposes the independent meeting transcription product', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await mockMeetingApis(page)
  await page.goto('/meetings')

  await expect(page.getByRole('link', { name: '会议转写' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '会议转写' })).toBeVisible()
  await expect(page.getByText('产品与投研周会')).toBeVisible()
  await expect(page.getByText('2 位发言人').first()).toBeVisible()
  await expectNoHorizontalOverflow(page)
  await capture(page, testInfo, 'meeting-list-desktop')
})

test('mobile recording import is visible and has no horizontal overflow', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 375, height: 812 })
  await mockMeetingApis(page)
  await page.goto('/meetings/import')

  await expect(page.getByRole('heading', { name: '导入会议录音' })).toBeVisible()
  await expect(page.getByRole('button', { name: /选择会议录音/ })).toBeVisible()
  await expect(page.getByRole('button', { name: '开始导入' })).toBeDisabled()
  await expectNoHorizontalOverflow(page)
  await capture(page, testInfo, 'meeting-import-mobile')
})

test('review transcript renames one segment or every segment from the same speaker', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 375, height: 812 })
  const transcriptSegments = [
    segments[0],
    segments[1],
    {
      ...segments[0],
      id: 'segment-3',
      ordinal: 3,
      utterance_id: 'utterance-3',
      start_ms: 32_000,
      end_ms: 38_000,
      raw_text: '第二段属于张明的发言。',
      asr_final_text: '第二段属于张明的发言。',
      display_text: '第二段属于张明的发言。',
    },
  ]
  const requestCounts = await mockMeetingApis(page, { transcriptSegments })

  await page.goto(`/meetings/${meeting.id}`)
  await page.getByRole('tab', { name: '逐字稿', exact: true }).click()
  const firstSpeaker = page.getByRole('button', { name: '修改发言人：张明' }).first()
  await expect(firstSpeaker).toBeVisible()
  await firstSpeaker.click()

  const dialog = page.getByRole('dialog', { name: '修改发言人' })
  await expect(dialog).toBeVisible()
  await expect(dialog.getByRole('radio', { name: /仅修改这一段/ })).toBeChecked()
  await dialog.getByLabel('发言人名称').fill('王敏')
  await dialog.getByRole('radio', { name: /修改此发言人的全部发言/ }).check()
  await dialog.getByRole('button', { name: '应用到全部' }).click()

  await expect.poll(() => requestCounts.speakerRenameBodies).toEqual([{
    display_name: '王敏',
    scope: 'speaker',
    expected_speaker_version: 2,
  }])
  await expect(page.getByRole('button', { name: '修改发言人：王敏' })).toHaveCount(2)
  await expect(page.getByText('本场共 2 段发言已统一显示为“王敏”。')).toBeVisible()

  const secondSpeaker = page.getByRole('button', { name: '修改发言人：李然' })
  await secondSpeaker.click()
  await dialog.getByLabel('发言人名称').fill('陈晓')
  await dialog.getByRole('button', { name: '保存此段' }).click()
  await expect.poll(() => requestCounts.speakerRenameBodies.at(-1)).toEqual({
    display_name: '陈晓',
    scope: 'segment',
    expected_speaker_version: 1,
  })
  await expect(page.getByRole('button', { name: '修改发言人：陈晓' })).toHaveCount(1)
  await expectNoHorizontalOverflow(page)
  await capture(page, testInfo, 'meeting-speaker-rename-mobile')
})

for (const viewport of viewports) {
  test(`${viewport.name} meeting live and review workspaces stay usable`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height })
    const requestCounts = await mockMeetingApis(page)

    await page.goto(`/meetings/${liveMeeting.id}/live`)
    await expect(page.getByRole('heading', { name: liveMeeting.title })).toBeVisible()
    await expect(page.getByRole('button', { name: '开始会议' })).toBeVisible()
    await expect(page.getByRole('heading', { name: '实时逐字稿' })).toHaveCount(1)
    await expect(page.locator('#main-content [aria-live="polite"]')).toHaveCount(1)
    await expect.poll(() => requestCounts.capabilities).toBe(1)
    if (viewport.workspace === 'wide') {
      await expect(page.getByLabel('AI 整理模型')).toBeVisible()
      await expect(page.locator('#meeting-model')).toHaveCount(1)
    } else {
      const transcriptTab = page.getByRole('tab', { name: '逐字稿' })
      await expect(transcriptTab).toBeVisible()
      if (viewport.workspace === 'mobile') await expect(page.getByRole('tab', { name: '发言人' })).toBeVisible()
      await page.getByRole('tab', { name: 'AI 要点' }).click()
      await expect(page.getByLabel('AI 整理模型')).toBeVisible()
      await expect(page.locator('#meeting-model')).toHaveCount(1)
      await transcriptTab.click()
      await expect(page.getByRole('heading', { name: '实时逐字稿' })).toBeVisible()
    }
    await expectNoHorizontalOverflow(page)
    await capture(page, testInfo, `meeting-live-${viewport.name}`)

    await page.goto(`/meetings/${meeting.id}`)
    await expect(page.getByRole('heading', { name: meeting.title })).toBeVisible()
    await expect(page.getByLabel('会议录音播放器')).toBeVisible()
    await expect(page.getByRole('tab', { name: '文件与导出' })).toBeVisible()
    await expect(page.getByText('收入质量改善，下周继续复核客户留存率。')).toBeVisible()
    await capture(page, testInfo, `meeting-minutes-${viewport.name}`)
    await page.getByRole('tab', { name: '决定', exact: true }).click()
    await page.getByRole('button', { name: '00:12' }).click()
    await expect(page.getByRole('tab', { name: '逐字稿', exact: true })).toHaveAttribute('aria-selected', 'true')
    await expect(page.getByText('本季度收入质量明显改善。')).toBeVisible()
    await expectNoHorizontalOverflow(page)
    await capture(page, testInfo, `meeting-review-${viewport.name}`)
  })
}

test('multi-hour transcript paging stays bounded while preserving live and editing state', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  const longTranscript = Array.from({ length: 5_000 }, (_, index) => {
    const ordinal = index + 1
    return {
      id: `segment-${ordinal}`,
      meeting_id: liveMeeting.id,
      ordinal,
      utterance_id: `utterance-${ordinal}`,
      start_ms: ordinal * 1_000,
      end_ms: ordinal * 1_000 + 800,
      speaker_track_id: ordinal % 2 ? 'speaker-1' : 'speaker-2',
      speaker_label: ordinal % 2 ? '张明' : '李然',
      raw_text: `第 ${ordinal} 段多小时会议逐字稿。`,
      asr_final_text: `第 ${ordinal} 段多小时会议逐字稿。`,
      display_text: `第 ${ordinal} 段多小时会议逐字稿。`,
      current_revision_no: 1,
      display_layer: 'asr',
      human_locked: false,
    }
  })
  const requestCounts = await mockMeetingApis(page, {
    transcriptSegments: longTranscript,
    lastSegmentOrdinal: 4_800,
    durableEvents: [
      {
        schema_version: 'siq.meeting.event.v1',
        event_id: 'partial-5001',
        meeting_id: liveMeeting.id,
        type: 'transcript.partial',
        cursor: 1,
        emitted_at: '2026-07-14T01:00:00Z',
        payload: { utterance_id: 'utterance-5001', text: '不应播报的 partial', start_ms: 5_001_000 },
      },
      {
        schema_version: 'siq.meeting.event.v1',
        event_id: 'stable-5001',
        meeting_id: liveMeeting.id,
        type: 'transcript.segment.stable',
        cursor: 2,
        emitted_at: '2026-07-14T01:00:01Z',
        payload: {
          id: 'segment-5001',
          meeting_id: liveMeeting.id,
          ordinal: 5_001,
          utterance_id: 'utterance-5001',
          start_ms: 5_001_000,
          end_ms: 5_001_800,
          speaker_track_id: 'speaker-1',
          speaker_display_name: '张明',
          raw_text: '实时新增第 5001 段。',
          asr_final_text: '实时新增第 5001 段。',
          display_text: '实时新增第 5001 段。',
          revision_no: 1,
          text_state: 'stable',
          human_locked: false,
        },
      },
    ],
  })

  await page.goto(`/meetings/${liveMeeting.id}/live`)
  await expect(page.getByRole('heading', { name: '实时逐字稿' })).toBeVisible()
  await expect.poll(() => requestCounts.transcriptAfterOrdinals).toEqual([4_600])
  await expect(page.getByText(/已加载 200/)).toBeVisible()
  await expect(page.getByRole('button', { name: '加载更早段落' })).toBeVisible()
  await expect(page.getByRole('button', { name: '加载后续段落' })).toBeVisible()
  await expect.poll(() => page.locator('[data-transcript-segment]').count()).toBeLessThanOrEqual(TRANSCRIPT_MAX_RENDERED_SEGMENTS)

  await page.getByRole('button', { name: '加载后续段落' }).click()
  await expect.poll(() => requestCounts.transcriptAfterOrdinals).toContain(4_800)
  const liveRegion = page.locator('#main-content [aria-live="polite"]')
  await expect(liveRegion).toContainText('实时新增第 5001 段。', { timeout: 8_000 })
  await expect(liveRegion).not.toContainText('不应播报的 partial')
  await expect(page.getByTestId('transcript-scroll').getByText('实时新增第 5001 段。')).toBeVisible()

  const earlierButton = page.getByRole('button', { name: '加载更早段落' })
  for (let pageIndex = 0; pageIndex < 30 && await earlierButton.count(); pageIndex += 1) {
    const before = requestCounts.transcriptAfterOrdinals.length
    await earlierButton.click()
    await expect.poll(() => requestCounts.transcriptAfterOrdinals.length).toBe(before + 1)
    await expect(page.getByRole('button', { name: '正在加载' })).toHaveCount(0)
  }
  await expect(earlierButton).toHaveCount(0)
  await expect(page.getByText(/已加载 5001/)).toBeVisible()
  await expect.poll(() => page.locator('[data-transcript-segment]').count()).toBeLessThanOrEqual(TRANSCRIPT_MAX_RENDERED_SEGMENTS)

  const scrollRegion = page.getByTestId('transcript-scroll')
  await scrollRegion.evaluate((element) => {
    element.scrollTop = element.scrollHeight
    element.dispatchEvent(new Event('scroll'))
  })
  const latestSegment = page.locator('[data-transcript-segment="segment-5001"]')
  await expect(latestSegment).toBeVisible()
  await latestSegment.getByRole('button', { name: '修改文字' }).click()
  const editor = page.getByLabel('订正文字')
  await editor.fill('编辑中的第 5001 段')
  await scrollRegion.evaluate((element) => {
    element.scrollTop = 0
    element.dispatchEvent(new Event('scroll'))
  })
  await expect(page.getByRole('button', { name: '回到实时' })).toBeVisible()
  await expect(editor).toHaveCount(1)
  await expect(editor).toHaveValue('编辑中的第 5001 段')
  await expect.poll(() => page.locator('[data-transcript-segment]').count()).toBeLessThanOrEqual(TRANSCRIPT_MAX_RENDERED_SEGMENTS)

  await page.getByRole('button', { name: '回到实时' }).click()
  await expect.poll(() => scrollRegion.evaluate((element) => (
    element.scrollHeight - element.scrollTop - element.clientHeight
  ))).toBeLessThan(100)
  await expect(editor).toHaveCount(1)
  await expect(editor).toHaveValue('编辑中的第 5001 段')
  await expect(editor).toBeVisible()
  await expect(latestSegment.getByLabel('订正文字')).toHaveCount(1)
  await expectNoHorizontalOverflow(page)
  await capture(page, testInfo, 'meeting-transcript-multi-hour-window')
})

test('real IndexedDB outbox survives a browser refresh and clears acknowledged frames', async ({ page }) => {
  await mockMeetingApis(page)
  await page.goto('/meetings')

  await page.evaluate(async () => {
    const moduleUrl = '/src/features/meeting-transcription/meetingOutbox.ts'
    const module = await import(/* @vite-ignore */ moduleUrl)
    const store = new module.IndexedDbMeetingOutboxStore(60_000)
    await store.putFrame(
      'meeting-refresh-test',
      7,
      '11111111-1111-4111-8111-111111111111',
      -1,
      0,
      Uint8Array.from([7, 8, 9]).buffer,
    )
  })

  await page.reload()
  const restored = await page.evaluate(async () => {
    const moduleUrl = '/src/features/meeting-transcription/meetingOutbox.ts'
    const module = await import(/* @vite-ignore */ moduleUrl)
    const store = new module.IndexedDbMeetingOutboxStore(60_000)
    const snapshot = await store.restore('meeting-refresh-test', 7)
    const bytes = snapshot.frames.get(0)
      ? [...new Uint8Array(snapshot.frames.get(0))]
      : []
    await store.acknowledge(
      'meeting-refresh-test',
      7,
      snapshot.clientStreamId || '',
      0,
    )
    const afterAck = await store.restore('meeting-refresh-test', 7)
    await store.clear('meeting-refresh-test', 7)
    return {
      clientStreamId: snapshot.clientStreamId,
      sequences: [...snapshot.frames.keys()],
      bytes,
      remainingAfterAck: afterAck.frames.size,
    }
  })

  expect(restored).toEqual({
    clientStreamId: '11111111-1111-4111-8111-111111111111',
    sequences: [0],
    bytes: [7, 8, 9],
    remainingAfterAck: 0,
  })
})
