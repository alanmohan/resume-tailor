import { screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { makeProfile } from '@/test/fixtures'
import {
  TEST_LIMITS,
  deferred,
  errorResponse,
  jsonResponse,
  sessionInfo,
  type RecordedRequest,
} from '@/test/mockApi'
import type { Job } from '@/lib/types'
import { POLL_INTERVAL_MS } from '../generateDraft'
import {
  makeGeneration,
  makeGenerationSummary,
  makeJob,
  makeRequirement,
  readyProfile,
} from './jobFixtures'
import { openJobPage } from './renderJob'

const GENERATE_LABEL = 'Generate tailored resume and cover letter'
const UNSAVED_TEXT = 'You have unsaved changes. Save them before generating.'

afterEach(() => {
  vi.useRealTimers()
})

function generateButton() {
  return screen.getByRole('button', { name: GENERATE_LABEL })
}

function saveButton() {
  return screen.getByRole('button', { name: 'Save changes' })
}

/** Wait for the review of `job` to be on screen. */
async function reviewLoaded() {
  await screen.findByRole('heading', { name: 'Review the job requirements' })
}

function requestsTo(requests: RecordedRequest[], method: string, path: string) {
  return requests.filter((request) => request.method === method && request.path === path)
}

/** What the server would answer after saving `body` on top of `job`. */
function savedJob(job: Job, request: RecordedRequest): Job {
  const body = request.body as {
    title: string | null
    company: string | null
    requirements: { requirement_id: string | null; text: string; category: string; importance: string }[]
  }
  return {
    ...job,
    version: job.version + 1,
    title: body.title,
    company: body.company,
    requirements: body.requirements.map((input, index) => {
      const stored = job.requirements.find((item) => item.requirement_id === input.requirement_id)
      return makeRequirement({
        ...stored,
        requirement_id: input.requirement_id ?? `req-added-${index}`,
        text: input.text,
        category: input.category as Job['requirements'][number]['category'],
        importance: input.importance as Job['requirements'][number]['importance'],
        user_edited: stored ? stored.user_edited || stored.text !== input.text : false,
      })
    }),
  }
}

describe('JobReview requirement editing', () => {
  it('shows flags and the supporting excerpt of each requirement', async () => {
    const job = makeJob()
    job.requirements[1].user_edited = true
    const { user } = openJobPage({ jobs: [job] })
    await reviewLoaded()

    const first = within(screen.getByRole('group', { name: 'Requirement 1' }))
    expect(first.getByRole('combobox', { name: 'Category of requirement 1' })).toHaveTextContent('Skill')
    expect(first.getByRole('combobox', { name: 'Importance of requirement 1' })).toHaveTextContent(
      'Required',
    )
    expect(first.queryByText('User edited')).toBeNull()

    // The excerpt opens on demand and names where it came from.
    await user.click(first.getByRole('button', { name: 'View source of requirement 1' }))
    expect(
      await screen.findByText('Strong Python skills and experience building REST APIs'),
    ).toBeInTheDocument()
    expect(screen.getByText('Source: Job description')).toBeInTheDocument()
    await user.keyboard('{Escape}')

    const second = within(screen.getByRole('group', { name: 'Requirement 2' }))
    expect(second.getByText('User edited')).toBeInTheDocument()
    expect(second.getByRole('button', { name: 'View original source of requirement 2' })).toBeInTheDocument()

    // An inferred requirement has no excerpt; its badge explains itself.
    const fourth = within(screen.getByRole('group', { name: 'Requirement 4' }))
    expect(fourth.queryByRole('button', { name: /View/ })).toBeNull()
    await user.hover(fourth.getByRole('button', { name: 'Inferred' }))
    expect(await screen.findByRole('tooltip')).toHaveTextContent(
      'Not stated explicitly in the posting.',
    )
  })

  it('marks edits as unsaved and saves them with the version the draft is based on', async () => {
    const job = makeJob({ version: 3 })
    const { requests, user } = openJobPage({
      jobs: [job],
      routes: { 'PATCH /api/jobs/job-1': (request) => jsonResponse(savedJob(job, request)) },
    })
    await reviewLoaded()
    expect(saveButton()).toBeDisabled()
    expect(screen.getByText('All changes are saved.')).toBeInTheDocument()

    const text = screen.getByLabelText('Requirement 1 text')
    await user.clear(text)
    await user.type(text, '  Strong Python and FastAPI skills ')
    await user.clear(screen.getByLabelText('Company'))

    expect(screen.getByText(UNSAVED_TEXT)).toBeInTheDocument()
    // The wording no longer matches the posting, so it is flagged at once.
    expect(
      within(screen.getByRole('group', { name: 'Requirement 1' })).getByText('User edited'),
    ).toBeInTheDocument()

    await user.click(saveButton())

    expect(await screen.findByText('Changes saved')).toBeInTheDocument()
    const patches = requestsTo(requests, 'PATCH', '/api/jobs/job-1')
    expect(patches).toHaveLength(1)
    expect(patches[0].body).toEqual({
      expected_version: 3,
      title: 'Applied Machine Learning Engineer',
      company: null,
      requirements: [
        { requirement_id: 'req-1', text: 'Strong Python and FastAPI skills', category: 'skill', importance: 'required' },
        { requirement_id: 'req-2', text: 'Experience with Docker', category: 'skill', importance: 'required' },
        { requirement_id: 'req-3', text: 'Experience with AWS', category: 'skill', importance: 'preferred' },
        { requirement_id: 'req-4', text: 'Comfortable working with support teams', category: 'responsibility', importance: 'preferred' },
      ],
    })
    expect(screen.getByLabelText('Requirement 1 text')).toHaveValue('Strong Python and FastAPI skills')
    expect(screen.getByText('All changes are saved.')).toBeInTheDocument()
    expect(saveButton()).toBeDisabled()
  })

  it('sends the next save with the new version', async () => {
    const job = makeJob({ version: 1 })
    let current = job
    const { requests, user } = openJobPage({
      jobs: [job],
      routes: {
        'PATCH /api/jobs/job-1': (request) => {
          current = savedJob(current, request)
          return jsonResponse(current)
        },
      },
    })
    await reviewLoaded()

    await user.type(screen.getByLabelText('Requirement 2 text'), ' Compose')
    await user.click(saveButton())
    await waitFor(() => expect(saveButton()).toBeDisabled())
    await user.type(screen.getByLabelText('Requirement 2 text'), ' and Swarm')
    await user.click(saveButton())

    await waitFor(() => expect(requestsTo(requests, 'PATCH', '/api/jobs/job-1')).toHaveLength(2))
    const versions = requestsTo(requests, 'PATCH', '/api/jobs/job-1').map(
      (request) => (request.body as { expected_version: number }).expected_version,
    )
    expect(versions).toEqual([1, 2])
  })

  it('adds a requirement, focuses it, and will not save it blank', async () => {
    const job = makeJob()
    const { requests, user } = openJobPage({
      jobs: [job],
      routes: { 'PATCH /api/jobs/job-1': (request) => jsonResponse(savedJob(job, request)) },
    })
    await reviewLoaded()

    await user.click(screen.getByRole('button', { name: 'Add requirement to Preferred' }))

    const preferred = within(screen.getByRole('region', { name: 'Preferred (3)' }))
    const added = preferred.getByLabelText('Requirement 5 text')
    await waitFor(() => expect(added).toHaveFocus())
    expect(preferred.getByText('Added by you, not saved yet')).toBeInTheDocument()
    expect(screen.getByText('5 / 25 requirements')).toBeInTheDocument()

    await user.click(saveButton())
    expect(await screen.findByText('Describe the requirement, or remove it.')).toBeInTheDocument()
    expect(added).toBeInvalid()
    expect(requestsTo(requests, 'PATCH', '/api/jobs/job-1')).toHaveLength(0)

    // The message follows the edit, and the save sends a null ID for the new row.
    await user.type(added, 'Experience with Terraform')
    expect(screen.queryByText('Describe the requirement, or remove it.')).toBeNull()
    await user.click(saveButton())

    expect(await screen.findByText('Changes saved')).toBeInTheDocument()
    const body = requestsTo(requests, 'PATCH', '/api/jobs/job-1')[0].body as {
      requirements: unknown[]
    }
    expect(body.requirements).toHaveLength(5)
    expect(body.requirements[4]).toEqual({
      requirement_id: null,
      text: 'Experience with Terraform',
      category: 'skill',
      importance: 'preferred',
    })
    // Once stored it is an ordinary requirement.
    expect(screen.queryByText('Added by you, not saved yet')).toBeNull()
  })

  it('removes a stored requirement only on save, with Undo until then', async () => {
    const job = makeJob()
    const { requests, user } = openJobPage({
      jobs: [job],
      routes: { 'PATCH /api/jobs/job-1': (request) => jsonResponse(savedJob(job, request)) },
    })
    await reviewLoaded()

    await user.click(screen.getByRole('button', { name: 'Remove requirement 1' }))

    expect(screen.getByText(/will be removed when you\s+save/)).toBeInTheDocument()
    const undo = screen.getByRole('button', { name: 'Undo removing requirement 1' })
    await waitFor(() => expect(undo).toHaveFocus())
    expect(screen.getByText('3 / 25 requirements')).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Required (1)' })).toBeInTheDocument()

    await user.click(undo)
    await waitFor(() => expect(screen.getByLabelText('Requirement 1 text')).toHaveFocus())
    expect(screen.getByText('All changes are saved.')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Remove requirement 1' }))
    await user.click(saveButton())

    expect(await screen.findByText('Changes saved')).toBeInTheDocument()
    const body = requestsTo(requests, 'PATCH', '/api/jobs/job-1')[0].body as {
      requirements: { requirement_id: string }[]
    }
    expect(body.requirements.map((item) => item.requirement_id)).toEqual(['req-2', 'req-3', 'req-4'])
    expect(screen.getByLabelText('Requirement 1 text')).toHaveValue('Experience with Docker')
  })

  it('drops a requirement that was never saved and returns focus to the add button', async () => {
    const { user } = openJobPage({ jobs: [makeJob()] })
    await reviewLoaded()

    await user.click(screen.getByRole('button', { name: 'Add requirement to Required' }))
    expect(screen.getByText(UNSAVED_TEXT)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Remove requirement 3' }))

    expect(screen.queryByText(/will be removed/)).toBeNull()
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Add requirement to Required' })).toHaveFocus(),
    )
    expect(screen.getByText('All changes are saved.')).toBeInTheDocument()
  })

  it('moves a requirement to the other group when its importance changes', async () => {
    const job = makeJob()
    const { requests, user } = openJobPage({
      jobs: [job],
      routes: { 'PATCH /api/jobs/job-1': (request) => jsonResponse(savedJob(job, request)) },
    })
    await reviewLoaded()

    await user.click(screen.getByRole('combobox', { name: 'Importance of requirement 2' }))
    await user.click(await screen.findByRole('option', { name: 'Preferred' }))

    const preferred = within(screen.getByRole('region', { name: 'Preferred (3)' }))
    // It is now the first preferred requirement, so it is numbered after the required ones.
    expect(preferred.getByLabelText('Requirement 2 text')).toHaveValue('Experience with Docker')
    expect(screen.getByRole('region', { name: 'Required (1)' })).toBeInTheDocument()
    await waitFor(() =>
      expect(preferred.getByRole('combobox', { name: 'Importance of requirement 2' })).toHaveFocus(),
    )
    // Changing the importance is an edit too, exactly as the server records it.
    expect(
      within(preferred.getByRole('group', { name: 'Requirement 2' })).getByText('User edited'),
    ).toBeInTheDocument()

    await user.click(screen.getByRole('combobox', { name: 'Category of requirement 2' }))
    await user.click(await screen.findByRole('option', { name: 'Experience' }))
    await user.click(saveButton())

    await waitFor(() => expect(requestsTo(requests, 'PATCH', '/api/jobs/job-1')).toHaveLength(1))
    const body = requestsTo(requests, 'PATCH', '/api/jobs/job-1')[0].body as {
      requirements: unknown[]
    }
    expect(body.requirements[1]).toEqual({
      requirement_id: 'req-2',
      text: 'Experience with Docker',
      category: 'experience',
      importance: 'preferred',
    })
  })

  it('shows the count against the limit and stops adding at the limit', async () => {
    const { user } = openJobPage({
      jobs: [makeJob()],
      routes: {
        'GET /api/session': jsonResponse(
          sessionInfo({ has_profile: true, limits: { ...TEST_LIMITS, max_requirements: 5 } }),
        ),
      },
    })
    await reviewLoaded()
    await screen.findByText('4 / 5 requirements')

    await user.click(screen.getByRole('button', { name: 'Add requirement to Required' }))

    expect(screen.getByText('5 / 5 requirements')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Add requirement to Required' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Add requirement to Preferred' })).toBeDisabled()
    expect(
      screen.getByText('This is the most a job can have. Remove one to add another.'),
    ).toBeInTheDocument()
  })

  it('offers to reload when the job changed elsewhere', async () => {
    const job = makeJob({ version: 2 })
    const latest: Job = {
      ...job,
      version: 5,
      requirements: [makeRequirement({ requirement_id: 'req-1', text: 'Python, changed in another tab' })],
    }
    let serveLatest = false
    const { requests, user } = openJobPage({
      routes: {
        'GET /api/jobs': jsonResponse({ jobs: [] }),
        'GET /api/jobs/job-1': () => jsonResponse(serveLatest ? latest : job),
        'PATCH /api/jobs/job-1': () => {
          serveLatest = true
          return errorResponse(409, 'version_conflict', 'This job was changed elsewhere.')
        },
      },
      route: '/job?job=job-1',
    })
    await reviewLoaded()

    await user.type(screen.getByLabelText('Requirement 1 text'), ' (my edit)')
    await user.click(saveButton())

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Your changes were not saved')
    expect(alert).toHaveTextContent('This job was changed elsewhere.')
    expect(within(alert).queryByRole('button', { name: 'Retry' })).toBeNull()
    // The edit is still there until the user decides.
    expect(screen.getByLabelText('Requirement 1 text')).toHaveValue('Strong Python skills (my edit)')

    await user.click(within(alert).getByRole('button', { name: /Reload latest version/ }))

    await waitFor(() =>
      expect(screen.getByLabelText('Requirement 1 text')).toHaveValue('Python, changed in another tab'),
    )
    expect(screen.queryByRole('alert')).toBeNull()
    expect(saveButton()).toBeDisabled()

    // The next save is based on the reloaded version.
    serveLatest = false
    await user.type(screen.getByLabelText('Requirement 1 text'), '!')
    await user.click(saveButton())
    await waitFor(() => expect(requestsTo(requests, 'PATCH', '/api/jobs/job-1')).toHaveLength(2))
    expect(requestsTo(requests, 'PATCH', '/api/jobs/job-1')[1].body).toMatchObject({
      expected_version: 5,
    })
  })

  it('keeps the edits and offers Retry when saving fails', async () => {
    const job = makeJob()
    let attempts = 0
    const { user } = openJobPage({
      jobs: [job],
      routes: {
        'PATCH /api/jobs/job-1': (request) => {
          attempts += 1
          return attempts === 1
            ? errorResponse(503, 'database_unavailable', 'The database is unavailable.')
            : jsonResponse(savedJob(job, request))
        },
      },
    })
    await reviewLoaded()

    await user.type(screen.getByLabelText('Requirement 3 text'), ' (EC2, S3)')
    await user.click(saveButton())

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('The database is unavailable.')
    expect(screen.getByLabelText('Requirement 3 text')).toHaveValue('Experience with AWS (EC2, S3)')

    await user.click(within(alert).getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText('Changes saved')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).toBeNull()
  })
})

