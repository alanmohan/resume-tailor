/**
 * Reviewing a draft against the real API: validation after a dishonest edit,
 * the download gate, regeneration, coverage corrections, copying, and drafts
 * that go out of date.
 */
import {
  claimWithText,
  countDownloads,
  editClaim,
  expectPdfDownload,
  heading,
  pinClaim,
  reachWorkspaceWithSample,
  workspaceOpened,
} from './support/steps.ts'
import { expect, test } from './support/test.ts'

const DOCKER_BULLET = /^Packaged model services as Docker images/
const KUBERNETES_EDIT = 'Packaged model services as Docker images and ran them on Kubernetes'

test('a skill the profile does not contain is flagged after revalidation and gates the download', async ({
  page,
}) => {
  await reachWorkspaceWithSample(page)
  const downloads = countDownloads(page)
  const resume = page.getByRole('tabpanel', { name: 'Resume' })
  const claim = await pinClaim(page, claimWithText(resume, DOCKER_BULLET))
  await expect(claim.getByText('Supported')).toBeVisible()

  // The profile mentions Docker only. Claiming Kubernetes must not pass validation.
  await editClaim(claim, KUBERNETES_EDIT)
  await page.getByRole('button', { name: 'Revalidate' }).click()

  await expect(claim.getByText('Unsupported')).toBeVisible()
  await expect(claim.getByText('Supported', { exact: true })).toBeHidden()
  await expect(claim).toContainText(/Kubernetes/)
  await expect(claim.getByText(/\d+ warnings?/)).toBeVisible()
  await expect(page.getByText('1 unsupported')).toBeVisible()
  await expect(page.getByText('No statements are flagged.')).toBeHidden()
  // The text itself is the visitor's: revalidation never rewrites it.
  await expect(claim.locator('.ws-claim-text')).toHaveText(KUBERNETES_EDIT)

  // Downloading lists the flagged statement and needs an explicit acknowledgement.
  await page.getByRole('button', { name: 'Download PDF' }).click()
  const downloadGate = page.getByRole('dialog', { name: 'Review before downloading' })
  await expect(downloadGate).toContainText('1 statement has not been confirmed by validation.')
  await expect(downloadGate.getByText(KUBERNETES_EDIT)).toBeVisible()
  await expect(downloadGate.getByText('Unsupported')).toBeVisible()
  const downloadAnyway = downloadGate.getByRole('button', { name: 'Download anyway' })
  await expect(downloadAnyway).toBeDisabled()

  // "Review" closes the dialog and goes to the statement instead of downloading.
  await downloadGate.getByRole('button', { name: 'Review' }).click()
  await expect(downloadGate).toBeHidden()
  await expect(claim).toBeFocused()
  expect(downloads()).toBe(0)

  // The browser's own print command (Ctrl/Cmd+P, the browser menu) never
  // opens that dialog. Until the statement is acknowledged, what it would put
  // on paper is a short notice, not the document.
  const printNotice = page.getByText('Review the flagged statements in the app before printing')
  await expect(printNotice).toBeHidden()
  await page.emulateMedia({ media: 'print' })
  await expect(printNotice).toBeVisible()
  await expect(resume).toBeHidden()
  await expect(claim.locator('.ws-claim-text')).toBeHidden()
  await expect(page.getByRole('tabpanel', { name: 'Cover letter' })).toBeHidden()
  await page.emulateMedia({ media: 'screen' })
  await expect(claim.locator('.ws-claim-text')).toBeVisible()

  // Copying is an export too and goes through the same review.
  await page.evaluate(() => navigator.clipboard.writeText('clipboard before the test'))
  await page.getByRole('button', { name: 'Copy', exact: true }).click()
  const copyGate = page.getByRole('dialog', { name: 'Review before copying' })
  await expect(copyGate.getByText(KUBERNETES_EDIT)).toBeVisible()
  await expect(copyGate.getByRole('button', { name: 'Copy anyway' })).toBeDisabled()
  await copyGate.getByRole('button', { name: 'Cancel' }).click()
  await expect(copyGate).toBeHidden()
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe('clipboard before the test')

  // After the acknowledgement the file is saved, with the visitor's own wording in it.
  await page.getByRole('button', { name: 'Download PDF' }).click()
  await downloadGate.getByRole('checkbox', { name: /I have read these statements/ }).check()
  await expectPdfDownload(page, downloadAnyway, 'Jordan Rivera - Resume.pdf')
  await expect(downloadGate).toBeHidden()
  await expect(page.getByText('Resume downloaded')).toBeVisible()
  expect(downloads()).toBe(1)

  // Acknowledged for this revision of the draft: now the document is also what the browser prints.
  await page.emulateMedia({ media: 'print' })
  await expect(printNotice).toBeHidden()
  await expect(resume.getByRole('heading', { name: 'Jordan Rivera' })).toBeVisible()
  await expect(claim.locator('.ws-claim-text')).toHaveText(KUBERNETES_EDIT)
  await expect(claim.locator('.ws-claim-text')).toBeVisible()
  await page.emulateMedia({ media: 'screen' })
})

