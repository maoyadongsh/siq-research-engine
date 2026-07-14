import { defineConfig, devices } from '@playwright/test'

const defaultPort = 15177

function parseTcpPort(value: string, label: string): number {
  const port = Number(value)
  if (!Number.isInteger(port) || port < 1 || port > 65_535) {
    throw new Error(`${label} must be an integer TCP port, got "${value}"`)
  }
  return port
}

const configuredPort = parseTcpPort(
  process.env.SIQ_MEETING_DISABLED_E2E_PORT || String(defaultPort),
  'SIQ_MEETING_DISABLED_E2E_PORT',
)
const configuredBaseURL =
  process.env.PLAYWRIGHT_BASE_URL || `http://127.0.0.1:${configuredPort}`
const parsedBaseURL = new URL(configuredBaseURL)
if (!parsedBaseURL.port) parsedBaseURL.port = String(configuredPort)
const baseURL = parsedBaseURL.toString()
const webServerPort = parseTcpPort(parsedBaseURL.port, 'PLAYWRIGHT_BASE_URL port')

export default defineConfig({
  testDir: './e2e/tests',
  testMatch: 'meeting-feature-disabled.spec.ts',
  timeout: 30_000,
  fullyParallel: false,
  workers: 1,
  reporter: [['list'], ['html', { open: 'never' }]],
  expect: { timeout: 10_000 },
  use: {
    baseURL,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  webServer: {
    command: `VITE_SIQ_MEETINGS_ENABLED=0 VITE_SIQ_MEETING_IMPORT_ENABLED=0 SIQ_FRONTEND_PORT=${webServerPort} npm run dev -- --host 127.0.0.1 --port ${webServerPort}`,
    url: baseURL,
    reuseExistingServer: false,
    timeout: 120_000,
  },
  projects: [
    {
      name: 'chromium-meeting-disabled',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
})
