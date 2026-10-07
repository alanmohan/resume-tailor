import { test as base, expect } from '@playwright/test'

declare global {
  interface Window {
    /** How often the page asked the browser to print (see the init script below). */
    __printCalls: number
    /** Set by the hostile markup in the escaping test if it were ever executed. */
    __xss?: unknown
  }
}

/**
 * The browser logs one console line for every non-2xx response. Several of
 * those are part of normal use (404 for "no profile yet", 409 for a version
 * conflict) or are provoked by a test on purpose, so they are not failures.
 */
const EXPECTED_CONSOLE_ERROR = /^Failed to load resource/

/**
 * `test` for every spec in this folder. On top of Playwright's own it
 *   - fails a test when the page throws an uncaught error or logs an
 *     unexpected console error, and
 *   - replaces `window.print` with a counter, because a real print dialog
 *     would block a headless browser.
 */
export const test = base.extend({
  page: async ({ page }, use) => {
    const problems: string[] = []
    page.on('pageerror', (error) => problems.push(`uncaught error: ${error.message}`))
    page.on('console', (message) => {
      if (message.type() === 'error' && !EXPECTED_CONSOLE_ERROR.test(message.text())) {
        problems.push(`console error: ${message.text()}`)
      }
    })
    await page.addInitScript(() => {
      window.__printCalls = 0
      window.print = () => {
        window.__printCalls += 1
      }
    })

    await use(page)

    expect(problems, 'the page reported errors during the test').toEqual([])
  },
})

export { expect }
