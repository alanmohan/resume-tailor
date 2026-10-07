/**
 * Untrusted text. A resume and a job posting containing script and image
 * markup go through every screen; the markup must be shown as the characters
 * that were pasted and must never become part of the page.
 */
import type { Page } from '@playwright/test'
import { HOSTILE, IMG_TAG, SCRIPT_TAG } from './support/data.ts'
import { analyzeJob, confirmProfile, extractProfile, generateDraft } from './support/steps.ts'
import { expect, test } from './support/test.ts'

/** Assert that none of the pasted markup was executed or turned into elements. */
async function expectNothingInjected(page: Page, where: string): Promise<void> {
  const injected = await page.evaluate(() => ({
    flag: window.__xss,
    images: document.querySelectorAll('img[src="x"], [onerror]').length,
    scripts: [...document.scripts].filter((script) => script.textContent?.includes('__xss')).length,
    bold: [...document.querySelectorAll('main b')].filter((b) => b.textContent === 'bold').length,
  }))
  expect(injected, where).toEqual({ flag: undefined, images: 0, scripts: 0, bold: 0 })
}

test('markup in pasted text is displayed as text and never executed', async ({ page }) => {
  await extractProfile(page, { resume: HOSTILE.resume })

  // ------------------------------------------------------- Profile review
  const role = page.getByRole('group', { name: HOSTILE.role })
  const scriptBullet = role.getByRole('textbox', { name: `Bullet 1 of ${HOSTILE.role}` })
  await expect(scriptBullet).toHaveValue(HOSTILE.scriptBullet)
  await expect(role.getByRole('textbox', { name: `Bullet 2 of ${HOSTILE.role}` })).toHaveValue(HOSTILE.imgBullet)
  await expect(role.getByRole('textbox', { name: `Bullet 3 of ${HOSTILE.role}` })).toHaveValue(HOSTILE.boldBullet)

  // The source excerpt shows the exact characters that were pasted.
  for (const [index, bullet] of [HOSTILE.scriptBullet, HOSTILE.imgBullet].entries()) {
    const item = role
      .getByRole('listitem')
      .filter({ has: page.getByRole('textbox', { name: `Bullet ${index + 1} of ${HOSTILE.role}` }) })
    await item.getByRole('button', { name: 'View source' }).click()
    const popover = page.getByRole('dialog')
    await expect(popover.getByRole('blockquote')).toHaveText(bullet)
    await expectNothingInjected(page, `source excerpt of bullet ${index + 1}`)
    await page.keyboard.press('Escape')
    await expect(popover).toBeHidden()
  }

  await confirmProfile(page)
  await expectNothingInjected(page, 'profile review')

  // ------------------------------------------------------------------ Job
  await analyzeJob(page, HOSTILE.job)
  await expect(page.getByRole('textbox', { name: 'Role title' })).toHaveValue(HOSTILE.job.title)
  await expect(page.getByRole('textbox', { name: 'Company' })).toHaveValue(HOSTILE.job.company)
  const requirementTexts = await page
    .getByRole('textbox', { name: /^Requirement \d+ text$/ })
    .evaluateAll((fields) => fields.map((field) => (field as HTMLTextAreaElement).value))
  expect(requirementTexts.some((text) => text.includes(IMG_TAG))).toBe(true)
  expect(requirementTexts.some((text) => text.includes(SCRIPT_TAG))).toBe(true)
  await expectNothingInjected(page, 'job review')

  // ------------------------------------------------------------ Workspace
  await generateDraft(page)
  // The page introduction names the job exactly as typed.
  await expect(page.getByRole('main')).toContainText(`Tailored for ${HOSTILE.job.title} at ${HOSTILE.job.company}`)
  const resume = page.getByRole('tabpanel', { name: 'Resume' })
  await expect(resume.getByRole('heading', { name: 'Sam Carter' })).toBeVisible()
  await expect(resume.locator('.ws-claim-text', { hasText: SCRIPT_TAG }).first()).toBeVisible()
  await expectNothingInjected(page, 'workspace, resume')

  // Evidence for a statement that contains markup.
  const sidePanel = page.getByRole('complementary', { name: 'Evidence and coverage' })
  const hostileClaim = resume.locator('.ws-claim').filter({ hasText: SCRIPT_TAG }).first()
  await hostileClaim.getByRole('button', { name: /^Show evidence \d+ for this statement$/ }).first().click()
  await expect(sidePanel.getByRole('blockquote')).toContainText(SCRIPT_TAG)
  await expectNothingInjected(page, 'workspace, evidence panel')

  // Requirements in the coverage list.
  await sidePanel.getByRole('tab', { name: 'Coverage' }).click()
  await expect(sidePanel.getByText(IMG_TAG).first()).toBeVisible()
  await expectNothingInjected(page, 'workspace, coverage')

  // The cover letter names the job and quotes the same statements, as plain characters.
  await page.getByRole('tab', { name: 'Cover letter' }).click()
  const coverLetter = page.getByRole('tabpanel', { name: 'Cover letter' })
  await expect(coverLetter.locator('.ws-claim-text').first()).toContainText(
    `${HOSTILE.job.title} role at ${HOSTILE.job.company}`,
  )
  await expect(coverLetter).toContainText(HOSTILE.scriptBullet)
  await expect(coverLetter).toContainText(IMG_TAG)
  await expectNothingInjected(page, 'workspace, cover letter')
})
