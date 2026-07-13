import { expect, test, type Page, type Route } from '@playwright/test'
import { e2eUser } from '../support/mockApi'

function json(body: unknown, status = 200) {
  return { status, contentType: 'application/json', body: JSON.stringify(body) }
}

async function mockVoiceChat(page: Page) {
  let historyMessages: Array<Record<string, unknown>> = []
  let streamPayload: Record<string, unknown> | undefined

  await page.addInitScript((user) => {
    window.localStorage.setItem('access_token', 'playwright-token')
    window.localStorage.setItem('user', JSON.stringify(user))
    window.localStorage.setItem('theme', 'light')

    class FakeMediaRecorder {
      static isTypeSupported() { return true }
      state: 'inactive' | 'recording' = 'inactive'
      mimeType = 'audio/webm;codecs=opus'
      ondataavailable: ((event: { data: Blob }) => void) | null = null
      onstop: (() => void) | null = null
      onerror: ((event: unknown) => void) | null = null
      start() { this.state = 'recording' }
      stop() {
        this.state = 'inactive'
        queueMicrotask(() => {
          this.ondataavailable?.({ data: new Blob(['voice-sample'], { type: this.mimeType }) })
          this.onstop?.()
        })
      }
    }

    Object.defineProperty(window, 'MediaRecorder', { configurable: true, value: FakeMediaRecorder })
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: {
        getUserMedia: async () => ({ getTracks: () => [{ stop() {} }] }),
      },
    })
  }, e2eUser)

  await page.route('**/*', async (route: Route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (!url.pathname.startsWith('/api/')) {
      await route.continue()
      return
    }
    if (url.pathname === '/api/auth/me') {
      await route.fulfill(json(e2eUser))
      return
    }
    if (url.pathname === '/api/chat/sessions') {
      await route.fulfill(json({ sessions: [] }))
      return
    }
    if (url.pathname === '/api/chat/history') {
      await route.fulfill(json({ session_id: 'voice-session', messages: historyMessages }))
      return
    }
    if (url.pathname === '/api/chat/active') {
      await route.fulfill(json({ running: false, session_id: 'voice-session' }))
      return
    }
    if (url.pathname === '/api/chat/transcribe' && request.method() === 'POST') {
      await route.fulfill(json({
        text: '请分析这家公司的收入质量',
        duration: 0.65,
        language: 'zh',
        provider: 'funasr',
        attachment: {
          id: 'voice-id',
          filename: 'voice.webm',
          content_type: 'audio/webm',
          size: 12,
          path: '/data/chat_uploads/7/voice-id_voice.webm',
          url: '/api/chat/attachments/voice-id_voice.webm',
          kind: 'audio',
          metadata: { duration_ms: 650, transcript: '请分析这家公司的收入质量' },
        },
      }))
      return
    }
    if (url.pathname === '/api/chat/attachments/voice-id_voice.webm') {
      await route.fulfill({ status: 200, contentType: 'audio/webm', body: Buffer.from('voice-sample') })
      return
    }
    if (url.pathname === '/api/chat/stream' && request.method() === 'POST') {
      streamPayload = request.postDataJSON() as Record<string, unknown>
      historyMessages = [
        {
          role: 'user',
          content: '请分析这家公司的收入质量',
          created_at: '2026-07-13T08:00:00Z',
          attachments: [streamPayload.attachments && (streamPayload.attachments as Array<unknown>)[0]],
        },
        { role: 'assistant', content: '已收到语音指令。', created_at: '2026-07-13T08:00:01Z' },
      ]
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: [
          'event: run',
          'data: {"run_id":"voice-run","session_id":"voice-session"}',
          '',
          'data: {"content":"已收到语音指令。"}',
          '',
          'data: [DONE]',
          '',
        ].join('\n'),
      })
      return
    }
    await route.fulfill(json({ sessions: [], messages: [] }))
  })

  return {
    getStreamPayload: () => streamPayload,
  }
}

test('按住语音按钮会自动转写、发送并显示可回放音频', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  const mock = await mockVoiceChat(page)
  await page.goto('/chat')
  await page.waitForLoadState('networkidle')

  const mic = page.getByRole('button', { name: '按住说话' })
  await expect(mic).toBeVisible()
  const box = await mic.boundingBox()
  expect(box).not.toBeNull()
  await page.mouse.move((box?.x || 0) + (box?.width || 0) / 2, (box?.y || 0) + (box?.height || 0) / 2)
  await page.mouse.down()
  await page.waitForTimeout(650)
  await page.mouse.up()

  await expect(page.getByText('请分析这家公司的收入质量')).toBeVisible()
  await expect(page.locator('audio')).toHaveCount(1)
  await expect.poll(() => mock.getStreamPayload()?.message).toBe('请分析这家公司的收入质量')
  await expect(page.locator('audio')).toHaveAttribute('src', /^blob:/)
})
