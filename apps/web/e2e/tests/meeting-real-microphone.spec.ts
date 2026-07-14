import { Buffer } from 'node:buffer'

import { expect, test, type Locator, type Page, type TestInfo } from '@playwright/test'

const enabled = process.env.SIQ_MEETING_REAL_MIC_E2E === '1'
const username = process.env.SIQ_E2E_USERNAME
const password = process.env.SIQ_E2E_PASSWORD

test.skip(!enabled, 'requires an explicitly enabled running-instance microphone test')

function required(value: string | undefined, name: string): string {
  if (!value) throw new Error(`${name} is required for the real microphone meeting E2E`)
  return value
}

interface MicrophoneProbe {
  calls: number
  audioTrackCount: number
}

interface LiveEvidence {
  schema_version: 'siq.meeting.real_microphone.e2e.v1'
  origin: string
  login_status: number | null
  create_status: number | null
  meeting_id: string | null
  microphone: MicrophoneProbe
  websocket_upgrade_observed: boolean
  stream_start_observed: boolean
  stream_ready_observed: boolean
  pcm_binary_frame_count: number
  pcm_payload_byte_count: number
  audio_ack_count: number
  transcription_event_types: string[]
  audio_ticket_status: number | null
  replay_range_status: number | null
  replay_content_range_valid: boolean
  replay_wav_header_valid: boolean
  delete_status: number | null
  cleanup_error: string | null
}

interface ReplayProbe {
  audioTicketStatus: number
  rangeStatus: number | null
  contentRangeValid: boolean
  wavHeaderValid: boolean
}

declare global {
  interface Window {
    __siqMeetingMicrophoneProbe?: MicrophoneProbe
  }
}

async function authenticatedMutation(
  page: Page,
  path: string,
  method: 'POST' | 'DELETE',
): Promise<number> {
  return page.evaluate(async ({ requestPath, requestMethod }) => {
    const headers: Record<string, string> = { 'Content-Type': 'application/json' }
    const token = window.localStorage.getItem('access_token')
    if (token) headers.Authorization = `Bearer ${token}`
    const csrfCookie = document.cookie
      .split(';')
      .map((item) => item.trim())
      .find((item) => item.startsWith('siq_csrf_token='))
    if (csrfCookie) headers['X-CSRF-Token'] = decodeURIComponent(csrfCookie.slice(csrfCookie.indexOf('=') + 1))
    const response = await fetch(requestPath, {
      method: requestMethod,
      headers,
      body: requestMethod === 'POST' ? '{}' : undefined,
    })
    return response.status
  }, { requestPath: path, requestMethod: method })
}

async function attachEvidence(testInfo: TestInfo, evidence: LiveEvidence) {
  await testInfo.attach('meeting-real-microphone-evidence.json', {
    body: Buffer.from(`${JSON.stringify(evidence, null, 2)}\n`, 'utf8'),
    contentType: 'application/json',
  })
}

async function fillSecret(input: Locator, secret: string) {
  await input.evaluate((element, value) => {
    const field = element as HTMLInputElement
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set
    if (!setter) throw new Error('password input setter is unavailable')
    setter.call(field, value)
    field.dispatchEvent(new Event('input', { bubbles: true, composed: true }))
    field.dispatchEvent(new Event('change', { bubbles: true }))
  }, secret)
}

async function probeMeetingReplay(page: Page, meetingId: string): Promise<ReplayProbe> {
  return page.evaluate(async (id) => {
    const mutationHeaders: Record<string, string> = { 'Content-Type': 'application/json' }
    const token = window.localStorage.getItem('access_token')
    if (token) mutationHeaders.Authorization = `Bearer ${token}`
    const csrfCookie = document.cookie
      .split(';')
      .map((item) => item.trim())
      .find((item) => item.startsWith('siq_csrf_token='))
    if (csrfCookie) {
      mutationHeaders['X-CSRF-Token'] = decodeURIComponent(csrfCookie.slice(csrfCookie.indexOf('=') + 1))
    }

    const ticketResponse = await fetch(
      `/api/meetings/v1/sessions/${encodeURIComponent(id)}/audio-ticket`,
      { method: 'POST', headers: mutationHeaders, body: '{}' },
    )
    if (!ticketResponse.ok) {
      return {
        audioTicketStatus: ticketResponse.status,
        rangeStatus: null,
        contentRangeValid: false,
        wavHeaderValid: false,
      }
    }
    const ticket = await ticketResponse.json() as { audio_url?: string }
    if (!ticket.audio_url) {
      return {
        audioTicketStatus: ticketResponse.status,
        rangeStatus: null,
        contentRangeValid: false,
        wavHeaderValid: false,
      }
    }
    const replayResponse = await fetch(ticket.audio_url, { headers: { Range: 'bytes=0-63' } })
    const bytes = new Uint8Array(await replayResponse.arrayBuffer())
    const ascii = (start: number, end: number) => String.fromCharCode(...bytes.slice(start, end))
    return {
      audioTicketStatus: ticketResponse.status,
      rangeStatus: replayResponse.status,
      contentRangeValid: /^bytes 0-\d+\/\d+$/.test(replayResponse.headers.get('content-range') || ''),
      wavHeaderValid: bytes.length >= 12 && ascii(0, 4) === 'RIFF' && ascii(8, 12) === 'WAVE',
    }
  }, meetingId)
}

