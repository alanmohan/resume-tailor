import { screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Generation } from '@/lib/types'
import { deferred, errorResponse, jsonResponse } from '@/test/mockApi'
import { downloadPdf } from '../pdfDocument'
import {
  GENERATION_ID,
  makeClaim,
  makeEntry,
  makeGeneration,
  stubWideScreen,
  withClaim,
  withNothingFlagged,
} from './fixtures'
import {
  allowForSlowMachine,
  claim,
  claimElement,
  draftIsShown,
  openWorkspace,
} from './helpers'

allowForSlowMachine()

// The real module builds a PDF and asks the browser to save it. These tests
// are about when the Workspace asks for that, so the save function is a spy.
vi.mock('../pdfDocument', () => ({ downloadPdf: vi.fn() }))

const DRAFT = `/api/generations/${GENERATION_ID}`

/** The draft as the server returns it after the user edited one statement. */
function afterEdit(generation: Generation, itemId: string, text: string): Generation {
  const edited = withClaim(generation, itemId, {
    text,
    validation_status: 'user_edited',
    user_edited: true,
    warnings: [],
  })
  edited.revision = generation.revision + 1
  edited.validation = {
    ...generation.validation,
    state: 'needs_revalidation',
    user_edited_count: generation.validation.user_edited_count + 1,
  }
  return edited
}

beforeEach(() => {
  stubWideScreen()
  vi.mocked(downloadPdf).mockReset()
})

