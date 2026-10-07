/**
 * Keyboard-only use. The Start form, the "Clear my data" dialog and an
 * evidence item are operated with Tab, Shift+Tab, Space, Enter and Escape
 * only; focus has to be visible and has to come back to a sensible place.
 *
 * The steps between those three (confirming the profile, analyzing the job,
 * generating) use the shared helpers, which click.
 */
import {
  ACKNOWLEDGEMENT,
  analyzeJob,
  confirmProfile,
  expectVisibleFocus,
  generateDraft,
  heading,
  tabTo,
} from './support/steps.ts'
import { expect, test } from './support/test.ts'

test('Start, the clear-data dialog and an evidence item work with the keyboard alone', async ({ page }) => {
  // ---------------------------------------------------------------- Start
  await page.goto('/')
  await expect(heading(page, /A resume for this job/)).toBeVisible()

  // The first Tab stop is the skip link, which jumps over the header.
  await page.keyboard.press('Tab')
  const skipLink = page.getByRole('link', { name: 'Skip to main content' })
  await expect(skipLink).toBeFocused()
  await expect(skipLink).toBeInViewport()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('main')).toBeFocused()

  const sampleButton = page.getByRole('button', { name: 'Try sample profile' })
  await tabTo(page, sampleButton)
  await expectVisibleFocus(page)
  await page.keyboard.press('Enter')
  await expect(page.getByRole('textbox', { name: 'Resume/CV text' })).toHaveValue(/Jordan Rivera/)

  const acknowledgement = page.getByRole('checkbox', { name: ACKNOWLEDGEMENT })
  await tabTo(page, acknowledgement)
  await expectVisibleFocus(page)
  await page.keyboard.press('Space')
  await expect(acknowledgement).toBeChecked()

  const submit = page.getByRole('button', { name: 'Extract my profile' })
  await tabTo(page, submit)
  await expectVisibleFocus(page)
  await page.keyboard.press('Enter')

  // The new screen takes focus, so the next Tab continues inside it.
  await expect(heading(page, 'Review your profile')).toBeVisible()
  await expect(page.getByRole('main')).toBeFocused()

  // -------------------------------------------------------- Clear my data
  const clearButton = page.getByRole('button', { name: 'Clear my data' })
  await tabTo(page, clearButton, { backwards: true })
  await expectVisibleFocus(page)
  await page.keyboard.press('Enter')

  const dialog = page.getByRole('alertdialog', { name: 'Delete all your data?' })
  await expect(dialog).toBeVisible()
  // Focus starts on the safe choice and cannot leave the dialog.
  const keep = dialog.getByRole('button', { name: 'Keep my data' })
  const deleteEverything = dialog.getByRole('button', { name: 'Delete everything' })
  await expect(keep).toBeFocused()
  await page.keyboard.press('Tab')
  await expect(deleteEverything).toBeFocused()
  await expectVisibleFocus(page)
  await page.keyboard.press('Tab')
  await expect(keep).toBeFocused()

  await page.keyboard.press('Escape')
  await expect(dialog).toBeHidden()
  await expect(clearButton).toBeFocused()
  // Nothing was deleted by opening and closing the dialog.
  await expect(heading(page, 'Review your profile')).toBeVisible()

  // ------------------------------------------------- Evidence (two panes)
  await confirmProfile(page)
  await analyzeJob(page, 'sample')
  // The review replaced the form: focus follows to the new screen here too.
  await expect(page.getByRole('main')).toBeFocused()
  await generateDraft(page)
  await expect(page.getByRole('main')).toBeFocused()

  const resume = page.getByRole('tabpanel', { name: 'Resume' })
  const badge = resume.getByRole('button', { name: /^Show evidence \d+ for this statement$/ }).first()
  await tabTo(page, badge)
  await expectVisibleFocus(page)
  await page.keyboard.press('Enter')

  const sidePanel = page.getByRole('complementary', { name: 'Evidence and coverage' })
  await expect(badge).toHaveAttribute('aria-pressed', 'true')
  await expect(sidePanel.getByRole('heading', { name: /^Evidence \d+$/ })).toBeVisible()
  await expect(sidePanel.getByRole('blockquote')).not.toBeEmpty()
  // Selecting evidence does not move focus away from the statement being read.
  await expect(badge).toBeFocused()

  // From the evidence, "Go to statement" leads back into the document.
  const goToStatement = sidePanel.getByRole('button', { name: /^Go to statement:/ }).first()
  await tabTo(page, goToStatement, { maxPresses: 200 })
  await expectVisibleFocus(page)
  await page.keyboard.press('Enter')
  await expect(page.locator('.ws-claim:focus')).toHaveCount(1)

  // ------------------------------------------- Evidence (drawer, narrower)
  await page.setViewportSize({ width: 800, height: 900 })
  await expect(sidePanel).toHaveCount(0)
  await badge.focus()
  await page.keyboard.press('Enter')
  const drawer = page.getByRole('dialog', { name: /^Evidence \d+$/ })
  await expect(drawer.getByRole('blockquote')).not.toBeEmpty()
  // Focus is inside the drawer while it is open...
  expect(await drawer.evaluate((element) => element.contains(document.activeElement))).toBe(true)
  await page.keyboard.press('Escape')
  // ...and returns to the badge that opened it.
  await expect(drawer).toBeHidden()
  await expect(badge).toBeFocused()
})
