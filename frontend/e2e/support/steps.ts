/**
 * The steps of the workflow as a visitor performs them, shared by the specs.
 * Each helper ends by waiting for the screen it leads to, so the next step
 * never starts on a page that is still loading.
 */
import fs from 'node:fs'
import path from 'node:path'
import { expect, type APIRequestContext, type Locator, type Page } from '@playwright/test'
import { API_URL, SESSION_STORAGE_KEY, WEB_URL } from './env.ts'

export const ACKNOWLEDGEMENT = 'I have read how my data is sent, stored and deleted.'
export const DEMO_BANNER =
  'Demo mode - responses come from a deterministic fake provider, not a real AI model.'

export interface PastedSources {
  resume?: string
  linkedin?: string
  notes?: string
}

const SOURCE_FIELDS: Record<keyof PastedSources, string> = {
  resume: 'Resume/CV text',
  linkedin: 'LinkedIn profile text',
  notes: 'Background notes text',
}

/** The page's main heading. */
export function heading(page: Page, name: string | RegExp): Locator {
  return page.getByRole('heading', { level: 1, name })
}

// ----------------------------------------------------------------- start

/** Start screen: load the fictional sample, acknowledge the notice and extract. */
export async function extractSampleProfile(page: Page): Promise<void> {
  await page.goto('/')
  await page.getByRole('button', { name: 'Try sample profile' }).click()
  await page.getByRole('checkbox', { name: ACKNOWLEDGEMENT }).check()
  await page.getByRole('button', { name: 'Extract my profile' }).click()
  await expect(heading(page, 'Review your profile')).toBeVisible()
}

/** Start screen: paste the given texts, acknowledge the notice and extract. */
export async function extractProfile(page: Page, sources: PastedSources): Promise<void> {
  await page.goto('/')
  for (const [slot, text] of Object.entries(sources) as [keyof PastedSources, string][]) {
    await page.getByRole('textbox', { name: SOURCE_FIELDS[slot], exact: true }).fill(text)
  }
  await page.getByRole('checkbox', { name: ACKNOWLEDGEMENT }).check()
  await page.getByRole('button', { name: 'Extract my profile' }).click()
  await expect(heading(page, 'Review your profile')).toBeVisible()
}

// --------------------------------------------------------------- profile

/** Profile review: decide every open conflict, then confirm and wait for the index. */
export async function confirmProfile(page: Page): Promise<void> {
  const resolve = page.getByRole('button', { name: 'Mark resolved' })
  for (let open = await resolve.count(); open > 0; open -= 1) {
    await resolve.first().click()
    await expect(resolve).toHaveCount(open - 1)
  }
  await page.getByRole('button', { name: 'Confirm profile and build evidence index' }).click()
  await expect(page.getByText('Profile confirmed', { exact: true })).toBeVisible()
}

// ------------------------------------------------------------------- job

export interface JobText {
  title?: string
  company?: string
  description: string
}

export const TAILOR_BUTTON = 'Tailor my resume'

/** Target job: open the screen from the confirmed profile and fill in the form. */
export async function fillJobForm(page: Page, job: JobText | 'sample'): Promise<void> {
  await page.getByRole('link', { name: 'Continue to target job' }).first().click()
  await expect(heading(page, 'Target job')).toBeVisible()
  if (job === 'sample') {
    await page.getByRole('button', { name: 'Use sample job' }).click()
  } else {
    await page.getByRole('textbox', { name: 'Role title (optional)' }).fill(job.title ?? '')
    await page.getByRole('textbox', { name: 'Company (optional)' }).fill(job.company ?? '')
    await page.getByRole('textbox', { name: 'Job description' }).fill(job.description)
  }
}

/** Wait for the workspace a finished run opens. Returns the generation ID. */
export async function workspaceOpened(page: Page): Promise<string> {
  await page.waitForURL(/\/workspace\/[^/]+$/)
  await expect(heading(page, 'Your tailored draft')).toBeVisible()
  return generationIdFromUrl(page)
}