describe('Workspace documents', () => {
  it('renders the resume sections from the draft and leaves out empty ones', async () => {
    openWorkspace()
    await draftIsShown()

    const resume = within(screen.getByRole('tabpanel', { name: 'Resume' }))
    expect(resume.getByRole('heading', { name: 'Riley Example' })).toBeInTheDocument()
    expect(resume.getByText('riley@example.com')).toBeInTheDocument()
    expect(resume.getByText('https://example.com/riley')).toBeInTheDocument()

    const regions = resume.getAllByRole('region').map((region) => region.getAttribute('aria-labelledby'))
    expect(regions).toEqual([
      'resume-summary',
      'resume-experience',
      'resume-projects',
      'resume-education',
      'resume-skills',
    ])
    expect(resume.queryByRole('region', { name: 'Certifications' })).toBeNull()

    const experience = within(resume.getByRole('region', { name: 'Experience' }))
    expect(experience.getByRole('heading', { name: 'Software Engineer' })).toBeInTheDocument()
    expect(experience.getByText('Northwind Robotics, Columbus, OH')).toBeInTheDocument()
    expect(experience.getByText('Aug 2022 - Present')).toBeInTheDocument()
    const experienceRegion = resume.getByRole('region', { name: 'Experience' })
    expect(
      [...experienceRegion.querySelectorAll('[data-item-id]')].map((item) => item.querySelector('p')?.textContent),
    ).toEqual([
      'Built a search service in Python used by 35 support agents',
      'Cut the nightly reporting job from 3 hours to 45 minutes',
    ])

    expect(within(resume.getByRole('region', { name: 'Projects' })).getByText('2024')).toBeInTheDocument()
    const education = within(resume.getByRole('region', { name: 'Education' }))
    expect(education.getByRole('heading', { name: 'B.S. Computer Science' })).toBeInTheDocument()
    expect(education.getByText('Lakeshore State University')).toBeInTheDocument()
    const skills = within(resume.getByRole('region', { name: 'Skills' }))
    expect(skills.getAllByRole('listitem').map((item) => item.querySelector('p')?.textContent)).toEqual([
      'Python',
      'TypeScript',
    ])
  })

  it('titles the projects section "Projects and publications" when it holds a publication', async () => {
    const generation = makeGeneration()
    generation.resume!.projects.push(
      makeEntry({
        entry_id: 'entry-9',
        category: 'publication',
        heading: 'Faster Trail Search',
        subheading: 'Workshop on Maps',
        bullets: [],
      }),
    )
    generation.resume!.certifications.push(
      makeEntry({ entry_id: 'entry-10', category: 'certification', heading: 'Cloud Basics', bullets: [] }),
    )
    openWorkspace(generation)
    await draftIsShown()

    expect(screen.getByRole('region', { name: 'Projects and publications' })).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Projects' })).toBeNull()
    expect(screen.getByRole('region', { name: 'Certifications' })).toBeInTheDocument()
  })

  it('shows the cover letter in its own tab with the confirmed contact details', async () => {
    const { user } = openWorkspace()
    await draftIsShown()
    // Both documents stay mounted; only the selected one is displayed.
    expect(claimElement('cl-2')).not.toBeVisible()

    await user.click(screen.getByRole('tab', { name: 'Cover letter' }))

    const letter = within(screen.getByRole('tabpanel', { name: 'Cover letter' }))
    expect(letter.getByRole('heading', { name: 'Riley Example' })).toBeInTheDocument()
    expect(letter.getByText('Dear Hiring Manager,')).toBeVisible()
    expect(claimElement('cl-2')).toBeVisible()
    expect(claimElement('b-1')).not.toBeVisible()
    expect(screen.queryByRole('tabpanel', { name: 'Resume' })).toBeNull()
  })

  it('closes the cover letter with a sign-off made from the confirmed name, not a statement', async () => {
    const { user } = openWorkspace()
    await draftIsShown()
    await user.click(screen.getByRole('tab', { name: 'Cover letter' }))

    const signOff = document.querySelector('.ws-letter-signoff') as HTMLElement
    expect(signOff).toHaveTextContent('Sincerely,Riley Example')
    // Plain text under the paragraphs: nothing to edit, regenerate or validate.
    expect(within(signOff).queryByRole('button')).toBeNull()
    expect(signOff.closest('.ws-claim')).toBeNull()
  })

  it('leaves the sign-off out when no name is confirmed', async () => {
    const generation = makeGeneration()
    generation.resume!.contact.name = null
    const { user } = openWorkspace(generation)
    await draftIsShown()
    await user.click(screen.getByRole('tab', { name: 'Cover letter' }))

    expect(screen.getByText('Dear Hiring Manager,')).toBeInTheDocument()
    expect(screen.queryByText('Sincerely,')).toBeNull()
  })

  it('explains a role without statements on screen, but not a degree, which normally has none', async () => {
    const generation = makeGeneration()
    generation.resume!.experience.push(
      makeEntry({ entry_id: 'entry-empty', heading: 'Volunteer Web Developer', bullets: [] }),
    )
    openWorkspace(generation)
    await draftIsShown()

    const note = screen.getByText(/No statement was generated for this record/)
    expect(note.closest('.ws-entry')).toHaveTextContent('Volunteer Web Developer')
    // Screen only: the printed resume carries no explanation.
    expect(note).toHaveClass('print:hidden')
    const education = within(screen.getByRole('region', { name: 'Education' }))
    expect(education.queryByText(/No statement was generated/)).toBeNull()
  })

  it('shows every validation status as a text label next to an icon', async () => {
    openWorkspace(
      withClaim(makeGeneration(), 'p-1', { validation_status: 'unsupported', warnings: ['Not in evidence'] }),
    )
    await draftIsShown()

    expect(claim('b-1').getByText('Supported')).toBeInTheDocument()
    expect(claim('b-2').getByText('Needs review')).toBeInTheDocument()
    expect(claim('p-1').getByText('Unsupported')).toBeInTheDocument()
    expect(claim('sum-2').getByText('No citation needed')).toBeInTheDocument()
    expect(claim('sk-1').getByText('Supported')).toBeInTheDocument()
    for (const itemId of ['b-1', 'b-2', 'p-1', 'sum-2', 'sk-1']) {
      const badge = claimElement(itemId).querySelector('[data-status]')!
      expect(badge.querySelector('svg[aria-hidden="true"]')).not.toBeNull()
    }
  })

  it('shows the warnings of a flagged statement and the numbered evidence badges', async () => {
    openWorkspace()
    await draftIsShown()

    expect(claim('b-2').getByText('1 warning')).toBeInTheDocument()
    expect(claim('b-2').getByText('"45 minutes" does not appear in the cited evidence')).toBeVisible()
    expect(
      claim('b-1')
        .getAllByRole('button', { name: /^Show evidence \d+ for this statement$/ })
        .map((badge) => badge.textContent),
    ).toEqual(['1', '2'])
    expect(claim('sum-2').queryByRole('button', { name: /Show evidence/ })).toBeNull()
  })

  it('offers Regenerate only for statements the server can rewrite', async () => {
    const generation = makeGeneration()
    generation.resume!.education[0].bullets = [makeClaim({ item_id: 'edu-1', text: 'GPA 3.7/4.0' })]
    openWorkspace(generation)
    await draftIsShown()

    const regenerate = { name: 'Regenerate this statement' }
    for (const itemId of ['sum-1', 'b-1', 'p-1', 'cl-2']) {
      expect(claim(itemId).getByRole('button', { ...regenerate, hidden: true })).toBeInTheDocument()
    }
    // Skills, education and certifications are copied from the confirmed profile.
    for (const itemId of ['sk-1', 'edu-1']) {
      expect(claim(itemId).queryByRole('button', { ...regenerate, hidden: true })).toBeNull()
      expect(claim(itemId).getByRole('button', { name: 'Edit this statement' })).toBeInTheDocument()
    }
  })

  it('renders markup inside generated text as plain text and creates no elements from it', async () => {
    const hostile = '<img src=x onerror="alert(1)">Built <b>fast</b> things<script>alert(2)</script>'
    const generation = withClaim(makeGeneration(), 'b-1', { text: hostile })
    generation.resume!.contact.name = '<i>Riley</i> Example'
    generation.resume!.experience[0].heading = '<u>Software</u> Engineer'
    openWorkspace(generation)
    await draftIsShown()

    expect(claim('b-1').getByText(hostile)).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '<u>Software</u> Engineer' })).toBeInTheDocument()
    expect(screen.getAllByText('<i>Riley</i> Example').length).toBeGreaterThan(0)
    const main = screen.getByRole('main')
    for (const selector of ['script', 'img', 'b', 'i', 'u', '[onerror]']) {
      expect(main.querySelector(selector)).toBeNull()
    }
  })
})

