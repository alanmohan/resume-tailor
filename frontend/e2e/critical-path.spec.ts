/**
 * The whole workflow as a new visitor experiences it, with the fictional
 * sample profile and job: input -> profile review -> job analysis ->
 * generation -> citations -> editing -> revalidation -> print -> clear data.
 */
import { SAMPLE_JOB, SAMPLE_SOURCES } from '../src/sample/sampleData.ts'
import type { Generation } from '../src/lib/types.ts'
import {
  ACKNOWLEDGEMENT,
  DEMO_BANNER,
  apiGet,
  claimWithText,
  editClaim,
  generationIdFromUrl,
  heading,
  pinClaim,
  printCalls,
  requireToken,
  screenshot,
} from './support/steps.ts'
import { expect, test } from './support/test.ts'

const ML_ROLE = 'Machine Learning Engineer at Brightloom Labs'
const RAG_BULLET =
  'Built a retrieval-augmented generation (RAG) service in Python and FastAPI that answers support questions over 40,000 help-center articles'
const ON_CALL_BULLET = 'Bullet 9 of Machine Learning Engineer at Brightloom Labs'

/** Wording the app must never use for the coverage figure. */
const FORBIDDEN_WORDING = /match score|ats score|fit score|job fit|% match|likelihood|chance of/i

