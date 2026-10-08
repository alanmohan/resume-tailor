/**
 * Failure handling. Provider failures are injected at the network layer (the
 * fake provider never fails by itself); the ended session is real: it is
 * revoked on the server while the page is open.
 */
import type { Page, Request } from '@playwright/test'
import { API_URL } from './support/env.ts'
import { SECOND_PERSON } from './support/data.ts'
import {
  ACKNOWLEDGEMENT,
  TAILOR_BUTTON,
  apiGet,
  confirmProfile,
  extractSampleProfile,
  fillJobForm,
  heading,
  injectedApiError,
  requireToken,
  storedSession,
} from './support/steps.ts'
import { expect, test } from './support/test.ts'

/** Record every API request the page sends that matches the method and path. */
function recordRequests(page: Page, method: string, apiPath: string): Request[] {
  const seen: Request[] = []
  page.on('request', (sent) => {
    if (sent.method() === method && sent.url() === `${API_URL}${apiPath}`) seen.push(sent)
  })
  return seen
}

test('a provider timeout during extraction keeps the pasted text and Retry succeeds', async ({ page }) => {
  let attempts = 0
  await page.route(`${API_URL}/api/profiles/ingest`, async (route) => {
    attempts += 1
    if (attempts === 1) {
      await route.fulfill(
        injectedApiError(504, 'provider_timeout', 'The AI provider took too long to respond. Please try again.'),
      )
    } else {
      await route.continue()
    }
  })

  await page.goto('/')
  const resumeText = page.getByRole('textbox', { name: 'Resume/CV text' })
  const resumeLabel = page.getByRole('textbox', { name: 'Resume/CV source label' })
  await resumeText.fill(SECOND_PERSON.resume)
  await resumeLabel.fill('My CV, autumn version')
  await page.getByRole('checkbox', { name: ACKNOWLEDGEMENT }).check()
  await page.getByRole('button', { name: 'Extract my profile' }).click()

  const alert = page.getByRole('alert').filter({ hasText: 'Your profile was not extracted' })
  await expect(alert).toContainText('The AI provider took too long to respond. Please try again.')
  await expect(alert).toContainText('Request ID: e2e-injected-failure')
  // Nothing the visitor entered is lost, and the form can be used again.
  await expect(resumeText).toHaveValue(SECOND_PERSON.resume)
  await expect(resumeLabel).toHaveValue('My CV, autumn version')
  await expect(page.getByRole('checkbox', { name: ACKNOWLEDGEMENT })).toBeChecked()
  await expect(page.getByRole('button', { name: 'Extract my profile' })).toBeEnabled()
  expect(new URL(page.url()).pathname).toBe('/')

  await alert.getByRole('button', { name: 'Retry' }).click()

  await expect(heading(page, 'Review your profile')).toBeVisible()
  await expect(page.getByText(/^Sources: My CV, autumn version \(/)).toBeVisible()
  await expect(page.getByRole('group', { name: SECOND_PERSON.role })).toBeVisible()
  expect(attempts).toBe(2)
})

test('a generation that fails is retried with the same idempotency key, without a second job analysis', async ({ page, request }) => {
  const keys: (string | undefined)[] = []
  await page.route(`${API_URL}/api/generations`, async (route) => {
    if (route.request().method() !== 'POST') return route.continue()
    keys.push(route.request().headers()['idempotency-key'])
    if (keys.length === 1) {
      await route.fulfill(
        injectedApiError(503, 'provider_rate_limited', 'The AI provider is busy. Please try again shortly.'),
      )
    } else {
      await route.continue()
    }
  })

  const analyses = recordRequests(page, 'POST', '/api/jobs')

  await extractSampleProfile(page)
  await confirmProfile(page)
  await fillJobForm(page, 'sample')
  await page.getByRole('button', { name: TAILOR_BUTTON }).click()

  const alert = page.getByRole('alert').filter({ hasText: 'The draft was not generated' })
  await expect(alert).toContainText('The AI provider is busy. Please try again shortly.')
  // The job was analyzed and is kept; only the second step failed.
  await expect(heading(page, 'Your target job')).toBeVisible()
  await expect(page).toHaveURL(/\/job\?job=[^&]+$/)
  const steps = page.getByRole('status', { name: 'Progress' }).getByRole('listitem')
  await expect(steps).toHaveText([
    /1\. Analyzing the job description\s*Done/,
    /2\. Writing your resume and cover letter\s*Failed/,
  ])

  await alert.getByRole('button', { name: 'Retry' }).click()

  await page.waitForURL(/\/workspace\/[^/]+$/)
  await expect(heading(page, 'Your tailored draft')).toBeVisible()
  expect(keys).toHaveLength(2)
  expect(keys[0]).toMatch(/^[0-9a-f-]{36}$/)
  expect(keys[1]).toBe(keys[0])
  // Retry repeated the generation only: the job was not analyzed (and paid for) again.
  expect(analyses).toHaveLength(1)

  // One draft exists on the server for that key: the retry did not create a second one.
  const token = await requireToken(page)
  const list = await (await apiGet(request, token, '/api/generations')).json()
  expect(list.generations).toHaveLength(1)
})

test('a session that ends in the middle of the flow is explained and can be restarted', async ({
  page,
  request,
}) => {
  await extractSampleProfile(page)
  const token = await requireToken(page)

  // The session is revoked on the server while the page still shows the profile.
  const revoked = await request.delete(`${API_URL}/api/session`, {
    headers: { Authorization: `Bearer ${token}` },
  })
  expect(revoked.status()).toBe(200)

  await page.getByRole('button', { name: 'Mark resolved' }).click()

  await expect(page.getByRole('heading', { name: 'Your session has ended' })).toBeVisible()
  await expect(heading(page, 'Review your profile')).toBeHidden()
  expect(await storedSession(page)).toBeNull()

  await page.getByRole('button', { name: 'Start over' }).click()
  await expect(heading(page, /A resume for this job/)).toBeVisible()
  await expect(page.getByRole('button', { name: 'Clear my data' })).toBeHidden()
})

test('double-clicking a submit button sends one request', async ({ page }) => {
  // Hold each response back briefly so the second click lands while the first is pending.
  for (const apiPath of ['/api/profiles/ingest', '/api/jobs', '/api/generations']) {
    await page.route(`${API_URL}${apiPath}`, async (route) => {
      if (route.request().method() === 'POST') await new Promise((done) => setTimeout(done, 400))
      await route.continue()
    })
  }
  const sessions = recordRequests(page, 'POST', '/api/sessions')
  const ingests = recordRequests(page, 'POST', '/api/profiles/ingest')
  const analyses = recordRequests(page, 'POST', '/api/jobs')
  const generations = recordRequests(page, 'POST', '/api/generations')

  await page.goto('/')
  await page.getByRole('button', { name: 'Try sample profile' }).click()
  await page.getByRole('checkbox', { name: ACKNOWLEDGEMENT }).check()
  await page.getByRole('button', { name: 'Extract my profile' }).dblclick()
  await expect(heading(page, 'Review your profile')).toBeVisible()
  expect(sessions).toHaveLength(1)
  expect(ingests).toHaveLength(1)

  await confirmProfile(page)
  await page.getByRole('link', { name: 'Continue to target job' }).first().click()
  await page.getByRole('button', { name: 'Use sample job' }).click()
  await page.getByRole('button', { name: TAILOR_BUTTON }).dblclick()
  // While the run is in progress its step list is on screen and the button is off.
  await expect(page.getByRole('status', { name: 'Progress' }).getByRole('listitem')).toHaveCount(2)
  await expect(heading(page, 'Your tailored draft')).toBeVisible()
  expect(analyses).toHaveLength(1)
  expect(generations).toHaveLength(1)
})
