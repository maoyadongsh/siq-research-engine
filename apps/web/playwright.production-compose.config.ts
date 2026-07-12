import { defineConfig, devices } from '@playwright/test'

const baseURL = process.env.PLAYWRIGHT_BASE_URL
const outputDir = process.env.SIQ_E2E_OUTPUT_DIR

if (!baseURL) {
  throw new Error('PLAYWRIGHT_BASE_URL is required for the production Compose browser smoke')
}
if (!outputDir) {
  throw new Error('SIQ_E2E_OUTPUT_DIR is required for the production Compose browser smoke')
}

export default defineConfig({
  testDir: './e2e/tests',
  testMatch: 'production-compose-password-login.spec.ts',
  outputDir,
  timeout: 60_000,
  fullyParallel: false,
  workers: 1,
  reporter: [['line']],
  expect: {
    timeout: 15_000,
  },
  use: {
    baseURL,
    screenshot: 'off',
    trace: 'off',
    video: 'off',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
})
