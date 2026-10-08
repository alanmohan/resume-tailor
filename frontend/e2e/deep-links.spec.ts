/**
 * Deep links: every screen can be opened by its address and survives a
 * refresh in the same tab, and addresses that lead nowhere say so.
 */
import type { Generation } from '../src/lib/types.ts'
import {
  apiGet,
  extractSampleProfile,
  heading,
  reachWorkspaceWithSample,
  requireToken,
  storedSession,
} from './support/steps.ts'
import { expect, test } from './support/test.ts'

test('every screen survives a refresh in the same tab', async ({ page, request }) => {
  const generationId = await reachWorkspaceWithSample(page)
  const workspacePath = `/workspace/${generationId}`
  const session = await storedSession(page)

  await page.reload()
  await expect(heading(page, 'Your tailored draft')).toBeVisible()
  await expect(
    page.getByRole('tabpanel', { name: 'Resume' }).getByRole('heading', { name: 'Jordan Rivera' }),
  ).toBeVisible()

  await page.goto('/profile')
  await page.reload()
  await expect(heading(page, 'Review your profile')).toBeVisible()
  await expect(page.getByText('Profile confirmed', { exact: true })).toBeVisible()
  await expect(
    page.getByRole('group', { name: 'Machine Learning Engineer at Brightloom Labs' }),
  ).toBeVisible()

  // The form for a new job, with the existing draft listed under it.
  await page.goto('/job')
  await page.reload()
  await expect(heading(page, 'Target job')).toBeVisible()
  await expect(page.getByRole('textbox', { name: 'Job description' })).toHaveValue('')
  const drafts = page.getByRole('region', { name: 'Your drafts' })
  await expect(drafts.getByRole('link', { name: /^Draft from/ })).toHaveAttribute('href', workspacePath)
  await expect(drafts.getByText('Applied Machine Learning Engineer at', { exact: false })).toBeVisible()

  // The draft's own job by its address: read-only, with its draft listed.
  const response = await apiGet(request, await requireToken(page), `/api/generations/${generationId}`)
  const generation = (await response.json()) as Generation
  await page.goto(`/job?job=${generation.job_id}`)
  await page.reload()
  await expect(heading(page, 'Your target job')).toBeVisible()
  const details = page.getByRole('region', { name: 'Job details' })
  await expect(details.getByText('Applied Machine Learning Engineer', { exact: true })).toBeVisible()
  await expect(page.getByRole('main').getByRole('textbox')).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Generate a new draft' })).toBeEnabled()
  await expect(
    page.getByRole('region', { name: 'Drafts for this job' }).getByRole('link', { name: /^Draft from/ }),
  ).toHaveAttribute('href', workspacePath)
  await expect(page.getByRole('link', { name: 'Start a different job' })).toHaveAttribute('href', '/job')

  // The same session throughout, and the step navigation still knows the draft.
  expect(await storedSession(page)).toEqual(session)
  const steps = page.getByRole('navigation', { name: 'Steps' })
  await steps.getByRole('link', { name: 'Workspace' }).click()
  await expect(page).toHaveURL(workspacePath)
  await expect(heading(page, 'Your tailored draft')).toBeVisible()
})

test('an address that leads nowhere shows a not-found state', async ({ page }) => {
  await extractSampleProfile(page)

  await page.goto('/workspace/0123456789abcdef0123456789abcdef')
  await expect(page.getByRole('heading', { name: 'Draft not found' })).toBeVisible()
  await expect(page.getByRole('link', { name: 'Go to target job' })).toBeVisible()

  await page.goto('/job?job=0123456789abcdef0123456789abcdef')
  await expect(page.getByRole('heading', { name: 'This job is not available' })).toBeVisible()

  await page.goto('/no-such-screen')
  await expect(page.getByRole('heading', { name: 'Page not found' })).toBeVisible()
  await page.getByRole('link', { name: 'Go to Start' }).click()
  await expect(heading(page, /A resume for this job/)).toBeVisible()
})

test('a deep link opened without a session points back to Start', async ({ page }) => {
  await page.goto('/workspace/0123456789abcdef0123456789abcdef')
  await expect(page.getByRole('heading', { name: 'No draft to show' })).toBeVisible()

  await page.goto('/profile')
  await expect(page.getByRole('heading', { name: 'No profile yet' })).toBeVisible()

  await page.goto('/job')
  await expect(page.getByRole('heading', { name: 'No profile yet' })).toBeVisible()
  await page.getByRole('link', { name: 'Go to Start' }).click()
  await expect(heading(page, /A resume for this job/)).toBeVisible()
  expect(await storedSession(page)).toBeNull()
})
