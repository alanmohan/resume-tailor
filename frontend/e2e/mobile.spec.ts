/**
 * A phone-sized screen (375 x 812): the workflow up to the workspace, with
 * no sideways scrolling on any screen, the four workspace tabs, and
 * evidence in a bottom sheet.
 */
import {
  ACKNOWLEDGEMENT,
  DEMO_BANNER,
  TAILOR_BUTTON,
  confirmProfile,
  expectNoHorizontalOverflow,
  extractSampleProfile,
  heading,
  screenshot,
  workspaceOpened,
} from './support/steps.ts'
import { expect, test } from './support/test.ts'

test.use({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true })

test('the workflow fits a 375px screen and the workspace uses tabs and a source drawer', async ({ page }) => {
  // ---------------------------------------------------------------- Start
  await page.goto('/')
  await expect(heading(page, /A resume for this job/)).toBeVisible()
  await expect(page.getByText(DEMO_BANNER)).toBeVisible()
  await expectNoHorizontalOverflow(page, 'Start')
  await screenshot(page, 'mobile-1-start')

  await page.getByRole('button', { name: 'Try sample profile' }).tap()
  await page.getByRole('checkbox', { name: ACKNOWLEDGEMENT }).tap()
  await expectNoHorizontalOverflow(page, 'Start with the sample loaded')
  await page.getByRole('button', { name: 'Extract my profile' }).tap()

  // -------------------------------------------------------------- Profile
  await expect(heading(page, 'Review your profile')).toBeVisible()
  await expectNoHorizontalOverflow(page, 'Profile review')
  await screenshot(page, 'mobile-2-profile')

  // The header's "Clear my data" is an icon button here; its dialog must fit too.
  await page.getByRole('button', { name: 'Clear my data' }).tap()
  const clearDialog = page.getByRole('alertdialog', { name: 'Delete all your data?' })
  await expect(clearDialog).toBeVisible()
  await expectNoHorizontalOverflow(page, 'Clear my data dialog')
  await screenshot(page, 'mobile-2b-clear-data-dialog')
  await clearDialog.getByRole('button', { name: 'Keep my data' }).tap()
  await expect(clearDialog).toBeHidden()

  await page.getByRole('button', { name: 'Mark resolved' }).tap()
  await expect(page.getByText('Every conflict has a decision.')).toBeVisible()
  await page.getByRole('button', { name: 'Confirm profile and build evidence index' }).tap()
  await expect(page.getByText('Profile confirmed', { exact: true })).toBeVisible()
  await expectNoHorizontalOverflow(page, 'Profile review after confirming')

  // ------------------------------------------------------------------ Job
  await page.getByRole('link', { name: 'Continue to target job' }).first().tap()
  await expect(heading(page, 'Target job')).toBeVisible()
  await page.getByRole('button', { name: 'Use sample job' }).tap()
  await expectNoHorizontalOverflow(page, 'Target job form')
  await screenshot(page, 'mobile-3-job-form')
  await page.getByRole('button', { name: TAILOR_BUTTON }).tap()

  // ------------------------------------------------------------ Workspace
  const generationId = await workspaceOpened(page)
  // One column: coverage and requirements are tabs and there is no side panel.
  await expect(page.getByRole('complementary')).toHaveCount(0)
  const tabs = page.getByRole('tablist')
  await expect(tabs.getByRole('tab')).toHaveText(['Resume', 'Cover letter', 'Coverage', 'Requirements'])
  const resume = page.getByRole('tabpanel', { name: 'Resume' })
  await expect(resume.getByRole('heading', { name: 'Jordan Rivera' })).toBeVisible()
  await expectNoHorizontalOverflow(page, 'Workspace, resume tab')
  await screenshot(page, 'mobile-5-workspace-resume')

  // Evidence opens in a drawer from the bottom and closes back to the badge.
  const badge = resume.getByRole('button', { name: /^Show evidence \d+ for this statement$/ }).first()
  await badge.tap()
  const drawer = page.getByRole('dialog', { name: /^Evidence \d+$/ })
  await expect(drawer.getByRole('blockquote')).not.toBeEmpty()
  await expect(drawer.getByText(/^Source: /).first()).toBeVisible()
  await expect(drawer.getByText('Belongs to')).toBeVisible()
  await expectNoHorizontalOverflow(page, 'Workspace, evidence drawer')
  await screenshot(page, 'mobile-6-workspace-evidence-drawer')
  await drawer.getByRole('button', { name: 'Close' }).tap()
  await expect(drawer).toBeHidden()
  await expect(badge).toBeFocused()

  await tabs.getByRole('tab', { name: 'Cover letter' }).tap()
  await expect(page.getByRole('tabpanel', { name: 'Cover letter' }).locator('.ws-claim-text').first()).toBeVisible()
  await expect(resume).toBeHidden()
  await expectNoHorizontalOverflow(page, 'Workspace, cover letter tab')
  await screenshot(page, 'mobile-7-workspace-cover-letter')

  await tabs.getByRole('tab', { name: 'Coverage' }).tap()
  const coverage = page.getByRole('tabpanel', { name: 'Coverage' })
  await expect(coverage.getByRole('heading', { name: 'Evidence coverage' })).toBeVisible()
  await expect(coverage.getByRole('list', { name: 'Requirement counts' }).getByRole('listitem')).toHaveCount(4)
  await expectNoHorizontalOverflow(page, 'Workspace, coverage tab')
  await screenshot(page, 'mobile-8-workspace-coverage')

  // A requirement's evidence opens in the same drawer.
  await coverage.getByRole('button', { name: /^Show evidence \d+ for this requirement$/ }).first().tap()
  await expect(drawer.getByRole('blockquote')).not.toBeEmpty()
  await page.keyboard.press('Escape')
  await expect(drawer).toBeHidden()

  // The job's requirements are the fourth tab, read-only.
  await tabs.getByRole('tab', { name: 'Requirements' }).tap()
  const requirements = page.getByRole('tabpanel', { name: 'Requirements' })
  await expect(requirements.getByRole('region', { name: /^Required \(\d+\)$/ })).toBeVisible()
  await expect(requirements.getByRole('textbox')).toHaveCount(0)
  await expectNoHorizontalOverflow(page, 'Workspace, requirements tab')
  await screenshot(page, 'mobile-8b-workspace-requirements')
  await requirements.getByRole('button', { name: /^View source of requirement \d+$/ }).first().tap()
  await expect(page.getByRole('dialog').getByRole('blockquote')).not.toBeEmpty()
  await expectNoHorizontalOverflow(page, 'Workspace, requirement source')
  await page.keyboard.press('Escape')

  // The job's own page, where a new draft can be generated for it.
  await page.goBack()
  await expect(heading(page, 'Your target job')).toBeVisible()
  await expectNoHorizontalOverflow(page, 'Target job, already analyzed')
  await screenshot(page, 'mobile-4-job-analyzed')
  await page.goto(`/workspace/${generationId}`)
  await expect(heading(page, 'Your tailored draft')).toBeVisible()

  // The dark theme has the same layout.
  await page.getByRole('button', { name: 'Switch to dark theme' }).tap()
  await tabs.getByRole('tab', { name: 'Resume' }).tap()
  await expect(page.locator('html')).toHaveClass(/dark/)
  await expectNoHorizontalOverflow(page, 'Workspace, dark theme')
  await screenshot(page, 'mobile-9-workspace-dark')
})