test('a visitor tailors a resume from the sample profile and clears the data', async ({
  page,
  context,
  request,
}) => {
  const requestedUrls: string[] = []
  page.on('request', (sent) => requestedUrls.push(sent.url()))

  // ---------------------------------------------------------------- Start
  await page.goto('/')
  await expect(heading(page, /A resume for this job/)).toBeVisible()
  await expect(page.getByText(DEMO_BANNER)).toBeVisible()

  const notice = page.getByRole('region', { name: 'How your data is handled' })
  await expect(notice).toContainText('for at most 24 hours')
  await expect(notice).toContainText('Access is tied to this browser tab')
  await expect(notice).toContainText('deletes everything immediately')
  await screenshot(page, 'desktop-1-start')

  await page.getByRole('button', { name: 'Try sample profile' }).click()
  await expect(page.getByText('Fictional sample loaded.')).toBeVisible()
  await expect(page.getByRole('textbox', { name: 'Resume/CV text' })).toHaveValue(/Jordan Rivera/)

  // The notice has to be acknowledged before anything is sent.
  await page.getByRole('button', { name: 'Extract my profile' }).click()
  await expect(page.getByText('Confirm that you have read how your data is handled.')).toBeVisible()
  expect(requestedUrls.filter((url) => url.includes('/api/'))).toEqual([])

  await page.getByRole('checkbox', { name: ACKNOWLEDGEMENT }).check()
  await page.getByRole('button', { name: 'Extract my profile' }).click()

  // ------------------------------------------------------- Profile review
  await expect(heading(page, 'Review your profile')).toBeVisible()
  await expect(page.getByText(/^Sources: Resume \([\d,]+ characters\), LinkedIn profile/)).toBeVisible()

  // Records are grouped by kind.
  const experience = page.getByRole('region', { name: 'Experience' })
  await expect(experience.getByRole('group', { name: ML_ROLE })).toBeVisible()
  await expect(
    experience.getByRole('group', { name: 'Software Engineer at Quillfeather Software' }),
  ).toBeVisible()
  await expect(
    page.getByRole('region', { name: 'Projects' }).getByRole('group', { name: /^TrailNotes/ }),
  ).toBeVisible()
  await expect(
    page.getByRole('region', { name: 'Education' }).getByRole('group', { name: /^B\.S\. in Computer Science/ }),
  ).toBeVisible()
  await expect(page.getByRole('region', { name: 'Skills' }).getByRole('group').first()).toBeVisible()

  // The two sources disagree on a start date: both values are shown with their excerpts.
  const conflicts = page.getByRole('region', { name: 'Conflicts between your sources' })
  await expect(conflicts.getByText('Unresolved')).toBeVisible()
  await expect(
    conflicts.getByRole('figure', { name: 'Source: Resume' }),
  ).toContainText('Software Engineer - Quillfeather Software (Jul 2022 - Jul 2024)')
  await expect(
    conflicts.getByRole('figure', { name: 'Source: LinkedIn profile' }),
  ).toContainText('Software Engineer - Quillfeather Software (Jun 2022 - Jul 2024)')
  await expect(page.getByText('1 item needs review')).toBeVisible()
  const confirmButton = page.getByRole('button', { name: 'Confirm profile and build evidence index' })
  await expect(confirmButton).toBeDisabled()
  await expect(page.getByText('Resolve or dismiss the 1 open conflict above before confirming.')).toBeVisible()

  // Every extracted bullet opens the exact text it came from.
  const mlRole = experience.getByRole('group', { name: ML_ROLE })
  const firstBullet = mlRole
    .getByRole('listitem')
    .filter({ has: page.getByRole('textbox', { name: `Bullet 1 of ${ML_ROLE}` }) })
  await firstBullet.getByRole('button', { name: 'View source' }).click()
  const sourcePopover = page.getByRole('dialog')
  await expect(sourcePopover.getByRole('blockquote')).toHaveText(RAG_BULLET)
  await expect(sourcePopover.getByText('Source: Resume')).toBeVisible()
  await screenshot(page, 'desktop-2-profile')
  await page.keyboard.press('Escape')
  await expect(sourcePopover).toBeHidden()

  // Editing a bullet marks it as the user's own statement.
  const onCallBullet = mlRole
    .getByRole('listitem')
    .filter({ has: page.getByRole('textbox', { name: ON_CALL_BULLET }) })
  await page
    .getByRole('textbox', { name: ON_CALL_BULLET })
    .fill('On the on-call rotation for the support assistant one week in every 6, handling escalations')
  await expect(onCallBullet.getByText('Edited by you')).toBeVisible()
  await expect(onCallBullet.getByRole('button', { name: 'View original source' })).toBeVisible()

  // Deciding the conflict saves the edit with it.
  await conflicts.getByRole('button', { name: 'Mark resolved' }).click()
  await expect(conflicts.getByText('Every conflict has a decision.')).toBeVisible()
  await expect(conflicts.getByText('Resolved', { exact: true })).toBeVisible()
  await expect(onCallBullet.getByText('Edited by you')).toBeVisible()
  await expect(page.getByText('All changes are saved.')).toBeVisible()

  await confirmButton.click()
  const confirmed = page.getByRole('status').filter({ hasText: 'Profile confirmed' })
  await expect(confirmed).toContainText(/\d+ evidence records are indexed/)
  await screenshot(page, 'desktop-3-profile-confirmed')

  // ----------------------------------------------------------- Target job
  await confirmed.getByRole('link', { name: 'Continue to target job' }).click()
  await expect(heading(page, 'Target job')).toBeVisible()
  await page.getByRole('button', { name: 'Use sample job' }).click()
  await expect(page.getByRole('textbox', { name: 'Role title (optional)' })).toHaveValue(SAMPLE_JOB.title)
  await expect(page.getByRole('textbox', { name: 'Company (optional)' })).toHaveValue(SAMPLE_JOB.company)
  await screenshot(page, 'desktop-4-job-form')
  await page.getByRole('button', { name: 'Analyze job' }).click()

  await expect(heading(page, 'Review the job requirements')).toBeVisible()
  await expect(page.getByRole('region', { name: /^Required \(\d+\)$/ })).toBeVisible()
  await expect(page.getByRole('region', { name: /^Preferred \(\d+\)$/ })).toBeVisible()

  // Correct one requirement and save it.
  const awsRequirement = page.getByRole('group', { name: 'Requirement 14', exact: true })
  const awsText = awsRequirement.getByRole('textbox', { name: 'Requirement 14 text', exact: true })
  await expect(awsText).toHaveValue('Experience with AWS')
  await awsText.fill('Experience deploying services on AWS')
  await expect(awsRequirement.getByText('User edited')).toBeVisible()
  const jobStatus = page.locator('#job-action-status')
  await expect(jobStatus).toHaveText('You have unsaved changes. Save them before generating.')
  await page.getByRole('button', { name: 'Save changes' }).click()
  await expect(jobStatus).toHaveText('All changes are saved.')
  await screenshot(page, 'desktop-5-job-review')

  await page.getByRole('button', { name: 'Generate tailored resume and cover letter' }).click()

  // ------------------------------------------------------------ Workspace
  await page.waitForURL(/\/workspace\/[^/]+$/)
  await expect(heading(page, 'Your tailored draft')).toBeVisible()
  const generationId = generationIdFromUrl(page)
  const token = await requireToken(page)
  await expect(page.getByText(DEMO_BANNER)).toBeVisible()

  // The resume: identity and history exactly as confirmed.
  const resume = page.getByRole('tabpanel', { name: 'Resume' })
  await expect(resume.getByRole('heading', { name: 'Jordan Rivera' })).toBeVisible()
  await expect(resume.getByText('jordan.rivera@example.com')).toBeVisible()
  const resumeExperience = resume.getByRole('region', { name: 'Experience' })
  await expect(resumeExperience.getByRole('heading', { name: 'Machine Learning Engineer' })).toBeVisible()
  await expect(resumeExperience.getByText('Brightloom Labs')).toBeVisible()
  await expect(resumeExperience.getByText('Aug 2024 - Present')).toBeVisible()
  await expect(resume.getByRole('region', { name: 'Skills' })).toBeVisible()
  await screenshot(page, 'desktop-6-workspace-resume')

  // The cover letter addresses the role and company of the job.
  await page.getByRole('tab', { name: 'Cover letter' }).click()
  const coverLetter = page.getByRole('tabpanel', { name: 'Cover letter' })
  await expect(coverLetter.locator('.ws-claim-text').first()).toContainText(
    `${SAMPLE_JOB.title} role at ${SAMPLE_JOB.company}`,
  )
  await expect(coverLetter.getByText('No citation needed').first()).toBeVisible()
  // The application closes the letter itself, with the name from the confirmed profile.
  await expect(coverLetter.locator('.ws-letter-signoff')).toHaveText(/^Sincerely,\s*Jordan Rivera$/)
  await screenshot(page, 'desktop-7-workspace-cover-letter')
  await page.getByRole('tab', { name: 'Resume' }).click()

  // An evidence badge opens the exact excerpt, its source and the role it belongs to.
  const ragClaim = await pinClaim(page, claimWithText(resumeExperience, RAG_BULLET))
  const sidePanel = page.getByRole('complementary', { name: 'Evidence and coverage' })
  const badge = ragClaim.getByRole('button', { name: /^Show evidence \d+ for this statement$/ }).first()
  await badge.click()
  await expect(badge).toHaveAttribute('aria-pressed', 'true')
  await expect(sidePanel.getByRole('heading', { name: /^Evidence \d+$/ })).toBeVisible()
  const excerpt = sidePanel.getByRole('blockquote')
  await expect(excerpt).toHaveText(RAG_BULLET)
  const resumeSource = SAMPLE_SOURCES.find((source) => source.source_type === 'resume')
  expect(resumeSource?.text).toContain(await excerpt.innerText())
  await expect(sidePanel.getByText('Source: Resume', { exact: true })).toBeVisible()
  await expect(sidePanel.getByText('Source type: Resume')).toBeVisible()
  await expect(sidePanel.getByText(`${ML_ROLE} (role)`)).toBeVisible()
  await expect(sidePanel.getByText('Extracted from your source')).toBeVisible()
  await expect(sidePanel.getByRole('button', { name: /^Go to statement:/ }).first()).toBeVisible()
  await screenshot(page, 'desktop-8-workspace-evidence')

  // Coverage: the server's own counts, the formula's result and the disclaimer.
  const response = await apiGet(request, token, `/api/generations/${generationId}`)
  const generation = (await response.json()) as Generation
  const summary = generation.coverage_summary
  await sidePanel.getByRole('tab', { name: 'Coverage' }).click()
  await expect(sidePanel.getByRole('heading', { name: 'Evidence coverage' })).toBeVisible()
  expect(summary.percent).not.toBeNull()
  expect(summary.percent).toBeCloseTo(
    (100 * (summary.supported + 0.5 * summary.partial)) / summary.assessed,
    1,
  )
  await expect(sidePanel.getByText(`${summary.percent}%`, { exact: true })).toBeVisible()
  const counts = sidePanel.getByRole('list', { name: 'Requirement counts' })
  await expect(counts.getByRole('listitem')).toHaveText([
    `${summary.supported} supported`,
    `${summary.partial} partially supported`,
    `${summary.missing} no evidence found`,
    `${summary.uncertain} uncertain not counted`,
  ])
  await expect(
    sidePanel.getByText('It is not a measure of job suitability, ATS compatibility or hiring probability.'),
  ).toBeVisible()
  await expect(sidePanel.getByText('Experience deploying services on AWS')).toBeVisible()
  expect(await page.locator('body').innerText()).not.toMatch(FORBIDDEN_WORDING)
  await screenshot(page, 'desktop-9-workspace-coverage')

  // ------------------------------------------------- Edit and revalidate
  await expect(page.getByText('No statements are flagged.')).toBeVisible()
  await editClaim(ragClaim, 'Built a retrieval-augmented generation (RAG) service in Python and FastAPI')
  await expect(ragClaim.getByText('Supported')).toBeHidden()
  await expect(page.getByText('Edited - revalidate before export')).toBeVisible()
  await expect(page.getByText('1 edited, not revalidated')).toBeVisible()
  await expect(page.getByText('No statements are flagged.')).toBeHidden()

  // Printing now asks for a review first and does not print behind the user's back.
  await page.getByRole('button', { name: 'Print / Save as PDF' }).click()
  const printGate = page.getByRole('dialog', { name: 'Review before printing' })
  await expect(printGate).toContainText('Some statements were edited after the last validation.')
  await expect(printGate.getByRole('button', { name: 'Print anyway' })).toBeDisabled()
  await printGate.getByRole('button', { name: 'Cancel' }).click()
  await expect(printGate).toBeHidden()
  expect(await printCalls(page)).toBe(0)

  await page.getByRole('button', { name: 'Revalidate' }).click()
  await expect(page.getByText('Edited - revalidate before export')).toBeHidden()
  await expect(page.getByText('Validated', { exact: true })).toBeVisible()
  await expect(ragClaim.getByText('Supported')).toBeVisible()
  await expect(ragClaim.getByText('Edited by you')).toBeVisible()
  await expect(page.getByText('No statements are flagged.')).toBeVisible()

  // ---------------------------------------------------------------- Print
  await page.getByRole('button', { name: 'Print / Save as PDF' }).click()
  await expect.poll(() => printCalls(page)).toBe(1)
  await expect(printGate).toBeHidden()

  await page.emulateMedia({ media: 'print' })
  // The document itself is on the page...
  await expect(resume.getByRole('heading', { name: 'Jordan Rivera' })).toBeVisible()
  await expect(ragClaim.locator('.ws-claim-text')).toBeVisible()
  await expect(resumeExperience.getByText('Aug 2024 - Present')).toBeVisible()
  // ...and nothing that belongs to the review tooling is.
  await expect(page.getByRole('banner')).toBeHidden()
  await expect(page.getByText(DEMO_BANNER)).toBeHidden()
  await expect(page.getByRole('contentinfo')).toBeHidden()
  await expect(heading(page, 'Your tailored draft')).toBeHidden()
  await expect(page.getByRole('tablist').first()).toBeHidden()
  await expect(sidePanel).toBeHidden()
  await expect(page.getByText('Validated', { exact: true })).toBeHidden()
  await expect(page.getByRole('button', { name: 'Print / Save as PDF' })).toBeHidden()
  await expect(ragClaim.getByRole('button', { name: /^Show evidence/ }).first()).toBeHidden()
  await expect(ragClaim.getByRole('button', { name: 'Edit this statement' })).toBeHidden()
  await expect(ragClaim.getByText('Supported')).toBeHidden()
  for (const control of await page.locator('.ws-paper button, .ws-paper [data-slot="badge"]').all()) {
    await expect(control).toBeHidden()
  }
  await screenshot(page, 'print-1-resume')
  // Only the active document prints.
  await expect(page.locator('.ws-document-panel[data-print-target="true"]')).toHaveCount(1)
  await expect(coverLetter).toBeHidden()
  await page.emulateMedia({ media: 'screen' })

  // -------------------------------------------------------- Clear my data
  const evidenceId = generation.retrieved_evidence_ids[0]
  expect((await apiGet(request, token, `/api/evidence/${evidenceId}`)).status()).toBe(200)

  await page.getByRole('button', { name: 'Clear my data' }).click()
  const clearDialog = page.getByRole('alertdialog', { name: 'Delete all your data?' })
  await expect(clearDialog.getByRole('listitem')).toHaveText([
    'The resume, LinkedIn and notes text you pasted',
    'Your extracted profile and its evidence index',
    'Saved job descriptions and their requirements',
    'Every generated resume and cover letter',
  ])
  await clearDialog.getByRole('button', { name: 'Delete everything' }).click()

  await expect(heading(page, /A resume for this job/)).toBeVisible()
  expect(new URL(page.url()).pathname).toBe('/')
  await expect(page.getByText('Your data was deleted')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Clear my data' })).toBeHidden()
  await expect(page.getByRole('textbox', { name: 'Resume/CV text' })).toHaveValue('')

  // Nothing is left in the browser, and the token was never put anywhere else.
  expect(await page.evaluate(() => window.sessionStorage.length)).toBe(0)
  expect(await page.evaluate(() => JSON.stringify(window.localStorage))).not.toContain(token)
  expect(await context.cookies()).toEqual([])
  expect(requestedUrls.filter((url) => url.includes(token))).toEqual([])

  // The server no longer accepts the old token for anything.
  for (const apiPath of [
    '/api/session',
    '/api/profile',
    '/api/jobs',
    `/api/generations/${generationId}`,
    `/api/evidence/${evidenceId}`,
  ]) {
    const rejected = await apiGet(request, token, apiPath)
    expect(rejected.status(), apiPath).toBe(401)
    expect((await rejected.json()).error.code, apiPath).toBe('unauthorized')
  }
})