test('the resume is copied, a statement regenerated and a coverage status corrected', async ({ page }) => {
  await reachWorkspaceWithSample(page)
  const resume = page.getByRole('tabpanel', { name: 'Resume' })
  const sidePanel = page.getByRole('complementary', { name: 'Evidence, coverage and requirements' })

  // Nothing is flagged in the fresh draft, so Copy gives the document straight
  // away: plain text, without review markers.
  await expect(page.getByText('No statements are flagged.')).toBeVisible()
  await page.getByRole('button', { name: 'Copy', exact: true }).click()
  await expect(page.getByText('Resume copied as plain text')).toBeVisible()
  const copied = await page.evaluate(() => navigator.clipboard.readText())
  expect(copied).toContain('Jordan Rivera')
  expect(copied).toContain('EXPERIENCE\nMachine Learning Engineer, Brightloom Labs')
  expect(copied).toContain('SKILLS\n')
  expect(copied).not.toMatch(/Supported|Regenerate|Evidence \d|Edit\b/)

  // Regenerate is offered only where the server can rewrite a statement.
  const regenerate = { name: 'Regenerate this statement' }
  await expect(resume.getByRole('region', { name: 'Experience' }).getByRole('button', regenerate).first()).toBeVisible()
  await expect(resume.getByRole('region', { name: 'Skills' }).getByRole('button', regenerate)).toHaveCount(0)
  await expect(resume.getByRole('region', { name: 'Education' }).getByRole('button', regenerate)).toHaveCount(0)

  const claim = await pinClaim(page, claimWithText(resume, DOCKER_BULLET))
  const sent = page.waitForRequest((request) => request.url().endsWith('/regenerate'))
  await claim.getByRole('button', regenerate).click()
  await claim.getByRole('textbox', { name: 'Instruction (optional)' }).fill('Lead with the result')
  await claim.getByRole('button', { name: 'Regenerate', exact: true }).click()
  expect((await sent).headers()['idempotency-key']).toMatch(/^[0-9a-f-]{36}$/)
  await expect(page.getByText('Statement regenerated')).toBeVisible()
  await expect(claim.getByRole('textbox', { name: 'Instruction (optional)' })).toBeHidden()
  await expect(claim.locator('[data-status]')).toHaveText(/Supported|Needs review|Unsupported/)
  await expect(claim.locator('.ws-claim-text')).not.toBeEmpty()

  // Correcting a requirement's status is recorded as the visitor's own assessment.
  const counts = sidePanel.getByRole('list', { name: 'Requirement counts' })
  const uncertainBefore = Number.parseInt(await counts.getByRole('listitem').nth(3).innerText(), 10)
  const firstRequirement = sidePanel.getByRole('region', { name: 'Requirements' }).getByRole('listitem').first()
  await firstRequirement.getByRole('button', { name: 'Correct this status' }).click()
  await firstRequirement.getByRole('combobox', { name: 'Status' }).click()
  await page.getByRole('option', { name: 'Uncertain' }).click()
  await firstRequirement.getByRole('textbox', { name: 'Note (optional)' }).fill('Only shown in coursework')
  await firstRequirement.getByRole('button', { name: 'Save correction' }).click()
  await expect(firstRequirement.getByText('Corrected by you')).toBeVisible()
  await expect(firstRequirement.getByText('Your note: Only shown in coursework')).toBeVisible()
  await expect(counts.getByRole('listitem').nth(3)).toHaveText(`${uncertainBefore + 1} uncertain not counted`)
})

test('editing the profile marks the existing draft as out of date', async ({ page }) => {
  const generationId = await reachWorkspaceWithSample(page)
  await expect(page.getByText('This draft is out of date')).toBeHidden()

  await page.getByRole('navigation', { name: 'Steps' }).getByRole('link', { name: 'Profile' }).click()
  await expect(heading(page, 'Review your profile')).toBeVisible()
  await page.getByRole('textbox', { name: 'Location' }).first().fill('Cleveland, OH')
  await page.getByRole('button', { name: 'Save changes' }).click()
  await expect(page.getByText('All changes are saved.')).toBeVisible()
  // The edit has to be confirmed again before anything new can be generated, and the page says so.
  await expect(page.getByText('Confirm again before generating')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Confirm profile and build evidence index' })).toBeEnabled()

  await page.goto(`/workspace/${generationId}`)
  const stale = page.getByRole('status').filter({ hasText: 'This draft is out of date' })
  await expect(stale).toContainText('Your profile changed after this draft was generated.')
  // The old draft is still readable and keeps the details it was generated from.
  await expect(page.getByRole('tabpanel', { name: 'Resume' }).getByText('Columbus, OH')).toBeVisible()

  await stale.getByRole('link', { name: 'Generate a new draft' }).click()
  // The link names the draft's own job, so a newer job can never be opened by mistake.
  await expect(page).toHaveURL(/\/job\?job=[^&]+$/)
  await expect(heading(page, 'Your target job')).toBeVisible()
  await expect(page.getByText('Your profile is not confirmed yet')).toBeVisible()
  const generate = page.getByRole('button', { name: 'Generate a new draft' })
  await expect(generate).toBeDisabled()
  await expect(page.getByRole('region', { name: 'Drafts for this job' }).getByText('Stale', { exact: true })).toBeVisible()

  // Once the profile is confirmed again, a new draft is written for the same
  // job without analyzing the job a second time.
  const analyses: string[] = []
  page.on('request', (sent) => {
    if (sent.method() === 'POST' && sent.url().endsWith('/api/jobs')) analyses.push(sent.url())
  })
  await page.getByRole('link', { name: 'Review and confirm profile' }).click()
  await page.getByRole('button', { name: 'Confirm profile and build evidence index' }).click()
  await expect(page.getByText('Profile confirmed', { exact: true })).toBeVisible()
  await page.goBack()
  await expect(heading(page, 'Your target job')).toBeVisible()
  await expect(generate).toBeEnabled()
  await generate.click()
  const newGenerationId = await workspaceOpened(page)
  expect(newGenerationId).not.toBe(generationId)
  expect(analyses).toHaveLength(0)
  await expect(page.getByText('This draft is out of date')).toBeHidden()
  await expect(page.getByRole('tabpanel', { name: 'Resume' }).getByText('Cleveland, OH')).toBeVisible()
})