describe('Workspace editing a statement', () => {
  it('saves an edit with the expected revision and marks the statement as edited', async () => {
    const generation = makeGeneration()
    const newText = 'Built and operated a Python search service for 35 support agents'
    const { user, requests } = openWorkspace(generation, {
      [`PATCH ${DRAFT}`]: jsonResponse(afterEdit(generation, 'b-1', newText)),
    })
    await draftIsShown()
    expect(claim('b-1').getByText('Supported')).toBeInTheDocument()

    const editButton = claim('b-1').getByRole('button', { name: 'Edit this statement' })
    await user.click(editButton)
    const textarea = claim('b-1').getByLabelText('Edit statement')
    expect(textarea).toHaveFocus()
    expect(textarea).toHaveValue('Built a search service in Python used by 35 support agents')
    // Nothing to save until the text changes.
    expect(claim('b-1').getByRole('button', { name: 'Save' })).toBeDisabled()

    await user.clear(textarea)
    await user.paste(newText)
    await user.click(claim('b-1').getByRole('button', { name: 'Save' }))

    expect(await claim('b-1').findByText('Edited - needs revalidation')).toBeInTheDocument()
    const patch = requests.find((request) => request.method === 'PATCH')!
    expect(patch.path).toBe(DRAFT)
    expect(patch.body).toEqual({
      expected_revision: 3,
      edits: [{ item_id: 'b-1', text: newText }],
    })

    // The statement shows the saved text and no longer carries a supported badge.
    expect(claim('b-1').getByText(newText)).toBeInTheDocument()
    expect(claim('b-1').queryByText('Supported')).toBeNull()
    expect(claimElement('b-1').querySelector('[data-status="supported"]')).toBeNull()
    expect(claim('b-1').queryByLabelText('Edit statement')).toBeNull()
    await waitFor(() => expect(editButton).toHaveFocus())
    expect(screen.getByText('Edited - revalidate before export')).toBeInTheDocument()
    expect(screen.getByText('1 edited, not revalidated')).toBeInTheDocument()
  })

  it('cancels an edit without calling the server', async () => {
    const { user, requests } = openWorkspace()
    await draftIsShown()

    await user.click(claim('b-1').getByRole('button', { name: 'Edit this statement' }))
    await user.type(claim('b-1').getByLabelText('Edit statement'), ' and more')
    await user.click(claim('b-1').getByRole('button', { name: 'Cancel' }))

    expect(claim('b-1').queryByLabelText('Edit statement')).toBeNull()
    expect(
      claim('b-1').getByText('Built a search service in Python used by 35 support agents'),
    ).toBeInTheDocument()
    expect(requests.some((request) => request.method === 'PATCH')).toBe(false)
  })

  it('rejects an empty or over-long edit before sending it', async () => {
    const { user, requests } = openWorkspace()
    await draftIsShown()

    await user.click(claim('b-1').getByRole('button', { name: 'Edit this statement' }))
    const textarea = claim('b-1').getByLabelText('Edit statement')
    await user.clear(textarea)

    expect(claim('b-1').getByRole('alert')).toHaveTextContent('Enter the statement')
    expect(claim('b-1').getByRole('button', { name: 'Save' })).toBeDisabled()

    await user.click(textarea)
    await user.paste('x'.repeat(1201))

    expect(claim('b-1').getByRole('alert')).toHaveTextContent('1200 characters or fewer')
    expect(claim('b-1').getByText('(1 over the limit)')).toBeInTheDocument()
    expect(claim('b-1').getByRole('button', { name: 'Save' })).toBeDisabled()
    expect(requests.some((request) => request.method === 'PATCH')).toBe(false)
  })

  it('keeps the typed text when saving fails and saves it on Retry', async () => {
    const generation = makeGeneration()
    let attempts = 0
    const { user, requests } = openWorkspace(generation, {
      [`PATCH ${DRAFT}`]: () => {
        attempts += 1
        return attempts === 1
          ? errorResponse(503, 'database_unavailable', 'The database is unavailable.', { retryable: true })
          : jsonResponse(afterEdit(generation, 'b-1', 'Built a search service'))
      },
    })
    await draftIsShown()

    await user.click(claim('b-1').getByRole('button', { name: 'Edit this statement' }))
    const textarea = claim('b-1').getByLabelText('Edit statement')
    await user.clear(textarea)
    await user.paste('Built a search service')
    await user.click(claim('b-1').getByRole('button', { name: 'Save' }))

    const alert = await claim('b-1').findByRole('alert')
    expect(alert).toHaveTextContent('Your edit was not saved')
    expect(alert).toHaveTextContent('The database is unavailable.')
    expect(claim('b-1').getByLabelText('Edit statement')).toHaveValue('Built a search service')

    await user.click(within(alert).getByRole('button', { name: 'Retry' }))

    expect(await claim('b-1').findByText('Edited - needs revalidation')).toBeInTheDocument()
    expect(requests.filter((request) => request.method === 'PATCH')).toHaveLength(2)
  })

  it('offers to reload after a version conflict and then saves against the new revision', async () => {
    const stored = makeGeneration()
    const newer = makeGeneration({ revision: 4 })
    let reads = 0
    const { user, requests } = openWorkspace(stored, {
      [`GET ${DRAFT}`]: () => {
        reads += 1
        return jsonResponse(reads === 1 ? stored : newer)
      },
      [`PATCH ${DRAFT}`]: (request) =>
        (request.body as { expected_revision: number }).expected_revision === 4
          ? jsonResponse(afterEdit(newer, 'b-1', 'Built a search service'))
          : errorResponse(409, 'version_conflict', 'This draft was changed elsewhere.'),
    })
    await draftIsShown()

    await user.click(claim('b-1').getByRole('button', { name: 'Edit this statement' }))
    const textarea = claim('b-1').getByLabelText('Edit statement')
    await user.clear(textarea)
    await user.paste('Built a search service')
    await user.click(claim('b-1').getByRole('button', { name: 'Save' }))

    const alert = await claim('b-1').findByRole('alert')
    expect(alert).toHaveTextContent('This draft was changed elsewhere.')
    expect(within(alert).queryByRole('button', { name: 'Retry' })).toBeNull()
    await user.click(within(alert).getByRole('button', { name: 'Reload latest version' }))

    await waitFor(() => expect(claim('b-1').queryByRole('alert')).toBeNull())
    expect(claim('b-1').getByLabelText('Edit statement')).toHaveValue('Built a search service')
    await user.click(claim('b-1').getByRole('button', { name: 'Save' }))

    expect(await claim('b-1').findByText('Edited - needs revalidation')).toBeInTheDocument()
    const revisions = requests
      .filter((request) => request.method === 'PATCH')
      .map((request) => (request.body as { expected_revision: number }).expected_revision)
    expect(revisions).toEqual([3, 4])
  })
})

