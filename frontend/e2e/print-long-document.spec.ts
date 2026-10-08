/**
 * A long resume. A profile with sixteen roles produces a document that needs
 * more than one page. The downloaded PDF must have those pages, and for a
 * visitor who prints from the browser the print layout must flow down the
 * pages as one column without being clipped or cut off at the side.
 */
import { longJob, longResume } from './support/data.ts'
import {
  confirmProfile,
  expectNoHorizontalOverflow,
  expectPdfDownload,
  extractProfile,
  screenshot,
  tailorJob,
} from './support/steps.ts'
import { expect, test } from './support/test.ts'

/** A4 at 96 CSS pixels per inch, the size the print layout is laid out for. */
const A4 = { width: 794, height: 1123 }

interface PrintLayout {
  /** Height of the whole document, in CSS pixels. */
  documentHeight: number
  /** Ancestors of the paper that would clip or scroll it. */
  clippingAncestors: string[]
  /** Entries or statements that stick out of the paper or hide part of their text. */
  cutOff: string[]
  entries: number
  /** Entries that the stylesheet allows to be split across two pages. */
  splittableEntries: number
  columns: string
}

test('a multi-page resume downloads as a multi-page PDF and prints as one unclipped column', async ({ page }) => {
  await extractProfile(page, { resume: longResume() })
  await expect(page.getByRole('region', { name: 'Experience' }).getByRole('group')).toHaveCount(16)
  await confirmProfile(page)
  await tailorJob(page, longJob())

  const resume = page.getByRole('tabpanel', { name: 'Resume' })
  await expect(resume.locator('.ws-entry')).not.toHaveCount(0)
  await expectNoHorizontalOverflow(page, 'Workspace with a long resume')
  await screenshot(page, 'desktop-10-workspace-long-resume')

  // The downloaded file: several pages of real text.
  const file = await expectPdfDownload(
    page,
    page.getByRole('button', { name: 'Download PDF' }),
    'Casey Whitlock - Resume.pdf',
  )
  const fileText = file.toString('latin1')
  expect((fileText.match(/\/Type\s*\/Page\b(?!s)/g) ?? []).length).toBeGreaterThanOrEqual(2)
  expect(fileText).toContain('/Font')

  await page.setViewportSize(A4)
  await page.emulateMedia({ media: 'print' })

  // Only the resume is laid out: no header, no tools, no second document.
  await expect(page.getByRole('banner')).toBeHidden()
  await expect(page.getByRole('complementary')).toBeHidden()
  await expect(page.getByRole('tabpanel', { name: 'Cover letter' })).toBeHidden()
  await expect(resume.getByRole('heading', { name: 'Casey Whitlock' })).toBeVisible()
  await expect(resume.locator('.ws-entry').last()).toBeVisible()
  await expectNoHorizontalOverflow(page, 'Print layout of a long resume')
  await screenshot(page, 'print-2-long-resume')

  const layout = await page.evaluate((): PrintLayout => {
    const paper = document.querySelector('.ws-document-panel[data-print-target="true"] .ws-paper')
    if (!paper) throw new Error('The print target has no paper element')
    const paperBox = paper.getBoundingClientRect()

    const clippingAncestors: string[] = []
    for (let node = paper.parentElement; node; node = node.parentElement) {
      const style = getComputedStyle(node)
      const clips = [style.overflowX, style.overflowY].some((value) => value !== 'visible')
      const limited = style.maxHeight !== 'none' || style.position === 'sticky'
      if ((clips && node !== document.documentElement && node !== document.body) || limited) {
        clippingAncestors.push(`<${node.tagName.toLowerCase()} class="${node.getAttribute('class')}">`)
      }
    }

    const cutOff: string[] = []
    for (const element of paper.querySelectorAll('.ws-entry, .ws-entry-header, .ws-claim-text')) {
      const box = element.getBoundingClientRect()
      const outside = box.left < paperBox.left - 1 || box.right > paperBox.right + 1
      const hidesText = element.scrollWidth > element.clientWidth + 1
      if (outside || hidesText) cutOff.push((element.textContent ?? '').slice(0, 60))
    }

    const entries = [...paper.querySelectorAll('.ws-entry')]
    return {
      documentHeight: document.documentElement.scrollHeight,
      clippingAncestors,
      cutOff,
      entries: entries.length,
      splittableEntries: entries.filter((entry) => getComputedStyle(entry).breakInside !== 'avoid').length,
      columns: getComputedStyle(paper).columnCount,
    }
  })

  expect(layout.clippingAncestors).toEqual([])
  expect(layout.cutOff).toEqual([])
  expect(layout.entries).toBeGreaterThanOrEqual(16)
  expect(layout.splittableEntries).toBe(0)
  expect(layout.columns).toBe('auto')
  // Taller than one sheet: the content really runs over several pages.
  expect(layout.documentHeight).toBeGreaterThan(A4.height)

  // The browser's own paginator agrees, and the text is real text, not an image.
  const pdf = await page.pdf({
    path: 'test-results/screenshots/print-2-long-resume.pdf',
    format: 'A4',
    preferCSSPageSize: true,
  })
  const pages = pdf.toString('latin1').match(/\/Type\s*\/Page\b(?!s)/g) ?? []
  expect(pages.length).toBeGreaterThanOrEqual(2)
  expect(pdf.toString('latin1')).toContain('/Font')
})