describe('JobReview generation', () => {
  it('generates with an Idempotency-Key and opens the workspace', async () => {
    const response = deferred<Response>()
    const { requests, user } = openJobPage({
      jobs: [makeJob()],
      routes: { 'POST /api/generations': () => response.promise },
    })
    await reviewLoaded()
    expect(generateButton()).toBeEnabled()

    await user.click(generateButton())

    // While the request runs: an honest label, and nothing can be submitted twice.
    expect(
      await screen.findByText('Generating... this can take a minute or two'),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Generating...' })).toBeDisabled()
    expect(saveButton()).toBeDisabled()
    expect(screen.getByLabelText('Requirement 1 text')).toBeDisabled()

    response.resolve(jsonResponse(makeGeneration({ generation_id: 'gen-42' }), 201))

    expect(await screen.findByRole('heading', { name: 'Workspace gen-42' })).toBeInTheDocument()
    const posts = requestsTo(requests, 'POST', '/api/generations')
    expect(posts).toHaveLength(1)
    expect(posts[0].body).toEqual({ job_id: 'job-1' })
    expect(posts[0].headers['Idempotency-Key']).toMatch(/^[0-9a-f-]{36}$/)
  })

  it('keeps everything after a provider error and retries with the same key', async () => {
    let attempts = 0
    const { requests, user } = openJobPage({
      jobs: [makeJob()],
      routes: {
        'POST /api/generations': () => {
          attempts += 1
          return attempts === 1
            ? errorResponse(502, 'provider_unavailable', 'The AI provider is unavailable.', {
                retryable: true,
              })
            : jsonResponse(makeGeneration({ generation_id: 'gen-7' }), 201)
        },
      },
    })
    await reviewLoaded()

    await user.click(generateButton())

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('The draft was not generated')
    expect(alert).toHaveTextContent('The AI provider is unavailable.')
    // The job and its requirements are untouched and generation can be tried again.
    expect(screen.getByLabelText('Requirement 1 text')).toHaveValue('Strong Python skills')
    expect(generateButton()).toBeEnabled()

    await user.click(within(alert).getByRole('button', { name: 'Retry' }))

    expect(await screen.findByRole('heading', { name: 'Workspace gen-7' })).toBeInTheDocument()
    const keys = requestsTo(requests, 'POST', '/api/generations').map(
      (request) => request.headers['Idempotency-Key'],
    )
    expect(keys).toHaveLength(2)
    expect(keys[0]).toBeTruthy()
    expect(keys[1]).toBe(keys[0])
  })

  it('uses a new key once the job has been saved again', async () => {
    const job = makeJob()
    const { requests, user } = openJobPage({
      jobs: [job],
      routes: {
        'POST /api/generations': errorResponse(504, 'provider_timeout', 'The AI provider timed out.', {
          retryable: true,
        }),
        'PATCH /api/jobs/job-1': (request) => jsonResponse(savedJob(job, request)),
      },
    })
    await reviewLoaded()

    await user.click(generateButton())
    await screen.findByRole('alert')
    await user.type(screen.getByLabelText('Requirement 1 text'), ' (3 years)')
    await user.click(saveButton())
    // Saving a new version clears the failure that belonged to the old one.
    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull())
    await user.click(generateButton())

    await waitFor(() => expect(requestsTo(requests, 'POST', '/api/generations')).toHaveLength(2))
    const keys = requestsTo(requests, 'POST', '/api/generations').map(
      (request) => request.headers['Idempotency-Key'],
    )
    expect(keys[1]).not.toBe(keys[0])
  })

  it('disables generation while there are unsaved changes and says why', async () => {
    const { requests, user } = openJobPage({ jobs: [makeJob()] })
    await reviewLoaded()
    expect(generateButton()).toBeEnabled()

    await user.type(screen.getByLabelText('Requirement 1 text'), ' and SQL')

    expect(generateButton()).toBeDisabled()
    expect(generateButton()).toHaveAccessibleDescription(UNSAVED_TEXT)
    await user.click(generateButton())
    expect(requestsTo(requests, 'POST', '/api/generations')).toHaveLength(0)
  })

  it('disables generation until the profile is confirmed and indexed', async () => {
    openJobPage({
      jobs: [makeJob()],
      profile: makeProfile({ status: 'confirmed', index_state: 'indexing', indexed_version: null }),
    })
    await reviewLoaded()

    expect(screen.getByText('Your profile is still being indexed')).toBeInTheDocument()
    expect(generateButton()).toBeDisabled()
    expect(generateButton()).toHaveAccessibleDescription(
      /Generating is unavailable until your profile is confirmed/,
    )
    // Reviewing and saving the job is still possible.
    expect(screen.getByLabelText('Requirement 1 text')).toBeEnabled()
  })

  it('waits for a generation that is already in progress, checking every 3 seconds', async () => {
    // Time only moves when the test advances it, apart from tiny real-time steps.
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const statuses = ['running', 'running', 'completed'] as const
    let polls = 0
    const { requests, user } = openJobPage({
      jobs: [makeJob()],
      routes: {
        'POST /api/generations': errorResponse(
          409,
          'generation_in_progress',
          'A generation for this request is already running.',
          { details: { generation_id: 'gen-9' } },
        ),
        'GET /api/generations/gen-9': () => {
          const status = statuses[Math.min(polls, statuses.length - 1)]
          polls += 1
          return jsonResponse(makeGeneration({ generation_id: 'gen-9', status }))
        },
      },
    })
    await reviewLoaded()

    await user.click(generateButton())

    expect(await screen.findByText(/A draft is already being generated/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Generating...' })).toBeDisabled()
    await waitFor(() => expect(polls).toBe(1))

    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS)
    await waitFor(() => expect(polls).toBe(2))
    expect(screen.queryByRole('heading', { name: /Workspace/ })).toBeNull()

    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS)
    expect(await screen.findByRole('heading', { name: 'Workspace gen-9' })).toBeInTheDocument()
    expect(polls).toBe(3)
    // The generation was joined, not started a second time.
    expect(requestsTo(requests, 'POST', '/api/generations')).toHaveLength(1)
  })

  it('reports a generation that failed while it was being waited for', async () => {
    const { user } = openJobPage({
      jobs: [makeJob()],
      routes: {
        'POST /api/generations': errorResponse(409, 'generation_in_progress', 'Already running.', {
          details: { generation_id: 'gen-9' },
        }),
        'GET /api/generations/gen-9': jsonResponse(
          makeGeneration({
            generation_id: 'gen-9',
            status: 'failed',
            error: { code: 'provider_invalid_output', message: 'The AI provider returned an unusable answer.' },
          }),
        ),
      },
    })
    await reviewLoaded()

    await user.click(generateButton())

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('The AI provider returned an unusable answer.')
    expect(within(alert).getByRole('button', { name: 'Retry' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: /Workspace/ })).toBeNull()
  })

  it('links to the profile when the server says it is not confirmed', async () => {
    let profile = readyProfile()
    const { user } = openJobPage({
      jobs: [makeJob()],
      routes: {
        'GET /api/profile': () => jsonResponse(profile),
        'POST /api/generations': () => {
          // The profile was edited in the meantime; a reload shows it as a draft.
          profile = makeProfile({ status: 'draft', version: 2 })
          return errorResponse(409, 'profile_not_confirmed', 'Confirm your profile before generating.')
        },
      },
    })
    await reviewLoaded()

    await user.click(generateButton())

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Confirm your profile before generating.')
    expect(within(alert).getByRole('link', { name: 'Review and confirm profile' })).toHaveAttribute(
      'href',
      '/profile',
    )
    expect(within(alert).queryByRole('button', { name: 'Retry' })).toBeNull()
    // The page reloads the profile and now blocks generation itself.
    expect(await screen.findByText('Your profile is not confirmed yet')).toBeInTheDocument()
    expect(generateButton()).toBeDisabled()
  })

  it('shows the quota message without offering a pointless retry', async () => {
    const { user } = openJobPage({
      jobs: [makeJob()],
      routes: {
        'POST /api/generations': errorResponse(
          429,
          'quota_exceeded',
          'This session has used all 12 of its generations.',
        ),
      },
    })
    await reviewLoaded()

    await user.click(generateButton())

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('This session has used all 12 of its generations.')
    expect(within(alert).queryByRole('button', { name: 'Retry' })).toBeNull()
  })

  it('shows the rate-limit message and lets the user try again', async () => {
    const { user } = openJobPage({
      jobs: [makeJob()],
      routes: {
        'POST /api/generations': errorResponse(429, 'rate_limited', 'Too many requests. Try again in a minute.'),
      },
    })
    await reviewLoaded()

    await user.click(generateButton())

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Too many requests. Try again in a minute.')
    expect(within(alert).getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })
})

describe('JobReview drafts', () => {
  it('lists the drafts of this job with a Stale badge where it applies', async () => {
    openJobPage({
      jobs: [makeJob()],
      routes: {
        'GET /api/generations': jsonResponse({
          generations: [
            makeGenerationSummary({ generation_id: 'gen-a', created_at: '2026-10-07T13:00:00.000Z', stale: true }),
            makeGenerationSummary({ generation_id: 'gen-b', created_at: '2026-10-07T14:00:00.000Z' }),
            makeGenerationSummary({ generation_id: 'gen-c', created_at: '2026-10-07T14:30:00.000Z', status: 'failed' }),
            makeGenerationSummary({ generation_id: 'gen-other', job_id: 'another-job' }),
          ],
        }),
      },
    })
    await reviewLoaded()

    const drafts = within(screen.getByRole('region', { name: 'Drafts for this job' }))
    const links = await drafts.findAllByRole('link')
    // Newest first, and only this job's drafts.
    expect(links.map((link) => link.getAttribute('href'))).toEqual([
      '/workspace/gen-c',
      '/workspace/gen-b',
      '/workspace/gen-a',
    ])
    for (const link of links) expect(link).toHaveTextContent(/^Draft from /)

    const items = drafts.getAllByRole('listitem')
    expect(within(items[0]).getByText('Failed')).toBeInTheDocument()
    expect(within(items[1]).queryByText('Stale')).toBeNull()
    expect(within(items[2]).getByText('Stale')).toBeInTheDocument()
    expect(drafts.getByText(/A stale draft was generated before/)).toBeInTheDocument()
  })

  it('says so when the job has no drafts yet', async () => {
    openJobPage({ jobs: [makeJob()] })
    await reviewLoaded()

    expect(
      await screen.findByText('No draft has been generated for this job yet.'),
    ).toBeInTheDocument()
  })
})