describe('Workspace revalidation', () => {
  it('revalidates edited statements and updates their status', async () => {
    const edited = afterEdit(makeGeneration(), 'b-1', 'Built a Python search service')
    const validated = withClaim(edited, 'b-1', { validation_status: 'supported' })
    validated.validation = {
      state: 'validated',
      needs_review_count: 1,
      unsupported_count: 0,
      user_edited_count: 0,
      validated_at: '2026-10-07T17:00:00Z',
    }
    const { user, requests } = openWorkspace(edited, {
      [`POST ${DRAFT}/validate`]: jsonResponse(validated),
    })
    await draftIsShown()
    expect(claim('b-1').getByText('Edited - needs revalidation')).toBeInTheDocument()
    expect(screen.queryByText('Validated')).toBeNull()

    await user.click(screen.getByRole('button', { name: 'Revalidate' }))

    expect(await claim('b-1').findByText('Supported')).toBeInTheDocument()
    expect(requests.some((r) => r.method === 'POST' && r.path === `${DRAFT}/validate`)).toBe(true)
    // The wording is still the user's, and that stays visible.
    expect(claim('b-1').getByText('Edited by you')).toBeInTheDocument()
    expect(claim('b-1').getByText('Built a Python search service')).toBeInTheDocument()
    expect(screen.getByText('Validated')).toBeInTheDocument()
    expect(document.querySelector('time[datetime="2026-10-07T17:00:00Z"]')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Revalidate' })).toBeNull()
    expect(screen.queryByText('Edited - revalidate before export')).toBeNull()
  })

  it('shows the validation time, the flagged counts and the limits of validation', async () => {
    const generation = withClaim(makeGeneration(), 'p-1', { validation_status: 'unsupported' })
    generation.validation.unsupported_count = 1
    openWorkspace(generation)
    await draftIsShown()

    expect(screen.getByText('Validated')).toBeInTheDocument()
    expect(document.querySelector('time[datetime="2026-10-07T16:12:00Z"]')).toBeInTheDocument()
    expect(screen.getByText('1 needs review')).toBeInTheDocument()
    expect(screen.getByText('1 unsupported')).toBeInTheDocument()
    expect(screen.getByText(/reduces the risk of fabricated claims but cannot eliminate it/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Revalidate' })).toBeNull()
  })

  it('steps through the flagged statements, switching document when needed', async () => {
    const generation = withClaim(makeGeneration(), 'cl-2', { validation_status: 'unsupported' })
    const { user } = openWorkspace(generation)
    await draftIsShown()
    const next = screen.getByRole('button', { name: 'Next flagged item' })

    await user.click(next)
    expect(claimElement('b-2')).toHaveFocus()

    await user.click(next)
    expect(claimElement('cl-2')).toHaveFocus()
    expect(screen.getByRole('tab', { name: 'Cover letter' })).toHaveAttribute('aria-selected', 'true')

    await user.click(next)
    expect(claimElement('b-2')).toHaveFocus()
    expect(screen.getByRole('tab', { name: 'Resume' })).toHaveAttribute('aria-selected', 'true')
  })

  it('says so when nothing is flagged', async () => {
    openWorkspace(withNothingFlagged(makeGeneration()))
    await draftIsShown()

    expect(screen.getByText('No statements are flagged.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Next flagged item' })).toBeNull()
  })
})

describe('Workspace regenerating a statement', () => {
  it('sends the instruction with an idempotency key and shows the new text', async () => {
    const generation = makeGeneration()
    const regenerated = withClaim(generation, 'b-1', {
      text: 'Built a Python search service that 35 support agents rely on',
    })
    regenerated.revision = 4
    const response = deferred<Response>()
    const { user, requests } = openWorkspace(generation, {
      [`POST ${DRAFT}/items/b-1/regenerate`]: () => response.promise,
    })
    await draftIsShown()

    await user.click(claim('b-1').getByRole('button', { name: 'Regenerate this statement' }))
    const instruction = claim('b-1').getByLabelText('Instruction (optional)')
    expect(instruction).toHaveFocus()
    await user.paste('Lead with the result')
    await user.click(claim('b-1').getByRole('button', { name: 'Regenerate' }))

    // While the request runs the button says so and nothing else can be saved.
    const pending = await claim('b-1').findByRole('button', { name: 'Regenerating...' })
    expect(pending).toBeDisabled()
    expect(claim('b-2').getByRole('button', { name: 'Edit this statement' })).toBeDisabled()

    response.resolve(jsonResponse(regenerated))

    expect(
      await claim('b-1').findByText('Built a Python search service that 35 support agents rely on'),
    ).toBeInTheDocument()
    const post = requests.find((request) => request.method === 'POST')!
    expect(post.path).toBe(`${DRAFT}/items/b-1/regenerate`)
    expect(post.body).toEqual({ instruction: 'Lead with the result' })
    expect(post.headers['Idempotency-Key']).toMatch(/^[0-9a-f-]{36}$/)
    expect(claim('b-1').queryByLabelText('Instruction (optional)')).toBeNull()
    expect(claim('b-2').getByRole('button', { name: 'Edit this statement' })).toBeEnabled()
  })

  it('reuses the idempotency key on Retry and sends no instruction when none was typed', async () => {
    const generation = makeGeneration()
    let attempts = 0
    const { user, requests } = openWorkspace(generation, {
      [`POST ${DRAFT}/items/b-2/regenerate`]: () => {
        attempts += 1
        return attempts === 1
          ? errorResponse(502, 'provider_unavailable', 'The AI provider is unavailable.', { retryable: true })
          : jsonResponse(withClaim(generation, 'b-2', { text: 'Shortened the nightly reporting job' }))
      },
    })
    await draftIsShown()

    await user.click(claim('b-2').getByRole('button', { name: 'Regenerate this statement' }))
    await user.click(claim('b-2').getByRole('button', { name: 'Regenerate' }))

    const alert = await claim('b-2').findByRole('alert')
    expect(alert).toHaveTextContent('This statement was not regenerated')
    expect(alert).toHaveTextContent('The AI provider is unavailable.')
    // The old text is still there: a failure never replaces it.
    expect(claim('b-2').getByText('Cut the nightly reporting job from 3 hours to 45 minutes')).toBeInTheDocument()

    await user.click(within(alert).getByRole('button', { name: 'Retry' }))

    expect(await claim('b-2').findByText('Shortened the nightly reporting job')).toBeInTheDocument()
    const posts = requests.filter((request) => request.method === 'POST')
    expect(posts).toHaveLength(2)
    expect(posts[0].body).toEqual({ instruction: null })
    expect(posts[1].headers['Idempotency-Key']).toBe(posts[0].headers['Idempotency-Key'])
  })

  it('uses a new idempotency key when the instruction changes after a failure', async () => {
    const { user, requests } = openWorkspace(makeGeneration(), {
      [`POST ${DRAFT}/items/b-2/regenerate`]: errorResponse(502, 'provider_unavailable', 'Unavailable.'),
    })
    await draftIsShown()

    await user.click(claim('b-2').getByRole('button', { name: 'Regenerate this statement' }))
    await user.click(claim('b-2').getByRole('button', { name: 'Regenerate' }))
    await claim('b-2').findByRole('alert')
    await user.type(claim('b-2').getByLabelText('Instruction (optional)'), 'Shorter')
    await user.click(claim('b-2').getByRole('button', { name: 'Regenerate' }))

    await waitFor(() => expect(requests.filter((request) => request.method === 'POST')).toHaveLength(2))
    const [first, second] = requests.filter((request) => request.method === 'POST')
    expect(second.body).toEqual({ instruction: 'Shorter' })
    expect(second.headers['Idempotency-Key']).not.toBe(first.headers['Idempotency-Key'])
  })
})

describe('Workspace copy and download', () => {
  it('copies the active document as plain text', async () => {
    const { user } = openWorkspace(withNothingFlagged(makeGeneration()))
    await draftIsShown()

    await user.click(screen.getByRole('button', { name: 'Copy' }))

    expect(await screen.findByText('Resume copied as plain text')).toBeInTheDocument()
    const copied = await navigator.clipboard.readText()
    expect(copied).toContain('Riley Example\nriley@example.com | Columbus, OH')
    expect(copied).toContain('- Built a search service in Python used by 35 support agents')
    expect(copied).toContain('SKILLS\nPython, TypeScript')
    expect(copied).not.toContain('Supported')
    expect(copied).not.toContain('Dear Hiring Manager')

    await user.click(screen.getByRole('tab', { name: 'Cover letter' }))
    await user.click(screen.getByRole('button', { name: 'Copy' }))

    expect(await screen.findByText('Cover letter copied as plain text')).toBeInTheDocument()
    expect(await navigator.clipboard.readText()).toContain('Dear Hiring Manager,')
  })

  it('asks for the same review before copying flagged statements', async () => {
    const { user } = openWorkspace()
    await draftIsShown()
    await navigator.clipboard.writeText('what was on the clipboard before')

    await user.click(screen.getByRole('button', { name: 'Copy' }))

    const dialog = within(await screen.findByRole('dialog', { name: 'Review before copying' }))
    expect(dialog.getByText(/You are about to copy the resume/)).toBeInTheDocument()
    expect(dialog.getByText('Cut the nightly reporting job from 3 hours to 45 minutes')).toBeInTheDocument()
    // Nothing is copied until the user has acknowledged the flagged statement.
    expect(await navigator.clipboard.readText()).toBe('what was on the clipboard before')
    const copyAnyway = dialog.getByRole('button', { name: 'Copy anyway' })
    expect(copyAnyway).toBeDisabled()

    await user.click(dialog.getByRole('checkbox', { name: /want to copy the document as it is/ }))
    await user.click(copyAnyway)

    expect(await screen.findByText('Resume copied as plain text')).toBeInTheDocument()
    expect(await navigator.clipboard.readText()).toContain('Riley Example')
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  })

  it('downloads the active document straight away when nothing needs review', async () => {
    const generation = withNothingFlagged(makeGeneration())
    const { user } = openWorkspace(generation)
    await draftIsShown()

    await user.click(screen.getByRole('button', { name: 'Download PDF' }))

    expect(await screen.findByText('Resume downloaded')).toBeInTheDocument()
    expect(downloadPdf).toHaveBeenCalledTimes(1)
    expect(downloadPdf).toHaveBeenCalledWith(generation, 'resume')
    expect(screen.queryByRole('dialog')).toBeNull()

    await user.click(screen.getByRole('tab', { name: 'Cover letter' }))
    await user.click(screen.getByRole('button', { name: 'Download PDF' }))

    expect(await screen.findByText('Cover letter downloaded')).toBeInTheDocument()
    expect(downloadPdf).toHaveBeenLastCalledWith(generation, 'cover_letter')
  })

  it('says so when the PDF cannot be created', async () => {
    vi.mocked(downloadPdf).mockImplementation(() => {
      throw new Error('This draft has no resume.')
    })
    const { user } = openWorkspace(withNothingFlagged(makeGeneration()))
    await draftIsShown()

    await user.click(screen.getByRole('button', { name: 'Download PDF' }))

    expect(await screen.findByText(/The PDF could not be created/)).toBeInTheDocument()
    expect(downloadPdf).toHaveBeenCalledTimes(1)
  })

  it('asks for review and an explicit acknowledgement before downloading flagged statements', async () => {
    const { user } = openWorkspace()
    await draftIsShown()
    const downloadButton = screen.getByRole('button', { name: 'Download PDF' })

    await user.click(downloadButton)

    const dialog = within(await screen.findByRole('dialog', { name: 'Review before downloading' }))
    expect(downloadPdf).not.toHaveBeenCalled()
    expect(dialog.getByText(/1 statement has not been confirmed by validation/)).toBeInTheDocument()
    expect(dialog.getByText('Needs review')).toBeInTheDocument()
    expect(dialog.getByText('Resume - Experience: Software Engineer, Northwind Robotics')).toBeInTheDocument()
    expect(dialog.getByText('Cut the nightly reporting job from 3 hours to 45 minutes')).toBeInTheDocument()
    const downloadAnyway = dialog.getByRole('button', { name: 'Download anyway' })
    expect(downloadAnyway).toBeDisabled()

    await user.click(dialog.getByRole('checkbox', { name: /I have read these statements/ }))
    expect(downloadAnyway).toBeEnabled()
    await user.click(downloadAnyway)

    expect(await screen.findByText('Resume downloaded')).toBeInTheDocument()
    expect(downloadPdf).toHaveBeenCalledTimes(1)
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    await waitFor(() => expect(downloadButton).toHaveFocus())
  })

  it('keeps the document off paper until the flagged statements are acknowledged', async () => {
    const { user } = openWorkspace()
    await draftIsShown()
    const root = document.querySelector('.ws-root') as HTMLElement
    const notice = () => screen.queryByText(/Review the flagged statements in the app before printing/)

    // What the browser's own print command (Ctrl/Cmd+P) would get: the notice.
    expect(root).toHaveAttribute('data-print-ready', 'false')
    expect(notice()).toHaveClass('ws-print-notice')

    await user.click(screen.getByRole('button', { name: 'Download PDF' }))
    const dialog = within(await screen.findByRole('dialog', { name: 'Review before downloading' }))
    // Opening the dialog, or cancelling it, is not an acknowledgement.
    expect(root).toHaveAttribute('data-print-ready', 'false')
    await user.click(dialog.getByRole('checkbox', { name: /I have read these statements/ }))
    await user.click(dialog.getByRole('button', { name: 'Download anyway' }))

    expect(root).toHaveAttribute('data-print-ready', 'true')
    expect(notice()).toBeNull()
  })

  it('asks again on paper when the draft changes after an acknowledgement', async () => {
    const generation = makeGeneration()
    const { user } = openWorkspace(generation, {
      [`PATCH ${DRAFT}`]: () => jsonResponse(afterEdit(generation, 'b-1', 'Built a search service')),
    })
    await draftIsShown()
    const root = document.querySelector('.ws-root') as HTMLElement
    await user.click(screen.getByRole('button', { name: 'Download PDF' }))
    const dialog = within(await screen.findByRole('dialog', { name: 'Review before downloading' }))
    await user.click(dialog.getByRole('checkbox', { name: /I have read these statements/ }))
    await user.click(dialog.getByRole('button', { name: 'Download anyway' }))
    expect(root).toHaveAttribute('data-print-ready', 'true')

    await user.click(claim('b-1').getByRole('button', { name: 'Edit this statement' }))
    await user.clear(claim('b-1').getByRole('textbox', { name: 'Edit statement' }))
    await user.type(claim('b-1').getByRole('textbox', { name: 'Edit statement' }), 'Built a search service')
    await user.click(claim('b-1').getByRole('button', { name: 'Save' }))

    // A new revision: the earlier acknowledgement does not cover the edit.
    await waitFor(() => expect(root).toHaveAttribute('data-print-ready', 'false'))
  })

  it('lets a draft with nothing to review print from the browser as well', async () => {
    openWorkspace(withNothingFlagged(makeGeneration()))
    await draftIsShown()

    expect(document.querySelector('.ws-root')).toHaveAttribute('data-print-ready', 'true')
    expect(screen.queryByText(/Review the flagged statements in the app before printing/)).toBeNull()
  })

  it('gates downloading and copying when the server counts an unsupported statement', async () => {
    const generation = withNothingFlagged(makeGeneration())
    generation.validation = { ...generation.validation, unsupported_count: 1 }
    const { user } = openWorkspace(generation)
    await draftIsShown()

    expect(document.querySelector('.ws-root')).toHaveAttribute('data-print-ready', 'false')
    await user.click(screen.getByRole('button', { name: 'Download PDF' }))

    expect(await screen.findByRole('dialog', { name: 'Review before downloading' })).toBeInTheDocument()
    expect(downloadPdf).not.toHaveBeenCalled()
  })

  it('goes to the statement from the review dialog without downloading', async () => {
    const generation = withClaim(makeGeneration(), 'cl-2', { validation_status: 'user_edited', user_edited: true })
    generation.validation = { ...generation.validation, state: 'needs_revalidation', user_edited_count: 1 }
    const { user } = openWorkspace(generation)
    await draftIsShown()

    await user.click(screen.getByRole('button', { name: 'Download PDF' }))
    const dialog = within(await screen.findByRole('dialog', { name: 'Review before downloading' }))
    expect(dialog.getByText(/2 statements have not been confirmed/)).toBeInTheDocument()
    expect(dialog.getByText(/edited after the last validation/)).toBeInTheDocument()
    const reviewButtons = dialog.getAllByRole('button', { name: 'Review' })
    expect(reviewButtons).toHaveLength(2)

    // The second entry is the edited cover-letter paragraph.
    await user.click(reviewButtons[1])

    await waitFor(() => expect(claimElement('cl-2')).toHaveFocus())
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(screen.getByRole('tab', { name: 'Cover letter' })).toHaveAttribute('aria-selected', 'true')
    expect(downloadPdf).not.toHaveBeenCalled()
  })

  it('marks only the active document as the one the browser would print', async () => {
    const { user } = openWorkspace()
    await draftIsShown()
    const panels = () =>
      [...document.querySelectorAll('.ws-document-panel')].map((panel) =>
        panel.getAttribute('data-print-target'),
      )

    expect(panels()).toEqual(['true', 'false'])

    await user.click(screen.getByRole('tab', { name: 'Cover letter' }))

    expect(panels()).toEqual(['false', 'true'])
  })
})