/**
 * Target job: one submission analyzes the job, writes the draft and opens
 * the workspace. Returns the generation ID.
 */
export async function tailorJob(page: Page, job: JobText | 'sample'): Promise<string> {
  await fillJobForm(page, job)
  await page.getByRole('button', { name: TAILOR_BUTTON }).click()
  return workspaceOpened(page)
}

export function generationIdFromUrl(page: Page): string {
  return new URL(page.url()).pathname.split('/').pop() ?? ''
}

/** The whole way from an empty tab to the workspace, with the fictional sample data. */
export async function reachWorkspaceWithSample(page: Page): Promise<string> {
  await extractSampleProfile(page)
  await confirmProfile(page)
  return tailorJob(page, 'sample')
}

// ------------------------------------------------------------- workspace

/** One statement of the draft (its text and its review controls), found by its text. */
export function claimWithText(scope: Page | Locator, text: string | RegExp): Locator {
  // The inner locator of `has` is resolved inside each candidate, so it must start at the page.
  const page = 'page' in scope ? scope.page() : scope
  return scope.locator('.ws-claim').filter({ has: page.locator('.ws-claim-text', { hasText: text }) })
}

/** The same statement addressed by ID, which stays valid after its text changes. */
export async function pinClaim(page: Page, claim: Locator): Promise<Locator> {
  const itemId = await claim.first().getAttribute('data-item-id')
  return page.locator(`[data-item-id="${itemId}"]`)
}

/** Replace the text of a statement through its inline editor. */
export async function editClaim(claim: Locator, text: string): Promise<void> {
  await claim.getByRole('button', { name: 'Edit this statement' }).click()
  await claim.getByRole('textbox', { name: 'Edit statement' }).fill(text)
  await claim.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(claim.getByText('Edited - needs revalidation')).toBeVisible()
}

/**
 * Click "Download PDF" (or the dialog button that confirms it) and check the
 * file the browser receives: its name, that it is a PDF and that it is not
 * an empty shell. Returns the file's bytes.
 */
export async function expectPdfDownload(page: Page, trigger: Locator, fileName: string): Promise<Buffer> {
  const [download] = await Promise.all([page.waitForEvent('download'), trigger.click()])
  expect(download.suggestedFilename()).toBe(fileName)
  const bytes = fs.readFileSync(await download.path())
  expect(bytes.subarray(0, 4).toString('latin1')).toBe('%PDF')
  expect(bytes.length).toBeGreaterThan(2_000)
  return bytes
}

/** Count the files the page hands to the browser from now on. */
export function countDownloads(page: Page): () => number {
  let downloads = 0
  page.on('download', () => {
    downloads += 1
  })
  return () => downloads
}

// ----------------------------------------------------------- session/API

export interface StoredSession {
  token: string
  expiresAt: string
}

/** The session this tab keeps in sessionStorage, or null when there is none. */
export async function storedSession(page: Page): Promise<StoredSession | null> {
  return page.evaluate((key) => {
    const raw = window.sessionStorage.getItem(key)
    return raw ? (JSON.parse(raw) as StoredSession) : null
  }, SESSION_STORAGE_KEY)
}

export async function requireToken(page: Page): Promise<string> {
  const session = await storedSession(page)
  if (!session) throw new Error('This tab has no session in sessionStorage')
  return session.token
}

/** Call the API directly (not through the page) with a bearer token. */
export function apiGet(request: APIRequestContext, token: string, apiPath: string) {
  return request.get(`${API_URL}${apiPath}`, { headers: { Authorization: `Bearer ${token}` } })
}

/**
 * A response for `route.fulfill` in the API's own error format. The page
 * calls the API across origins, so the browser only hands the response to the
 * app when it carries the CORS headers the real API sends.
 */
export function injectedApiError(status: number, code: string, message: string) {
  return {
    status,
    contentType: 'application/json',
    headers: {
      'access-control-allow-origin': WEB_URL,
      'access-control-expose-headers': 'X-Request-ID, Retry-After',
    },
    body: JSON.stringify({
      error: { code, message, request_id: 'e2e-injected-failure', retryable: true },
    }),
  }
}

