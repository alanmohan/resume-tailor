import { act, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { makeBullet, makeConflict, makeProfile, makeRecord, sourceRef } from '@/test/fixtures'
import {
  deferred,
  errorResponse,
  jsonResponse,
  mockApi,
  seedSession,
  sessionInfo,
  type Routes,
} from '@/test/mockApi'
import { renderApp } from '@/test/render'
import type { Profile } from '@/lib/types'
import { INDEX_STALL_MS } from './IndexStatus'

const CONFIRM_LABEL = 'Confirm profile and build evidence index'

/** Open /profile with a session whose profile is the given one. */
function openProfile(profile: Profile, routes: Routes = {}) {
  seedSession()
  const requests = mockApi({
    'GET /api/session': jsonResponse(sessionInfo({ has_profile: true })),
    'GET /api/profile': jsonResponse(profile),
    ...routes,
  })
  return { requests, ...renderApp('/profile') }
}

describe('ProfilePage loading states', () => {
  it('links to Start when there is no session', () => {
    mockApi()
    renderApp('/profile')

    expect(screen.getByRole('heading', { name: 'No profile yet' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Go to Start' })).toHaveAttribute('href', '/')
  })

  it('treats 404 as "no profile yet", not as an error', async () => {
    seedSession()
    mockApi({ 'GET /api/profile': errorResponse(404, 'not_found', 'No profile') })
    renderApp('/profile')

    expect(await screen.findByRole('heading', { name: 'No profile yet' })).toBeInTheDocument()
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('shows a retryable error when the profile cannot be loaded', async () => {
    seedSession()
    let attempts = 0
    mockApi({
      'GET /api/profile': () => {
        attempts += 1
        return attempts === 1
          ? errorResponse(503, 'database_unavailable', 'The database is unavailable.')
          : jsonResponse(makeProfile())
      },
    })
    const { user } = renderApp('/profile')

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Your profile could not be loaded')
    await user.click(within(alert).getByRole('button', { name: 'Retry' }))

    expect(await screen.findByRole('heading', { name: 'Review your profile' })).toBeInTheDocument()
  })
})

describe('ProfilePage records', () => {
  it('groups records under category headings', async () => {
    openProfile(makeProfile())

    await screen.findByRole('heading', { name: 'Review your profile' })
    for (const heading of [
      'Contact',
      'Experience',
      'Projects',
      'Publications',
      'Education',
      'Certifications',
      'Skills',
      'Achievements',
    ]) {
      expect(screen.getByRole('heading', { name: heading })).toBeInTheDocument()
    }

    const experience = within(screen.getByRole('region', { name: 'Experience' }))
    expect(experience.getByLabelText('Job title')).toHaveValue('Software Engineer')
    expect(experience.getByLabelText('Employer')).toHaveValue('Northwind Robotics')
    expect(experience.getByLabelText('Start date')).toHaveValue('Aug 2022')
    expect(experience.getByLabelText('End date')).toHaveValue('Present')
    expect(
      experience.getByLabelText('Bullet 1 of Software Engineer at Northwind Robotics'),
    ).toHaveValue('Built a search service in Python')

    const education = within(screen.getByRole('region', { name: 'Education' }))
    expect(education.getByLabelText('Degree or program')).toHaveValue('B.S. Computer Science')
    expect(education.getByLabelText('Institution')).toHaveValue('Lakeshore State University')

    const skills = within(screen.getByRole('region', { name: 'Skills' }))
    expect(skills.getByLabelText('Group label')).toHaveValue('Languages')
    expect(skills.getByText('TypeScript')).toBeInTheDocument()
    // A skill group has no employer or dates.
    expect(skills.queryByLabelText('Start date')).toBeNull()

    expect(within(screen.getByRole('region', { name: 'Projects' })).getByText(/No match found in your sources/)).toBeInTheDocument()
    expect(screen.getByLabelText('Name')).toHaveValue('Riley Example')
  })

  it('shows needs-review reasons and the "Edited by you" badge', async () => {
    const profile = makeProfile({
      records: [
        makeRecord({
          needs_review: true,
          review_reasons: ['End date is ambiguous'],
          bullets: [
            makeBullet({
              bullet_id: 'bul-1',
              text: 'Cut build time by 40%',
              needs_review: true,
              review_reasons: ['source span not found'],
              source_ref: null,
            }),
            makeBullet({
              bullet_id: 'bul-2',
              text: 'Led the migration to TypeScript',
              provenance: 'user_edited',
              source_ref: sourceRef('Helped migrate the codebase to TypeScript'),
            }),
            makeBullet({ bullet_id: 'bul-3', text: 'Ran weekly demos', provenance: 'user_added', source_ref: null }),
          ],
        }),
      ],
    })
    const { user } = openProfile(profile)

    const card = within(await screen.findByRole('group', { name: 'Software Engineer at Northwind Robotics' }))
    expect(screen.getByText('2 items need review')).toBeInTheDocument()
    expect(card.getAllByText('Needs review')).toHaveLength(2)
    expect(card.getByText('End date is ambiguous')).toBeInTheDocument()
    expect(card.getByText('source span not found')).toBeInTheDocument()
    expect(card.getByText('Edited by you')).toBeInTheDocument()
    expect(card.getByText('Added by you')).toBeInTheDocument()

    // The edited bullet still links to the wording it was extracted from.
    await user.click(card.getByRole('button', { name: 'View original source' }))
    expect(await screen.findByText('Helped migrate the codebase to TypeScript')).toBeInTheDocument()
  })

  it('marks an item as edited as soon as the user changes it', async () => {
    const { user } = openProfile(makeProfile())

    const card = within(await screen.findByRole('group', { name: 'Software Engineer at Northwind Robotics' }))
    expect(card.queryByText('Edited by you')).toBeNull()

    await user.type(card.getByLabelText(/^Bullet 1 of/), ' and Go')

    expect(card.getByText('Edited by you')).toBeInTheDocument()
    expect(screen.getByText('You have unsaved changes.')).toBeInTheDocument()
  })

  it('renders a hostile source excerpt as text and creates no elements from it', async () => {
    const hostile = '<script>alert(1)</script><img src=x onerror="alert(2)"> Software Engineer'
    const profile = makeProfile({
      records: [makeRecord({ source_ref: sourceRef(hostile, '<b>Resume</b>') })],
      conflicts: [
        makeConflict({
          description: '<script>alert(3)</script> dates differ',
          values: [{ value: '<img src=x onerror="alert(4)">', source_ref: sourceRef(hostile) }],
        }),
      ],
    })
    const { user } = openProfile(profile)

    const card = within(await screen.findByRole('group', { name: 'Software Engineer at Northwind Robotics' }))
    await user.click(card.getAllByRole('button', { name: 'View source' })[0])

    await waitFor(() => expect(screen.getAllByText(hostile).length).toBe(2))
    expect(screen.getByText('Source: <b>Resume</b>')).toBeInTheDocument()
    expect(screen.getByText('<script>alert(3)</script> dates differ')).toBeInTheDocument()
    expect(document.querySelector('script')).toBeNull()
    expect(document.querySelector('img')).toBeNull()
    expect(document.querySelector('[onerror]')).toBeNull()
    expect(document.querySelector('main b')).toBeNull()
  })
})

describe('ProfilePage extraction notices', () => {
  it('shows each notice as plain text near the top without blocking confirmation', async () => {
    openProfile(
      makeProfile({
        notices: [
          { code: 'no_contact', message: 'No name or contact details were found.' },
          { code: 'uncaptured_lines', message: '2 lines of Resume were not captured: <b>Volunteer</b> tutor' },
        ],
      }),
    )

    const notices = (await screen.findByText('Notes from extraction')).closest('[role="status"]') as HTMLElement
    expect(within(notices).getByText('No name or contact details were found.')).toBeInTheDocument()
    // Markup inside a message is shown, never turned into elements.
    expect(
      within(notices).getByText('2 lines of Resume were not captured: <b>Volunteer</b> tutor'),
    ).toBeInTheDocument()
    expect(notices.querySelector('b')).toBeNull()
    expect(screen.getByRole('button', { name: CONFIRM_LABEL })).toBeEnabled()
  })

  it('shows no notice box when the profile has none, or the field is missing', async () => {
    openProfile(makeProfile())

    await screen.findByRole('heading', { name: 'Review your profile' })
    expect(screen.queryByText('Notes from extraction')).toBeNull()
  })
})

describe('ProfilePage keyboard focus after removing', () => {
  /** Activate a button the way a keyboard user does: focus it, then press Enter. */
  async function pressEnterOn(user: ReturnType<typeof openProfile>['user'], button: HTMLElement) {
    button.focus()
    await user.keyboard('{Enter}')
  }

  it('moves to Undo after removing a stored record, and to its title after Undo', async () => {
    const { user } = openProfile(makeProfile())
    await screen.findByRole('heading', { name: 'Review your profile' })

    await pressEnterOn(
      user,
      screen.getByRole('button', { name: 'Remove B.S. Computer Science at Lakeshore State University' }),
    )
    await waitFor(() => expect(screen.getByRole('button', { name: 'Undo' })).toHaveFocus())

    await pressEnterOn(user, screen.getByRole('button', { name: 'Undo' }))
    await waitFor(() => expect(screen.getByLabelText('Degree or program')).toHaveFocus())
  })

  it('moves to the section\'s Add button after removing a record that was never saved', async () => {
    const { user } = openProfile(makeProfile())
    await screen.findByRole('heading', { name: 'Review your profile' })
    await user.click(screen.getByRole('button', { name: 'Add project' }))

    await pressEnterOn(user, screen.getByRole('button', { name: 'Remove New project' }))

    await waitFor(() => expect(screen.getByRole('button', { name: 'Add project' })).toHaveFocus())
  })

  it('moves to the next bullet, or to Add bullet after the last one', async () => {
    const record = makeRecord({
      bullets: [makeBullet(), makeBullet({ bullet_id: 'bul-2', text: 'Mentored two interns' })],
    })
    const { user } = openProfile(makeProfile({ records: [record] }))
    await screen.findByRole('heading', { name: 'Review your profile' })
    const role = 'Software Engineer at Northwind Robotics'

    await pressEnterOn(user, screen.getByRole('button', { name: `Remove bullet 1 of ${role}` }))
    await waitFor(() => expect(screen.getByLabelText(`Bullet 1 of ${role}`)).toHaveFocus())
    expect(screen.getByLabelText(`Bullet 1 of ${role}`)).toHaveValue('Mentored two interns')

    await pressEnterOn(user, screen.getByRole('button', { name: `Remove bullet 1 of ${role}` }))
    await waitFor(() => expect(screen.getByRole('button', { name: `Add bullet to ${role}` })).toHaveFocus())
  })

  it('moves to the skill input after removing a skill', async () => {
    const { user } = openProfile(makeProfile())
    await screen.findByRole('heading', { name: 'Review your profile' })
    const skills = within(screen.getByRole('region', { name: 'Skills' }))

    await pressEnterOn(user, skills.getByRole('button', { name: 'Remove TypeScript' }))

    await waitFor(() => expect(skills.getByLabelText('Skills in this group')).toHaveFocus())
    expect(skills.queryByText('TypeScript')).toBeNull()
  })
})

describe('ProfilePage editing and saving', () => {
  it('saves edits with the expected version and shows the saved result', async () => {
    const profile = makeProfile()
    const saved = makeProfile({
      version: 2,
      records: [makeRecord({ title: 'Senior Software Engineer', provenance: 'user_edited' })],
    })
    const { user, requests } = openProfile(profile, { 'PATCH /api/profile': jsonResponse(saved) })

    const title = await screen.findByLabelText('Job title')
    const save = screen.getByRole('button', { name: 'Save changes' })
    expect(save).toBeDisabled()

    await user.clear(title)
    await user.type(title, 'Senior Software Engineer')
    await user.click(
      screen.getByRole('button', { name: 'Add bullet to Senior Software Engineer at Northwind Robotics' }),
    )
    await user.type(screen.getByLabelText(/^Bullet 2 of/), 'Mentored two interns')
    await user.click(save)

    expect(await screen.findByText('All changes are saved.')).toBeInTheDocument()
    const patch = requests.find((request) => request.method === 'PATCH')!
    const body = patch.body as { expected_version: number; records: Record<string, unknown>[] }
    expect(body.expected_version).toBe(1)
    expect(body.records[0]).toMatchObject({
      record_id: 'rec-1',
      title: 'Senior Software Engineer',
      start_date: 'Aug 2022',
      bullets: [
        { bullet_id: 'bul-1', text: 'Built a search service in Python' },
        { bullet_id: null, text: 'Mentored two interns' },
      ],
    })
    expect(screen.getByLabelText('Job title')).toHaveValue('Senior Software Engineer')
    expect(screen.getByRole('button', { name: 'Save changes' })).toBeDisabled()
  })

  it('adds a record, edits skills and can undo a removal', async () => {
    const { user, requests } = openProfile(makeProfile(), {
      'PATCH /api/profile': jsonResponse(makeProfile({ version: 2 })),
    })
    await screen.findByRole('heading', { name: 'Review your profile' })

    await user.click(screen.getByRole('button', { name: 'Add project' }))
    const projects = within(screen.getByRole('region', { name: 'Projects' }))
    expect(projects.getByLabelText('Project name')).toHaveFocus()
    await user.type(projects.getByLabelText('Project name'), 'Trail Map App')
    await user.type(projects.getByLabelText('Skills used'), 'React, Leaflet{Enter}')
    expect(projects.getByText('Leaflet')).toBeInTheDocument()

    const skills = within(screen.getByRole('region', { name: 'Skills' }))
    await user.click(skills.getByRole('button', { name: 'Remove TypeScript' }))

    await user.click(screen.getByRole('button', { name: 'Remove B.S. Computer Science at Lakeshore State University' }))
    expect(screen.getByText(/will be removed when you save/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Undo' }))
    expect(screen.getByLabelText('Degree or program')).toHaveValue('B.S. Computer Science')

    await user.click(screen.getByRole('button', { name: 'Save changes' }))

    await waitFor(() => expect(requests.some((request) => request.method === 'PATCH')).toBe(true))
    const body = requests.find((request) => request.method === 'PATCH')!.body as {
      records: { record_id: string | null; category: string; title: string; skills: string[] }[]
    }
    expect(body.records).toHaveLength(4)
    expect(body.records.find((record) => record.record_id === 'rec-3')!.skills).toEqual(['Python'])
    expect(body.records[3]).toMatchObject({
      record_id: null,
      category: 'project',
      title: 'Trail Map App',
      skills: ['React', 'Leaflet'],
    })
  })

  it('blocks saving a record without a title and says why', async () => {
    const { user, requests } = openProfile(makeProfile())

    const title = await screen.findByLabelText('Job title')
    await user.clear(title)
    await user.click(screen.getByRole('button', { name: 'Save changes' }))

    expect(screen.getByText('Enter a title, or remove this record.')).toBeInTheDocument()
    expect(title).toHaveAttribute('aria-invalid', 'true')
    expect(requests.some((request) => request.method === 'PATCH')).toBe(false)
  })

  it('offers to reload after a version conflict and keeps the edits until then', async () => {
    const latest = makeProfile({ version: 5, records: [makeRecord({ title: 'Staff Engineer' })] })
    let profileReads = 0
    const { user } = openProfile(makeProfile(), {
      'GET /api/profile': () => {
        profileReads += 1
        return jsonResponse(profileReads === 1 ? makeProfile() : latest)
      },
      'PATCH /api/profile': errorResponse(409, 'version_conflict', 'The profile changed since you loaded it.'),
    })

    const title = await screen.findByLabelText('Job title')
    await user.type(title, ' II')
    await user.click(screen.getByRole('button', { name: 'Save changes' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Your changes were not saved')
    expect(alert).toHaveTextContent('The profile changed since you loaded it.')
    expect(screen.getByLabelText('Job title')).toHaveValue('Software Engineer II')

    await user.click(within(alert).getByRole('button', { name: /Reload latest version/ }))

    await waitFor(() => expect(screen.getByLabelText('Job title')).toHaveValue('Staff Engineer'))
    expect(screen.queryByText('Your changes were not saved')).toBeNull()
  })
})

describe('ProfilePage conflicts', () => {
  it('lists each conflicting value with its source and blocks confirmation', async () => {
    const { user, requests } = openProfile(
      makeProfile({ conflicts: [makeConflict({ field: 'start_date' })] }),
    )

    const panel = within(await screen.findByRole('region', { name: 'Conflicts between your sources' }))
    // The API's field name is shown as words, not as an identifier.
    expect(panel.getByRole('heading', { name: 'Conflicting start date' })).toBeInTheDocument()
    expect(panel.getByText('Unresolved')).toBeInTheDocument()
    expect(panel.getByText('Aug 2022')).toBeInTheDocument()
    expect(panel.getByText('Sep 2022')).toBeInTheDocument()
    expect(panel.getByText('Software Engineer, Sep 2022 to now')).toBeInTheDocument()
    expect(panel.getByText('Source: LinkedIn profile')).toBeInTheDocument()
    expect(panel.getByText('Affects: Software Engineer at Northwind Robotics')).toBeInTheDocument()

    const confirm = screen.getByRole('button', { name: CONFIRM_LABEL })
    expect(confirm).toBeDisabled()
    expect(confirm).toHaveAccessibleDescription(
      'Resolve or dismiss the 1 open conflict above before confirming.',
    )
    await user.click(confirm)
    expect(requests.some((request) => request.path === '/api/profile/confirm')).toBe(false)
  })

  it('saves a resolution together with pending edits and unblocks confirmation', async () => {
    const resolved = makeProfile({
      version: 2,
      records: [makeRecord({ start_date: 'Sep 2022', provenance: 'user_edited' })],
      conflicts: [makeConflict({ resolution: 'resolved' })],
    })
    const { user, requests } = openProfile(makeProfile({ conflicts: [makeConflict()] }), {
      'PATCH /api/profile': jsonResponse(resolved),
    })

    const experience = within(await screen.findByRole('region', { name: 'Experience' }))
    const start = experience.getByLabelText('Start date')
    await user.clear(start)
    await user.type(start, 'Sep 2022')
    await user.click(screen.getByRole('button', { name: 'Mark resolved' }))

    const panel = within(await screen.findByRole('region', { name: 'Conflicts between your sources' }))
    expect(await panel.findByText('Resolved')).toBeInTheDocument()
    const body = requests.find((request) => request.method === 'PATCH')!.body as Record<string, unknown>
    expect(body.conflict_resolutions).toEqual([{ conflict_id: 'con-1', resolution: 'resolved' }])
    expect((body.records as { start_date: string }[])[0].start_date).toBe('Sep 2022')
    expect(screen.getByRole('button', { name: CONFIRM_LABEL })).toBeEnabled()
    expect(panel.queryByRole('button', { name: 'Mark resolved' })).toBeNull()
  })

  it('can dismiss a conflict as not a conflict', async () => {
    const { user, requests } = openProfile(makeProfile({ conflicts: [makeConflict()] }), {
      'PATCH /api/profile': jsonResponse(
        makeProfile({ version: 2, conflicts: [makeConflict({ resolution: 'dismissed' })] }),
      ),
    })

    await user.click(await screen.findByRole('button', { name: 'Not a conflict' }))

    await waitFor(() => expect(requests.some((request) => request.method === 'PATCH')).toBe(true))
    const body = requests.find((request) => request.method === 'PATCH')!.body as Record<string, unknown>
    expect(body.conflict_resolutions).toEqual([{ conflict_id: 'con-1', resolution: 'dismissed' }])
  })
})

describe('ProfilePage confirmation', () => {
  const confirmed = makeProfile({
    status: 'confirmed',
    index_state: 'indexed',
    indexed_version: 1,
    index_progress: { total: 7, embedded: 7 },
  })

  it('confirms, shows real progress counts while indexing and then the next step', async () => {
    const confirmResponse = deferred<Response>()
    let indexing = false
    const { user, requests } = openProfile(makeProfile(), {
      'GET /api/profile': () =>
        jsonResponse(
          indexing
            ? makeProfile({ index_state: 'indexing', index_progress: { total: 7, embedded: 3 } })
            : makeProfile(),
        ),
      'POST /api/profile/confirm': () => {
        indexing = true
        return confirmResponse.promise
      },
    })

    await user.click(await screen.findByRole('button', { name: CONFIRM_LABEL }))

    expect(await screen.findByText('Building evidence records...')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Confirming...' })).toBeDisabled()
    // The page polls GET /api/profile and shows the server's own counts.
    expect(await screen.findByText('Embedding 3 of 7 evidence records')).toBeInTheDocument()
    expect(screen.getByLabelText('Job title')).toBeDisabled()

    confirmResponse.resolve(jsonResponse(confirmed))

    expect(await screen.findByText('Profile confirmed')).toBeInTheDocument()
    expect(screen.getByText(/7 evidence records are indexed/)).toBeInTheDocument()
    const links = screen.getAllByRole('link', { name: 'Continue to target job' })
    expect(links[0]).toHaveAttribute('href', '/job')
    expect(requests.find((r) => r.path === '/api/profile/confirm')!.body).toEqual({ expected_version: 1 })
    // Nothing was edited, so confirming did not send a PATCH first.
    expect(requests.some((request) => request.method === 'PATCH')).toBe(false)
  })

  it('saves pending edits first and confirms the new version', async () => {
    const saved = makeProfile({ version: 2, records: [makeRecord({ title: 'Lead Engineer' })] })
    const { user, requests } = openProfile(makeProfile(), {
      'PATCH /api/profile': jsonResponse(saved),
      'POST /api/profile/confirm': jsonResponse({ ...confirmed, version: 2, indexed_version: 2, records: saved.records }),
    })

    const title = await screen.findByLabelText('Job title')
    await user.clear(title)
    await user.type(title, 'Lead Engineer')
    await user.click(screen.getByRole('button', { name: CONFIRM_LABEL }))

    expect(await screen.findByText('Profile confirmed')).toBeInTheDocument()
    const writes = requests.filter((request) => request.method !== 'GET')
    expect(writes.map((request) => `${request.method} ${request.path}`)).toEqual([
      'PATCH /api/profile',
      'POST /api/profile/confirm',
    ])
    expect(writes[1].body).toEqual({ expected_version: 2 })
  })

  it('shows a recoverable error with Retry when indexing fails', async () => {
    let attempts = 0
    const { user } = openProfile(makeProfile(), {
      'POST /api/profile/confirm': () => {
        attempts += 1
        return attempts === 1
          ? errorResponse(503, 'provider_rate_limited', 'The AI provider is busy. Try again shortly.', {
              retryable: true,
            })
          : jsonResponse(confirmed)
      },
    })

    await user.click(await screen.findByRole('button', { name: CONFIRM_LABEL }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('The profile was not confirmed')
    expect(alert).toHaveTextContent('The AI provider is busy. Try again shortly.')
    expect(screen.getByLabelText('Job title')).toHaveValue('Software Engineer')

    await user.click(within(alert).getByRole('button', { name: 'Retry confirmation' }))

    expect(await screen.findByText('Profile confirmed')).toBeInTheDocument()
  })

  it('shows the stored failure after a reload and lets the user retry', async () => {
    openProfile(
      makeProfile({ index_state: 'failed', index_error: 'Embedding stopped after 3 of 7 records.' }),
    )

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Embedding stopped after 3 of 7 records.')
    expect(within(alert).getByRole('button', { name: 'Retry confirmation' })).toBeEnabled()
  })

  it('requires re-confirmation when the server dropped indexed_version but a draft already exists', async () => {
    openProfile(makeProfile({ version: 3, status: 'draft', index_state: 'not_indexed', indexed_version: null }), {
      'GET /api/generations': jsonResponse({
        generations: [
          {
            generation_id: 'gen-1',
            job_id: 'job-1',
            job_title: 'Backend Engineer',
            company: 'Acme Analytics',
            status: 'completed',
            stale: true,
            created_at: '2026-10-01T12:00:00Z',
          },
        ],
      }),
    })

    expect(await screen.findByText('Confirm again before generating')).toBeInTheDocument()
    expect(screen.getByText(/You changed the profile after confirming it/)).toBeInTheDocument()
    expect(screen.queryByText('Draft profile')).toBeNull()
    expect(screen.getByRole('button', { name: CONFIRM_LABEL })).toBeEnabled()
  })

  it('calls a profile that was never confirmed a draft, not a changed one', async () => {
    openProfile(makeProfile())

    expect(await screen.findByText('Draft profile')).toBeInTheDocument()
    expect(screen.queryByText('Confirm again before generating')).toBeNull()
  })

  it('requires re-confirmation when the profile was edited after confirming', async () => {
    openProfile(
      makeProfile({ version: 3, status: 'draft', index_state: 'indexed', indexed_version: 2 }),
    )

    expect(await screen.findByText('Confirm again before generating')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: CONFIRM_LABEL })).toBeEnabled()
    expect(screen.queryByRole('link', { name: 'Continue to target job' })).toBeNull()
  })
})

describe('ProfilePage when indexing was interrupted', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it('says indexing seems to have stopped once the count stands still, and resumes on confirm', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    let embedded = 12
    const stuck = () =>
      makeProfile({ index_state: 'indexing', index_progress: { total: 40, embedded } })
    const { user, requests } = openProfile(stuck(), {
      'GET /api/profile': () => jsonResponse(stuck()),
      'POST /api/profile/confirm': jsonResponse(
        makeProfile({
          status: 'confirmed',
          index_state: 'indexed',
          indexed_version: 1,
          index_progress: { total: 40, embedded: 40 },
        }),
      ),
    })

    expect(await screen.findByText('Embedding 12 of 40 evidence records')).toBeInTheDocument()
    expect(screen.getByText(/Indexing is running on the server/)).toBeInTheDocument()
    // The profile can be confirmed again at any time, also before the hint appears.
    expect(screen.getByRole('button', { name: CONFIRM_LABEL })).toBeEnabled()

    // Progress restarts the wait: 15 s at 12, then the count moves to 13.
    await act(() => vi.advanceTimersByTimeAsync(15_000))
    embedded = 13
    await act(() => vi.advanceTimersByTimeAsync(10_000))
    expect(screen.getByText('Embedding 13 of 40 evidence records')).toBeInTheDocument()
    expect(screen.queryByText('Indexing seems to have stopped')).toBeNull()

    await act(() => vi.advanceTimersByTimeAsync(INDEX_STALL_MS))
    const stopped = screen.getByText('Indexing seems to have stopped').closest('[role="status"]') as HTMLElement
    expect(stopped).toHaveTextContent('The count has stayed at 13 of 40 evidence records')
    expect(stopped).toHaveTextContent('confirm the profile again to resume indexing')
    expect(screen.queryByText(/Indexing is running on the server/)).toBeNull()
    expect(screen.getByRole('button', { name: CONFIRM_LABEL })).toBeEnabled()

    await user.click(within(stopped).getByRole('button', { name: 'Confirm again' }))

    expect(await screen.findByText('Profile confirmed')).toBeInTheDocument()
    expect(requests.find((request) => request.path === '/api/profile/confirm')!.body).toEqual({
      expected_version: 1,
    })
  })

  it('keeps Confirm usable for a profile whose indexing failed', async () => {
    openProfile(makeProfile({ index_state: 'failed', index_error: 'Embedding stopped after 3 of 7 records.' }))

    await screen.findByRole('alert')
    expect(screen.getByRole('button', { name: CONFIRM_LABEL })).toBeEnabled()
  })
})
