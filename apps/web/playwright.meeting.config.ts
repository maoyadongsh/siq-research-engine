import { defineConfig, devices } from '@playwright/test'

const port = Number(process.env.SIQ_MEETING_E2E_PORT || 15176)
const baseURL = process.env.PLAYWRIGHT_BASE_URL || `http://127.0.0.1:${port}`

export default defineConfig({
  testDir: './e2e/tests',
  testMatch: 'meeting-transcription-responsive.spec.ts',
  timeout: 45_000,
  fullyParallel: false,
  workers: 1,
  preserveOutput: 'always',
  reporter: [['list'], ['html', { open: 'never' }]],
  expect: { timeout: 10_000 },
  use: {
    baseURL,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  webServer: {
    command: `VITE_SIQ_MEETINGS_ENABLED=1 VITE_SIQ_MEETING_IMPORT_ENABLED=1 SIQ_FRONTEND_PORT=${port} npm run dev -- --host 127.0.0.1 --port ${port}`,
    url: baseURL,
    reuseExistingServer: false,
    timeout: 120_000,
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
})