// -------------------------------------------------------------- keyboard

/**
 * Press Tab (or Shift+Tab) until `target` has keyboard focus, the way a
 * keyboard user reaches it. Fails if it is not reached, which means the
 * control is not in the tab order.
 */
export async function tabTo(
  page: Page,
  target: Locator,
  options: { backwards?: boolean; maxPresses?: number } = {},
): Promise<void> {
  const key = options.backwards ? 'Shift+Tab' : 'Tab'
  const maxPresses = options.maxPresses ?? 40
  for (let presses = 0; presses < maxPresses; presses += 1) {
    await page.keyboard.press(key)
    if (await target.evaluate((element) => element === document.activeElement)) return
  }
  throw new Error(`Not reached with ${maxPresses} presses of ${key}: ${target}`)
}

/** Assert that the focused element shows a focus indicator (an outline or a ring). */
export async function expectVisibleFocus(page: Page): Promise<void> {
  const focus = await page.evaluate(() => {
    const element = document.activeElement
    if (!element || element === document.body) return null
    const style = getComputedStyle(element)
    return {
      focusVisible: element.matches(':focus-visible'),
      outline: style.outlineStyle !== 'none' && parseFloat(style.outlineWidth) > 0,
      ring: style.boxShadow !== 'none',
    }
  })
  expect(focus, 'an element has keyboard focus').not.toBeNull()
  expect(focus?.focusVisible, 'the browser treats the focus as keyboard focus').toBe(true)
  expect(focus?.outline || focus?.ring, 'the focused element shows an outline or ring').toBe(true)
}

// ---------------------------------------------------------------- layout

interface Overflow {
  scrollWidth: number
  clientWidth: number
  /** Elements whose right edge lies outside the viewport, described for the failure message. */
  offenders: string[]
}

/**
 * Assert that the page cannot be scrolled sideways and that nothing on it is
 * cut off at the right edge. Elements inside a container that scrolls
 * horizontally on purpose (such as the step navigation) are not counted.
 */
export async function expectNoHorizontalOverflow(page: Page, where: string): Promise<void> {
  const overflow = await page.evaluate((): Overflow => {
    const root = document.documentElement
    const insideScroller = (element: Element): boolean => {
      for (let node = element.parentElement; node; node = node.parentElement) {
        const overflowX = getComputedStyle(node).overflowX
        if (overflowX === 'auto' || overflowX === 'scroll') return true
      }
      return false
    }
    const candidates = document.querySelectorAll(
      'header *, main *, footer *, [role="dialog"] *, [role="alertdialog"] *',
    )
    const offenders: string[] = []
    for (const element of candidates) {
      const rect = element.getBoundingClientRect()
      if (rect.width === 0 || rect.height === 0) continue
      if (element.closest('.sr-only')) continue
      if (rect.right <= root.clientWidth + 1 || insideScroller(element)) continue
      const text = (element.textContent ?? '').trim().slice(0, 40)
      offenders.push(`<${element.tagName.toLowerCase()} class="${element.getAttribute('class')}"> "${text}"`)
    }
    return { scrollWidth: root.scrollWidth, clientWidth: root.clientWidth, offenders }
  })
  expect(overflow.scrollWidth, `${where}: the page scrolls sideways`).toBeLessThanOrEqual(
    overflow.clientWidth,
  )
  expect(overflow.offenders, `${where}: elements extend past the right edge`).toEqual([])
}

const SCREENSHOT_DIR = path.join('test-results', 'screenshots')

/** Save a full-page screenshot under test-results/screenshots for a human to look at. */
export async function screenshot(page: Page, name: string): Promise<void> {
  fs.mkdirSync(SCREENSHOT_DIR, { recursive: true })
  await page.screenshot({ path: path.join(SCREENSHOT_DIR, `${name}.png`), fullPage: true })
}