/** 136 characters with no place to break a line, well inside every field's limit. */
const UNBROKEN = 'Supercalifragilisticexpialidocious'.repeat(4)

test('text without a break opportunity does not make a 375px screen scroll sideways', async ({ page }) => {
  await extractSampleProfile(page)

  // Profile: the row that stands in for a removed record repeats its title.
  const title = page.getByRole('region', { name: 'Experience' }).getByRole('textbox', { name: 'Job title' }).first()
  const originalTitle = await title.inputValue()
  await title.fill(UNBROKEN)
  await page.getByRole('button', { name: new RegExp(`^Remove ${UNBROKEN}`) }).tap()
  await expect(page.getByText('will be removed when you save')).toBeVisible()
  await expectNoHorizontalOverflow(page, 'Profile, removed record with a long title')
  await screenshot(page, 'mobile-10-profile-long-removed-record')
  // Undo puts the record back and continues in its title field.
  const undo = page.getByRole('button', { name: 'Undo' })
  await expect(undo).toBeFocused()
  await undo.tap()
  await expect(title).toBeFocused()
  await title.fill(originalTitle)

  // Workspace: the page header names the job title and the company.
  await confirmProfile(page)
  await page.getByRole('link', { name: 'Continue to target job' }).first().tap()
  await page.getByRole('button', { name: 'Use sample job' }).tap()
  await page.getByRole('textbox', { name: 'Role title (optional)' }).fill(UNBROKEN)
  await page.getByRole('textbox', { name: 'Company (optional)' }).fill(UNBROKEN)
  await page.getByRole('button', { name: TAILOR_BUTTON }).tap()
  await workspaceOpened(page)
  await expect(page.getByText(`Tailored for ${UNBROKEN} at ${UNBROKEN}.`)).toBeVisible()
  await expectNoHorizontalOverflow(page, 'Workspace header with a long job title and company')
  await screenshot(page, 'mobile-11-workspace-long-job-title')

  // The same long names on the job's own page and in the list of drafts.
  await page.goBack()
  await expect(heading(page, 'Your target job')).toBeVisible()
  await expectNoHorizontalOverflow(page, 'Target job with a long title and company')
  await page.getByRole('link', { name: 'Start a different job' }).tap()
  await expect(page.getByRole('region', { name: 'Your drafts' }).getByRole('link')).toHaveCount(1)
  await expectNoHorizontalOverflow(page, 'Target job form with a draft of a long-named job')
})
