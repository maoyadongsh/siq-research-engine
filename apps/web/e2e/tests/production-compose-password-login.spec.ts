import { execFileSync } from 'node:child_process'

import { expect, test } from '@playwright/test'

const username = process.env.SIQ_E2E_USERNAME
const password = process.env.SIQ_E2E_PASSWORD
const composeProject = process.env.SIQ_E2E_COMPOSE_PROJECT
const composeFile = process.env.SIQ_E2E_COMPOSE_FILE
const composeEnvFile = process.env.SIQ_E2E_COMPOSE_ENV_FILE
const backendURL = process.env.SIQ_E2E_BACKEND_URL
const productionComposeBrowserSmoke = process.env.SIQ_PRODUCTION_COMPOSE_BROWSER_SMOKE === '1'

test.skip(!productionComposeBrowserSmoke, 'requires the isolated production Compose smoke harness')

function required(value: string | undefined, name: string): string {
  if (!value) throw new Error(`${name} is required for the production Compose browser smoke`)
  return value
}

function restartApi(): void {
  execFileSync(
    'docker',
    [
      'compose',
      '--project-name',
      required(composeProject, 'SIQ_E2E_COMPOSE_PROJECT'),
      '--file',
      required(composeFile, 'SIQ_E2E_COMPOSE_FILE'),
      '--env-file',
      required(composeEnvFile, 'SIQ_E2E_COMPOSE_ENV_FILE'),
      'restart',
      'api',
    ],
    {
      env: process.env,
      stdio: 'pipe',
    },
  )
}

test('real password login survives API restart and logout clears the browser session', async ({ page }) => {
  const loginResponsePromise = page.waitForResponse((response) => (
    response.url().endsWith('/api/auth/login') && response.request().method() === 'POST'
  ))
  await page.goto('/login')
  await page.getByLabel('用户名').click()
  await page.getByLabel('用户名').fill(required(username, 'SIQ_E2E_USERNAME'))
  await page.getByLabel('密码').click()
  await page.getByLabel('密码').fill(required(password, 'SIQ_E2E_PASSWORD'))
  await page.getByRole('button', { name: '登录', exact: true }).click()

  const loginResponse = await loginResponsePromise
  expect(loginResponse.status()).toBe(200)
  await expect(page).toHaveURL(/\/$/)
  await expect(page.getByRole('heading', { name: '工作平台' })).toBeVisible()

  const browserCookies = await page.context().cookies()
  expect(browserCookies.some((cookie) => cookie.name === 'siq_access_token' && cookie.httpOnly)).toBe(true)
  expect(browserCookies.some((cookie) => cookie.name === 'siq_csrf_token' && !cookie.httpOnly)).toBe(true)
  expect(await page.evaluate(() => localStorage.getItem('access_token'))).toBeNull()
  expect((await page.request.get('/api/workspace/summary')).status()).toBe(200)

  restartApi()
  await expect.poll(async () => {
    try {
      return (await page.request.get(`${required(backendURL, 'SIQ_E2E_BACKEND_URL')}/health`)).status()
    } catch {
      return 0
    }
  }).toBe(200)

  await page.reload()
  await expect(page.getByRole('heading', { name: '工作平台' })).toBeVisible()
  expect((await page.request.get('/api/workspace/summary')).status()).toBe(200)

  await page.getByRole('button', { name: /Compose Smoke/ }).click()
  const logoutResponsePromise = page.waitForResponse((response) => (
    response.url().endsWith('/api/auth/logout') && response.request().method() === 'POST'
  ))
  await page.getByRole('menuitem', { name: '退出登录' }).click()
  expect((await logoutResponsePromise).status()).toBe(200)
  await expect(page).toHaveURL(/\/login$/)
  await expect(page.getByRole('button', { name: '登录', exact: true })).toBeVisible()

  const cookiesAfterLogout = await page.context().cookies()
  expect(cookiesAfterLogout.some((cookie) => cookie.name === 'siq_access_token')).toBe(false)
  expect(cookiesAfterLogout.some((cookie) => cookie.name === 'siq_csrf_token')).toBe(false)
  expect((await page.request.get('/api/workspace/summary')).status()).toBe(401)
})
