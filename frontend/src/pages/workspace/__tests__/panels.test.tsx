import { screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { errorResponse, jsonResponse } from '@/test/mockApi'
import {
  GENERATION_ID,
  makeCoverageItem,
  makeEvidence,
  makeGeneration,
  stubWideScreen,
} from './fixtures'
import {
  allowForSlowMachine,
  claim,
  claimElement,
  draftIsShown,
  openWorkspace,
  requirement,
} from './helpers'

allowForSlowMachine()

const DRAFT = `/api/generations/${GENERATION_ID}`

/** The right-hand panel of the two-pane layout. */
function sidePanel() {
  return within(screen.getByRole('complementary', { name: 'Evidence and coverage' }))
}

describe('Workspace evidence panel (two panes)', () => {
  it('opens the exact excerpt, its source, its role and its provenance from a badge', async () => {
    stubWideScreen()
    const { user, requests } = openWorkspace(makeGeneration(), {
      'GET /api/evidence/ev-2': jsonResponse(makeEvidence()),
    })
    await draftIsShown()
    // Coverage is shown first; nothing is fetched until a badge is chosen.
    expect(sidePanel().getByRole('tab', { name: 'Coverage' })).toHaveAttribute('aria-selected', 'true')
    expect(requests.some((request) => request.path.startsWith('/api/evidence'))).toBe(false)

    const badge = claim('b-1').getByRole('button', { name: 'Show evidence 2 for this statement' })
    await user.click(badge)

    const panel = within(await screen.findByRole('tabpanel', { name: 'Evidence' }))
    expect(
      await panel.findByText('Built React and TypeScript dashboards used by 35 internal support agents'),
    ).toBeInTheDocument()
    expect(panel.getByRole('heading', { name: 'Evidence 2' })).toBeInTheDocument()
    expect(panel.getByText('Source: My resume')).toBeInTheDocument()
    expect(panel.getByText('Source type: Resume')).toBeInTheDocument()
    expect(panel.getByText('Software Engineer at Northwind Robotics (role)')).toBeInTheDocument()
    expect(panel.getByText('Extracted from your source')).toBeInTheDocument()
    expect(badge).toHaveAttribute('aria-pressed', 'true')
    // The normalised text that was embedded is not shown, only the excerpt.
    expect(panel.queryByText(/^Software Engineer at Northwind Robotics: built/)).toBeNull()
    expect(panel.queryByText(/similarity|confidence|score/i)).toBeNull()
  })

  it('lists the statements that cite the evidence and jumps to one', async () => {
    stubWideScreen()
    const { user } = openWorkspace(makeGeneration(), {
      'GET /api/evidence/ev-1': jsonResponse(
        makeEvidence({ evidence_id: 'ev-1', excerpt: 'Built a search service in Python' }),
      ),
    })
    await draftIsShown()

    await user.click(claim('sum-1').getByRole('button', { name: 'Show evidence 1 for this statement' }))

    const panel = within(await screen.findByRole('tabpanel', { name: 'Evidence' }))
    await panel.findByText('Built a search service in Python')
    const citing = panel.getAllByRole('button', { name: /^Go to statement:/ })
    expect(citing.map((button) => button.textContent)).toEqual([
      expect.stringContaining('Resume - Summary'),
      expect.stringContaining('Resume - Experience: Software Engineer, Northwind Robotics'),
      expect.stringContaining('Cover letter - Paragraph 2'),
    ])
    expect(panel.getByText('Experience building Python services')).toBeInTheDocument()

    await user.click(citing[2])

    expect(claimElement('cl-2')).toHaveFocus()
    expect(screen.getByRole('tab', { name: 'Cover letter' })).toHaveAttribute('aria-selected', 'true')
  })

  it('labels evidence the user typed during profile review', async () => {
    stubWideScreen()
    const { user } = openWorkspace(makeGeneration(), {
      'GET /api/evidence/ev-4': jsonResponse(
        makeEvidence({
          evidence_id: 'ev-4',
          excerpt: 'Semantic search over 12,000 trip reports',
          source: { source_id: null, label: 'Profile review', source_type: 'notes', start: null, end: null },
          provenance: 'user_added',
          parent: null,
          tags: [],
        }),
      ),
    })
    await draftIsShown()

    await user.click(claim('p-1').getByRole('button', { name: 'Show evidence 4 for this statement' }))

    const panel = within(await screen.findByRole('tabpanel', { name: 'Evidence' }))
    expect(await panel.findByText('Added by you')).toBeInTheDocument()
    expect(panel.getByText('Source type: Notes')).toBeInTheDocument()
    expect(panel.getByText('Not linked to a role or project')).toBeInTheDocument()
  })

  it('renders markup inside an excerpt as plain text', async () => {
    stubWideScreen()
    const hostile = '<script>alert(1)</script><img src=x onerror="alert(2)"> Built dashboards'
    const { user } = openWorkspace(makeGeneration(), {
      'GET /api/evidence/ev-2': jsonResponse(
        makeEvidence({
          excerpt: hostile,
          source: { source_id: 'src-1', label: '<b>Resume</b>', source_type: 'resume', start: 0, end: 10 },
          tags: ['<i>React</i>'],
        }),
      ),
    })
    await draftIsShown()

    await user.click(claim('b-1').getByRole('button', { name: 'Show evidence 2 for this statement' }))

    expect(await screen.findByText(hostile)).toBeInTheDocument()
    expect(screen.getByText('Source: <b>Resume</b>')).toBeInTheDocument()
    expect(screen.getByText('<i>React</i>')).toBeInTheDocument()
    for (const selector of ['script', 'img', '[onerror]', 'main b', 'main i']) {
      expect(document.querySelector(selector)).toBeNull()
    }
  })

  it('explains a missing evidence record and offers Retry for other failures', async () => {
    stubWideScreen()
    let attempts = 0
    const { user } = openWorkspace(makeGeneration(), {
      'GET /api/evidence/ev-1': errorResponse(404, 'not_found', 'Not found'),
      'GET /api/evidence/ev-2': () => {
        attempts += 1
        return attempts === 1
          ? errorResponse(503, 'database_unavailable', 'The database is unavailable.')
          : jsonResponse(makeEvidence())
      },
    })
    await draftIsShown()

    await user.click(claim('b-1').getByRole('button', { name: 'Show evidence 1 for this statement' }))
    expect(
      await screen.findByRole('heading', { name: 'This evidence is no longer available' }),
    ).toBeInTheDocument()

    await user.click(claim('b-1').getByRole('button', { name: 'Show evidence 2 for this statement' }))
    const alert = await sidePanel().findByRole('alert')
    expect(alert).toHaveTextContent('The evidence could not be loaded')
    await user.click(within(alert).getByRole('button', { name: 'Retry' }))

    expect(
      await sidePanel().findByText('Built React and TypeScript dashboards used by 35 internal support agents'),
    ).toBeInTheDocument()
  })
})

describe('Workspace coverage panel (two panes)', () => {
  it('shows the percentage with its counts, the formula and the disclaimer', async () => {
    stubWideScreen()
    const { user } = openWorkspace()
    await draftIsShown()

    const panel = sidePanel()
    expect(panel.getByRole('heading', { name: 'Evidence coverage' })).toBeInTheDocument()
    expect(panel.getByText('50%')).toBeInTheDocument()
    expect(panel.getByText('Across 3 assessed requirements.')).toBeInTheDocument()
    const counts = within(panel.getByRole('list', { name: 'Requirement counts' }))
    expect(counts.getByText('1 supported')).toBeInTheDocument()
    expect(counts.getByText('1 partially supported')).toBeInTheDocument()
    expect(counts.getByText('1 no evidence found')).toBeInTheDocument()
    expect(counts.getByText('1 uncertain not counted')).toBeInTheDocument()
    expect(
      panel.getByText(/not a measure of job suitability, ATS compatibility or hiring probability/),
    ).toBeInTheDocument()

    await user.click(panel.getByRole('button', { name: 'How this is calculated' }))

    expect(
      await screen.findByText(
        'Evidence coverage = 100 × (supported + 0.5 × partially supported) ÷ assessed requirements',
      ),
    ).toBeInTheDocument()
    expect(screen.getByText('For this draft: 100 × (1 + 0.5 × 1) ÷ 3 = 50%')).toBeInTheDocument()
    expect(screen.getByText(/Uncertain requirements are left out/)).toBeInTheDocument()
  })

  it('says "Unavailable" with the counts when nothing could be assessed', async () => {
    stubWideScreen()
    openWorkspace(
      makeGeneration({
        coverage: [
          makeCoverageItem({ status: 'uncertain', evidence_ids: [] }),
          makeCoverageItem({ requirement_id: 'req-2', requirement_text: 'Teamwork', status: 'uncertain', evidence_ids: [] }),
        ],
        coverage_summary: { supported: 0, partial: 0, missing: 0, uncertain: 2, assessed: 0, percent: null },
      }),
    )
    await draftIsShown()

    const panel = sidePanel()
    expect(panel.getByText('Unavailable')).toBeInTheDocument()
    expect(panel.queryByText(/%/)).toBeNull()
    expect(panel.getByText(/No requirement could be assessed/)).toBeInTheDocument()
    expect(panel.getByText('0 supported')).toBeInTheDocument()
    expect(panel.getByText('0 partially supported')).toBeInTheDocument()
    expect(panel.getByText('0 no evidence found')).toBeInTheDocument()
    expect(panel.getByText('2 uncertain not counted')).toBeInTheDocument()
  })

  it('lists each requirement with importance, status label, rationale and evidence', async () => {
    stubWideScreen()
    openWorkspace()
    await draftIsShown()

    const supported = requirement('req-1')
    expect(supported.getByText('Experience building Python services')).toBeInTheDocument()
    expect(supported.getByText('Required')).toBeInTheDocument()
    expect(supported.getByText('Supported')).toBeInTheDocument()
    expect(supported.getByText('The profile describes a Python search service.')).toBeInTheDocument()
    expect(supported.getByRole('button', { name: 'Show evidence 1 for this requirement' })).toBeInTheDocument()

    const partial = requirement('req-3')
    expect(partial.getByText('Preferred')).toBeInTheDocument()
    expect(partial.getByText('Partially supported')).toBeInTheDocument()
    expect(partial.getByRole('button', { name: 'Show evidence 5 for this requirement' })).toBeInTheDocument()

    expect(requirement('req-4').getByText('Uncertain')).toBeInTheDocument()
  })

  it('words a missing requirement as "no evidence found", never as something the person lacks', async () => {
    stubWideScreen()
    const generation = makeGeneration()
    generation.coverage[1].rationale = 'The candidate lacks Kubernetes experience; you lack this skill.'
    openWorkspace(generation)
    await draftIsShown()

    const missing = requirement('req-2')
    expect(missing.getByText('No evidence found')).toBeInTheDocument()
    expect(missing.getByText(/^No evidence found in the supplied profile\./)).toBeInTheDocument()
    expect(missing.queryByRole('button', { name: /Show evidence/ })).toBeNull()
    expect(document.body).not.toHaveTextContent(/lack/i)
    expect(document.body).not.toHaveTextContent(/match score|ATS score|fit score/i)
  })

  it('saves a correction with the expected revision and marks the requirement as corrected', async () => {
    stubWideScreen()
    const generation = makeGeneration()
    const corrected = makeGeneration({ revision: 4 })
    corrected.coverage[1] = {
      ...corrected.coverage[1],
      status: 'partial',
      user_corrected: true,
      note: 'Used it in a course project',
    }
    corrected.coverage_summary = { supported: 1, partial: 2, missing: 0, uncertain: 1, assessed: 3, percent: 66.7 }
    const { user, requests } = openWorkspace(generation, {
      [`PATCH ${DRAFT}`]: jsonResponse(corrected),
    })
    await draftIsShown()

    const row = requirement('req-2')
    const correct = row.getByRole('button', { name: 'Correct this status' })
    await user.click(correct)
    const status = row.getByRole('combobox', { name: 'Status' })
    expect(status).toHaveFocus()
    expect(status).toHaveTextContent('No evidence found')
    await user.click(status)
    await user.click(await screen.findByRole('option', { name: 'Partially supported' }))
    await user.click(row.getByLabelText('Note (optional)'))
    await user.paste('Used it in a course project')
    await user.click(row.getByRole('button', { name: 'Save correction' }))

    expect(await row.findByText('Corrected by you')).toBeInTheDocument()
    const patch = requests.find((request) => request.method === 'PATCH')!
    expect(patch.path).toBe(DRAFT)
    expect(patch.body).toEqual({
      expected_revision: 3,
      coverage_overrides: [
        { requirement_id: 'req-2', status: 'partial', note: 'Used it in a course project' },
      ],
    })
    expect(row.getByText('Partially supported')).toBeInTheDocument()
    expect(row.getByText('Your note: Used it in a course project')).toBeInTheDocument()
    expect(row.getByText(/^Automated assessment before your correction:/)).toBeInTheDocument()
    expect(row.queryByRole('combobox')).toBeNull()
    await waitFor(() => expect(correct).toHaveFocus())
    // The headline uses the server's recomputed figures.
    expect(sidePanel().getByText('66.7%')).toBeInTheDocument()
    expect(sidePanel().getByText('2 partially supported')).toBeInTheDocument()
  })

  it('keeps the correction form open with Retry when saving fails', async () => {
    stubWideScreen()
    const { user } = openWorkspace(makeGeneration(), {
      [`PATCH ${DRAFT}`]: errorResponse(503, 'database_unavailable', 'The database is unavailable.'),
    })
    await draftIsShown()

    const row = requirement('req-4')
    await user.click(row.getByRole('button', { name: 'Correct this status' }))
    await user.click(row.getByLabelText('Note (optional)'))
    await user.paste('Led weekly demos')
    await user.click(row.getByRole('button', { name: 'Save correction' }))

    const alert = await row.findByRole('alert')
    expect(alert).toHaveTextContent('Your correction was not saved')
    expect(within(alert).getByRole('button', { name: 'Retry' })).toBeInTheDocument()
    expect(row.getByLabelText('Note (optional)')).toHaveValue('Led weekly demos')
    expect(row.queryByText('Corrected by you')).toBeNull()
  })

  it('lists omitted statements and warnings with their reasons', async () => {
    stubWideScreen()
    openWorkspace(
      makeGeneration({
        omitted_claims: [
          {
            section: 'experience',
            text: 'Led a team of 12 engineers',
            reason: 'The number 12 does not appear in the cited evidence.',
          },
        ],
        warnings: ['Only 5 evidence records matched this job.'],
      }),
    )
    await draftIsShown()

    const panel = sidePanel()
    expect(panel.getByRole('button', { name: 'Omitted statements (1)' })).toHaveAttribute('aria-expanded', 'true')
    expect(panel.getByText('Led a team of 12 engineers')).toBeInTheDocument()
    expect(panel.getByText('Experience')).toBeInTheDocument()
    expect(
      panel.getByText('Reason: The number 12 does not appear in the cited evidence.'),
    ).toBeInTheDocument()
    expect(panel.getByRole('button', { name: 'Warnings (1)' })).toBeInTheDocument()
    expect(panel.getByText('Only 5 evidence records matched this job.')).toBeInTheDocument()
  })

  it('shows no omitted or warnings sections when the draft has none', async () => {
    stubWideScreen()
    openWorkspace()
    await draftIsShown()

    expect(sidePanel().queryByRole('button', { name: /Omitted statements/ })).toBeNull()
    expect(sidePanel().queryByRole('button', { name: /Warnings/ })).toBeNull()
  })
})

// The shared test setup reports that no media query matches, which is the
// single-column layout used on phones.
describe('Workspace on a narrow screen', () => {
  it('offers Resume, Cover letter and Coverage as tabs and has no side panel', async () => {
    const { user } = openWorkspace()
    await draftIsShown()

    expect(screen.getAllByRole('tab').map((tab) => tab.textContent)).toEqual([
      'Resume',
      'Cover letter',
      'Coverage',
    ])
    expect(screen.queryByRole('complementary')).toBeNull()
    expect(screen.getByRole('button', { name: 'Print / Save as PDF' })).toBeInTheDocument()

    await user.click(screen.getByRole('tab', { name: 'Coverage' }))

    const coverage = within(screen.getByRole('tabpanel', { name: 'Coverage' }))
    expect(coverage.getByRole('heading', { name: 'Evidence coverage' })).toBeVisible()
    expect(coverage.getByText('50%')).toBeInTheDocument()
    expect(claimElement('b-1')).not.toBeVisible()
    // Copy and Print belong to a document, so they are not offered on this tab.
    expect(screen.queryByRole('button', { name: 'Print / Save as PDF' })).toBeNull()
  })

  it('keeps the last viewed document as the print target while Coverage is shown', async () => {
    const { user } = openWorkspace()
    await draftIsShown()

    await user.click(screen.getByRole('tab', { name: 'Cover letter' }))
    await user.click(screen.getByRole('tab', { name: 'Coverage' }))

    expect(claimElement('cl-2')).not.toBeVisible()
    expect(claimElement('cl-2').closest('.ws-document-panel')).toHaveAttribute('data-print-target', 'true')
    expect(claimElement('b-1').closest('.ws-document-panel')).toHaveAttribute('data-print-target', 'false')
  })

  it('opens evidence in a sheet and returns to the badge when it closes', async () => {
    const { user } = openWorkspace(makeGeneration(), {
      'GET /api/evidence/ev-2': jsonResponse(makeEvidence()),
    })
    await draftIsShown()
    const badge = claim('b-1').getByRole('button', { name: 'Show evidence 2 for this statement' })

    await user.click(badge)

    const sheet = within(await screen.findByRole('dialog', { name: 'Evidence 2' }))
    expect(
      await sheet.findByText('Built React and TypeScript dashboards used by 35 internal support agents'),
    ).toBeInTheDocument()
    expect(sheet.getByText('Source: My resume')).toBeInTheDocument()

    await user.click(sheet.getByRole('button', { name: 'Close' }))

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    await waitFor(() => expect(badge).toHaveFocus())
  })

  it('goes from the evidence sheet to a citing statement on another tab', async () => {
    const { user } = openWorkspace(makeGeneration(), {
      'GET /api/evidence/ev-1': jsonResponse(makeEvidence({ evidence_id: 'ev-1' })),
    })
    await draftIsShown()
    await user.click(screen.getByRole('tab', { name: 'Coverage' }))

    await user.click(
      requirement('req-1').getByRole('button', { name: 'Show evidence 1 for this requirement' }),
    )
    const sheet = within(await screen.findByRole('dialog', { name: 'Evidence 1' }))
    const citing = await sheet.findAllByRole('button', { name: /^Go to statement:/ })
    await user.click(citing[1])

    await waitFor(() => expect(claimElement('b-1')).toHaveFocus())
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(screen.getByRole('tab', { name: 'Resume' })).toHaveAttribute('aria-selected', 'true')
  })
})