test('running instance accepts real browser microphone PCM and returns live ASR events', async ({ page }, testInfo) => {
  const evidence: LiveEvidence = {
    schema_version: 'siq.meeting.real_microphone.e2e.v1',
    origin: '',
    login_status: null,
    create_status: null,
    meeting_id: null,
    microphone: { calls: 0, audioTrackCount: 0 },
    websocket_upgrade_observed: false,
    stream_start_observed: false,
    stream_ready_observed: false,
    pcm_binary_frame_count: 0,
    pcm_payload_byte_count: 0,
    audio_ack_count: 0,
    transcription_event_types: [],
    audio_ticket_status: null,
    replay_range_status: null,
    replay_content_range_valid: false,
    replay_wav_header_valid: false,
    delete_status: null,
    cleanup_error: null,
  }

  await page.addInitScript(() => {
    const probe: MicrophoneProbe = { calls: 0, audioTrackCount: 0 }
    window.__siqMeetingMicrophoneProbe = probe
    const mediaDevices = navigator.mediaDevices
    if (!mediaDevices?.getUserMedia) return
    const original = mediaDevices.getUserMedia.bind(mediaDevices)
    mediaDevices.getUserMedia = async (constraints: MediaStreamConstraints) => {
      probe.calls += 1
      const stream = await original(constraints)
      probe.audioTrackCount += stream.getAudioTracks().length
      return stream
    }
  })

  page.on('websocket', (socket) => {
    const url = new URL(socket.url())
    if (!/\/api\/meetings\/v1\/sessions\/[^/]+\/audio$/.test(url.pathname)) return
    evidence.websocket_upgrade_observed = true
    socket.on('framesent', ({ payload }) => {
      if (typeof payload === 'string') {
        try {
          const message = JSON.parse(payload) as { type?: string }
          if (message.type === 'stream.start') evidence.stream_start_observed = true
        } catch {
          // Non-JSON text frames are not part of the meeting stream contract.
        }
        return
      }
      const frame = Buffer.from(payload)
      if (frame.length <= 32 || frame.subarray(0, 4).toString('ascii') !== 'SIQA') return
      evidence.pcm_binary_frame_count += 1
      evidence.pcm_payload_byte_count += frame.length - 32
    })
    socket.on('framereceived', ({ payload }) => {
      if (typeof payload !== 'string') return
      try {
        const message = JSON.parse(payload) as { type?: string }
        if (message.type === 'stream.ready') evidence.stream_ready_observed = true
        if (message.type === 'audio.ack') evidence.audio_ack_count += 1
        if (
          message.type === 'transcript.partial'
          || message.type === 'transcript.segment.stable'
        ) {
          if (!evidence.transcription_event_types.includes(message.type)) {
            evidence.transcription_event_types.push(message.type)
          }
        }
      } catch {
        // Invalid frames are surfaced by the application and fail the assertions below.
      }
    })
  })

  try {
    const loginResponsePromise = page.waitForResponse((response) => (
      response.url().endsWith('/api/auth/login') && response.request().method() === 'POST'
    ))
    await page.goto('/login')
    evidence.origin = new URL(page.url()).origin
    await page.getByLabel('用户名').click()
    await page.getByLabel('用户名').fill(required(username, 'SIQ_E2E_USERNAME'))
    const passwordInput = page.getByLabel('密码')
    await passwordInput.click()
    await fillSecret(passwordInput, required(password, 'SIQ_E2E_PASSWORD'))
    await page.getByRole('button', { name: '登录', exact: true }).click()
    const loginResponse = await loginResponsePromise
    evidence.login_status = loginResponse.status()
    expect(evidence.login_status).toBe(200)

    await page.goto('/meetings/new')
    await expect(page.getByRole('heading', { name: '新建实时会议' })).toBeVisible()
    const title = `真实麦克风验收 ${new Date().toISOString()}`
    await page.getByLabel('会议标题').fill(title)
    const aiToggle = page.locator('#ai-enabled')
    if (await aiToggle.isChecked()) {
      await page.getByText('AI 整理', { exact: true }).click()
      await expect(aiToggle).not.toBeChecked()
    }

    const createResponsePromise = page.waitForResponse((response) => (
      new URL(response.url()).pathname === '/api/meetings/v1/sessions'
      && response.request().method() === 'POST'
    ))
    await page.getByRole('button', { name: '创建并进入工作台' }).click()
    const createResponse = await createResponsePromise
    evidence.create_status = createResponse.status()
    expect([200, 201]).toContain(evidence.create_status)
    const created = await createResponse.json() as { id?: string }
    evidence.meeting_id = created.id || null
    expect(evidence.meeting_id).toBeTruthy()
    await expect(page).toHaveURL(new RegExp(`/meetings/${evidence.meeting_id}/live$`))

    await page.getByRole('button', { name: '开始会议' }).click()
    await expect.poll(
      () => evidence.stream_ready_observed,
      { timeout: 30_000, message: 'the meeting WebSocket did not receive stream.ready' },
    ).toBe(true)
    await expect.poll(
      () => evidence.transcription_event_types.length,
      { timeout: 100_000, message: 'the live ASR service did not return a transcript event' },
    ).toBeGreaterThan(0)

    evidence.microphone = await page.evaluate(() => (
      window.__siqMeetingMicrophoneProbe || { calls: 0, audioTrackCount: 0 }
    ))
    expect(evidence.websocket_upgrade_observed).toBe(true)
    expect(evidence.stream_start_observed).toBe(true)
    expect(evidence.stream_ready_observed).toBe(true)
    expect(evidence.microphone.calls).toBeGreaterThan(0)
    expect(evidence.microphone.audioTrackCount).toBeGreaterThan(0)
    expect(evidence.pcm_binary_frame_count).toBeGreaterThan(0)
    expect(evidence.pcm_payload_byte_count).toBeGreaterThan(0)
    expect(evidence.audio_ack_count).toBeGreaterThan(0)

    await page.getByRole('button', { name: '结束', exact: true }).click()
    await page.getByRole('button', { name: '确认结束' }).click()
    await expect(page).toHaveURL(new RegExp(`/meetings/${evidence.meeting_id}$`), { timeout: 30_000 })

    let replay: ReplayProbe | null = null
    await expect.poll(async () => {
      replay = await probeMeetingReplay(page, evidence.meeting_id as string)
      return {
        ticket: replay.audioTicketStatus,
        range: replay.rangeStatus,
        contentRange: replay.contentRangeValid,
        wav: replay.wavHeaderValid,
      }
    }, { timeout: 30_000, message: 'finalized meeting WAV was not available for ranged replay' }).toEqual({
      ticket: 200,
      range: 206,
      contentRange: true,
      wav: true,
    })
    evidence.audio_ticket_status = replay?.audioTicketStatus ?? null
    evidence.replay_range_status = replay?.rangeStatus ?? null
    evidence.replay_content_range_valid = replay?.contentRangeValid ?? false
    evidence.replay_wav_header_valid = replay?.wavHeaderValid ?? false
  } finally {
    if (evidence.meeting_id && !page.isClosed()) {
      try {
        await authenticatedMutation(
          page,
          `/api/meetings/v1/sessions/${encodeURIComponent(evidence.meeting_id)}/stop`,
          'POST',
        )
        const deleted = await authenticatedMutation(
          page,
          `/api/meetings/v1/sessions/${encodeURIComponent(evidence.meeting_id)}`,
          'DELETE',
        )
        evidence.delete_status = deleted
      } catch (error) {
        evidence.cleanup_error = error instanceof Error ? error.message : 'meeting cleanup failed'
      }
    }
    evidence.microphone = await page.evaluate(() => (
      window.__siqMeetingMicrophoneProbe || { calls: 0, audioTrackCount: 0 }
    )).catch(() => evidence.microphone)
    await attachEvidence(testInfo, evidence)
  }

  expect(evidence.delete_status).toBe(202)
})
