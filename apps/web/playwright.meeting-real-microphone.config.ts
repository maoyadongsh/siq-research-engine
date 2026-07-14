import { defineConfig, devices } from '@playwright/test'
import { accessSync, constants } from 'node:fs'
import { resolve } from 'node:path'

function required(name: string): string {
  const value = process.env[name]?.trim()
  if (!value) throw new Error(`${name} is required for the real microphone meeting E2E`)
  return value
}

if (process.env.SIQ_MEETING_REAL_MIC_E2E !== '1') {
  throw new Error('Set SIQ_MEETING_REAL_MIC_E2E=1 to run the real microphone meeting E2E')
}

const baseURL = required('PLAYWRIGHT_BASE_URL')
const fakeAudioFile = resolve(required('SIQ_E2E_FAKE_AUDIO_FILE'))
required('SIQ_E2E_USERNAME')
required('SIQ_E2E_PASSWORD')
accessSync(fakeAudioFile, constants.R_OK)

export default defineConfig({
  testDir: './e2e/tests',
  testMatch: 'meeting-real-microphone.spec.ts',
  timeout: 150_000,
  fullyParallel: false,
  workers: 1,
  preserveOutput: 'always',
  reporter: [['list'], ['html', { open: 'never' }]],
  expect: { timeout: 20_000 },
  use: {
    ...devices['Desktop Chrome'],
    baseURL,
    permissions: ['microphone'],
    ignoreHTTPSErrors: process.env.SIQ_E2E_IGNORE_HTTPS_ERRORS === '1',
    screenshot: 'only-on-failure',
    // Live authentication bodies must never be persisted in a browser trace.
    trace: 'off',
    launchOptions: {
      args: [
        '--use-fake-device-for-media-stream',
        '--use-fake-ui-for-media-stream',
        `--use-file-for-fake-audio-capture=${fakeAudioFile}`,
        '--autoplay-policy=no-user-gesture-required',
      ],
    },
  },
  projects: [{ name: 'chromium-real-microphone' }],
})
