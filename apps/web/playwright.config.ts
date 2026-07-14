import { defineConfig, devices } from '@playwright/test'

const defaultPlaywrightPort = 15174

function parseTcpPort(value: string, label: string): number {
  const port = Number(value)
  if (!Number.isInteger(port) || port < 1 || port > 65_535) {
    throw new Error(`${label} must be an integer TCP port, got "${value}"`)
  }
  return port
}

const configuredPort = parseTcpPort(
  process.env.SIQ_FRONTEND_PORT || String(defaultPlaywrightPort),
  'SIQ_FRONTEND_PORT',
)

const configuredBaseURL =
  process.env.PLAYWRIGHT_BASE_URL || `http://127.0.0.1:${configuredPort}`
const parsedBaseURL = new URL(configuredBaseURL)
if (!parsedBaseURL.port) {
  parsedBaseURL.port = String(configuredPort)
}
const baseURL = parsedBaseURL.toString()
const webServerPort = parseTcpPort(parsedBaseURL.port, 'PLAYWRIGHT_BASE_URL port')

export default defineConfig({
  testDir: './e2e/tests',
  // Meeting routes are compiled behind a Vite flag and have dedicated
  // enabled/disabled suites. Keep the default suite focused on existing apps.
  testIgnore: '**/meeting-*.spec.ts',
  timeout: 30_000,
  fullyParallel: false,
  reporter: [['list'], ['html', { open: 'never' }]],
  expect: {
    timeout: 10_000,
  },
  use: {
    baseURL,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  webServer: {
    command: `VITE_SIQ_MEETINGS_ENABLED=0 VITE_SIQ_MEETING_IMPORT_ENABLED=0 SIQ_FRONTEND_PORT=${webServerPort} npm run dev -- --host 127.0.0.1 --port ${webServerPort}`,
    url: baseURL,
    reuseExistingServer: true,
    timeout: 120_000,
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
})
