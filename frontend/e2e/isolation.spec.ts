/**
 * Two visitors. Each browser context is a separate anonymous session; the
 * second one must not be able to see or reach anything of the first, by
 * address in the app or by ID through the API, and deleting one session
 * leaves the other untouched.
 */
import { API_URL } from './support/env.ts'
import type { Generation, JobListResponse } from '../src/lib/types.ts'
import { SECOND_PERSON } from './support/data.ts'
import {
  apiGet,
  extractProfile,
  heading,
  reachWorkspaceWithSample,
  requireToken,
} from './support/steps.ts'
import { expect, test } from './support/test.ts'

test('a second session cannot see or reach the first session’s data', async ({ page, browser, request }) => {
  // ---------------------------------------------- First visitor: Jordan
  const firstGenerationId = await reachWorkspaceWithSample(page)
  const firstToken = await requireToken(page)
  const generation = (await (
    await apiGet(request, firstToken, `/api/generations/${firstGenerationId}`)
  ).json()) as Generation
  const firstEvidenceId = generation.retrieved_evidence_ids[0]
  const jobs = (await (await apiGet(request, firstToken, '/api/jobs')).json()) as JobListResponse
  const firstJobId = jobs.jobs[0].job_id
  const firstSessionPaths = [
    `/api/generations/${firstGenerationId}`,
    `/api/evidence/${firstEvidenceId}`,
    `/api/jobs/${firstJobId}`,
  ]

  // --------------------------------------------- Second visitor: Morgan
  const secondContext = await browser.newContext()
  const secondPage = await secondContext.newPage()
  try {
    // A new context starts with nothing: no session, no profile.
    await secondPage.goto('/profile')
    await expect(secondPage.getByRole('heading', { name: 'No profile yet' })).toBeVisible()

    await extractProfile(secondPage, { resume: SECOND_PERSON.resume })
    const secondToken = await requireToken(secondPage)
    expect(secondToken).not.toBe(firstToken)

    // The second visitor sees only their own profile.
    await expect(secondPage.getByRole('textbox', { name: 'Name' })).toHaveValue(SECOND_PERSON.name)
    await expect(secondPage.getByRole('group', { name: SECOND_PERSON.role })).toBeVisible()
    const secondProfileText = await secondPage.getByRole('main').innerText()
    expect(secondProfileText).not.toMatch(/Jordan Rivera|Brightloom|Quillfeather/)
    const secondProfile = await (await apiGet(request, secondToken, '/api/profile')).json()
    expect(JSON.stringify(secondProfile)).not.toMatch(/Jordan Rivera|Brightloom|Quillfeather/)

    // Addresses of the first visitor's draft and job lead nowhere in the second tab.
    await secondPage.goto(`/workspace/${firstGenerationId}`)
    await expect(secondPage.getByRole('heading', { name: 'Draft not found' })).toBeVisible()
    await expect(secondPage.getByText('Jordan Rivera')).toHaveCount(0)
    await secondPage.goto(`/job?job=${firstJobId}`)
    await expect(secondPage.getByRole('heading', { name: 'This job is not available' })).toBeVisible()

    // The API answers 404 for the first visitor's IDs, exactly as for unknown ones.
    for (const apiPath of firstSessionPaths) {
      const foreign = await apiGet(request, secondToken, apiPath)
      expect(foreign.status(), apiPath).toBe(404)
      expect((await foreign.json()).error.code, apiPath).toBe('not_found')
    }
    const secondHeaders = { Authorization: `Bearer ${secondToken}` }
    const foreignEdit = await request.patch(`${API_URL}/api/generations/${firstGenerationId}`, {
      headers: secondHeaders,
      data: { expected_revision: generation.revision, edits: [] },
    })
    expect(foreignEdit.status()).toBe(404)
    const foreignValidate = await request.post(
      `${API_URL}/api/generations/${firstGenerationId}/validate`,
      { headers: secondHeaders },
    )
    expect(foreignValidate.status()).toBe(404)
    expect((await (await apiGet(request, secondToken, '/api/jobs')).json()).jobs).toEqual([])
    expect((await (await apiGet(request, secondToken, '/api/generations')).json()).generations).toEqual([])

    // The second visitor deletes their data through the app...
    await secondPage.goto('/profile')
    await secondPage.getByRole('button', { name: 'Clear my data' }).click()
    await secondPage.getByRole('button', { name: 'Delete everything' }).click()
    await expect(heading(secondPage, /A resume for this job/)).toBeVisible()
    expect((await apiGet(request, secondToken, '/api/profile')).status()).toBe(401)
  } finally {
    await secondContext.close()
  }

  // ...and the first visitor's data is all still there.
  for (const apiPath of firstSessionPaths) {
    expect((await apiGet(request, firstToken, apiPath)).status(), apiPath).toBe(200)
  }
  await page.reload()
  await expect(heading(page, 'Your tailored draft')).toBeVisible()
  await expect(
    page.getByRole('tabpanel', { name: 'Resume' }).getByRole('heading', { name: 'Jordan Rivera' }),
  ).toBeVisible()
})
