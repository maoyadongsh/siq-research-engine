import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './e2e/tests',
  timeout: 30_000,
  fullyParallel: false,
  reporter: [['list'], ['html', { open: 'never' }]],
  expect: {
    timeout: 10_000,
  },
  use: {
    baseURL: 'http://127.0.0.1:15174',
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  webServer: {
    command: 'npm run dev -- --host 127.0.0.1 --port 15174',
    url: 'http://127.0.0.1:15174/',
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
