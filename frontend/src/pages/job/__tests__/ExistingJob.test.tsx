import { screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { makeProfile } from '@/test/fixtures'
import { deferred, errorResponse, jsonResponse, type RecordedRequest } from '@/test/mockApi'
import { POLL_INTERVAL_MS } from '../generateDraft'
import { makeGeneration, makeGenerationSummary, makeJob, readyProfile } from './jobFixtures'
import { openJobPage } from './renderJob'

/**
 * The Target job screen for a job that is already analyzed (/job?job=<id>):
 * the workspace links here to generate again, and the combined run continues
 * here after its analysis.
 */

const JOB_ROUTE = '/job?job=job-1'

afterEach(() => {
  vi.useRealTimers()
})

function generateButton() {
  return screen.getByRole('button', { name: 'Generate a new draft' })
}

async function jobLoaded() {
  await screen.findByRole('heading', { name: 'Your target job' })
}

function requestsTo(requests: RecordedRequest[], method: string, path: string) {
  return requests.filter((request) => request.method === method && request.path === path)
}

describe('ExistingJob', () => {
  it('shows the stored job read-only with a link to start a different one', async () => {
    const { requests } = openJobPage({ jobs: [makeJob()], route: JOB_ROUTE })
    await jobLoaded()

    expect(screen.getByText(/Applied Machine Learning Engineer at Fernhollow AI\./)).toBeInTheDocument()
    const details = within(screen.getByRole('region', { name: 'Job details' }))
    expect(details.getByText('Applied Machine Learning Engineer')).toBeInTheDocument()
    expect(details.getByText('Fernhollow AI')).toBeInTheDocument()
    expect(details.getByText('Builds retrieval features for support teams.')).toBeInTheDocument()
    // Nothing about the job can be edited here.
    expect(screen.queryAllByRole('textbox')).toHaveLength(0)
    expect(screen.queryByRole('button', { name: /Save/ })).toBeNull()
    expect(screen.getByRole('link', { name: 'Start a different job' })).toHaveAttribute('href', '/job')
    expect(generateButton()).toBeEnabled()
    expect(requests.some((request) => request.method === 'POST')).toBe(false)
  })

  it('generates a new draft without analyzing the job again', async () => {
    const response = deferred<Response>()
    const { requests, user } = openJobPage({
      jobs: [makeJob()],
      route: JOB_ROUTE,
      routes: { 'POST /api/generations': () => response.promise },
    })
    await jobLoaded()

    await user.click(generateButton())

    // The same progress display as the combined run, with step 1 already done.
    const progress = within(screen.getByRole('status', { name: 'Progress' }))
    const steps = await progress.findAllByRole('listitem')
    expect(steps[0]).toHaveTextContent(/1\. Analyzing the job description\s*Done/)
    expect(steps[1]).toHaveTextContent(/2\. Writing your resume and cover letter\s*In progress/)
    expect(progress.getByText(/This usually takes about a minute/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Generating...' })).toBeDisabled()

    response.resolve(jsonResponse(makeGeneration({ generation_id: 'gen-42' }), 201))

    expect(await screen.findByRole('heading', { name: 'Workspace gen-42' })).toBeInTheDocument()
    expect(requestsTo(requests, 'POST', '/api/jobs')).toHaveLength(0)
    const posts = requestsTo(requests, 'POST', '/api/generations')
    expect(posts).toHaveLength(1)
    expect(posts[0].body).toEqual({ job_id: 'job-1' })
    expect(posts[0].headers['Idempotency-Key']).toMatch(/^[0-9a-f-]{36}$/)
  })

  it('disables generation until the profile is confirmed and indexed', async () => {
    openJobPage({
      jobs: [makeJob()],
      route: JOB_ROUTE,
      profile: makeProfile({ status: 'confirmed', index_state: 'indexing', indexed_version: null }),
    })
    await jobLoaded()

    expect(screen.getByText('Your profile is still being indexed')).toBeInTheDocument()
    expect(generateButton()).toBeDisabled()
    expect(generateButton()).toHaveAccessibleDescription(
      /Generating is unavailable until your profile is confirmed/,
    )
  })

  it('waits for a generation that is already in progress, checking every 3 seconds', async () => {
    // Time only moves when the test advances it, apart from tiny real-time steps.
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const statuses = ['running', 'running', 'completed'] as const
    let polls = 0
    const { requests, user } = openJobPage({
      jobs: [makeJob()],
      route: JOB_ROUTE,
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
    await jobLoaded()

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
      route: JOB_ROUTE,
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
    await jobLoaded()

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
      route: JOB_ROUTE,
      routes: {
        'GET /api/profile': () => jsonResponse(profile),
        'POST /api/generations': () => {
          // The profile was edited in the meantime; a reload shows it as a draft.
          profile = makeProfile({ status: 'draft', version: 2 })
          return errorResponse(409, 'profile_not_confirmed', 'Confirm your profile before generating.')
        },
      },
    })
    await jobLoaded()

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
      route: JOB_ROUTE,
      routes: {
        'POST /api/generations': errorResponse(
          429,
          'quota_exceeded',
          'This session has used all 12 of its generations.',
        ),
      },
    })
    await jobLoaded()

    await user.click(generateButton())

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('This session has used all 12 of its generations.')
    expect(within(alert).queryByRole('button', { name: 'Retry' })).toBeNull()
  })

  it('shows the rate-limit message and retries with the same key', async () => {
    let attempts = 0
    const { requests, user } = openJobPage({
      jobs: [makeJob()],
      route: JOB_ROUTE,
      routes: {
        'POST /api/generations': () => {
          attempts += 1
          return attempts === 1
            ? errorResponse(429, 'rate_limited', 'Too many requests. Try again in a minute.')
            : jsonResponse(makeGeneration({ generation_id: 'gen-3' }), 201)
        },
      },
    })
    await jobLoaded()

    await user.click(generateButton())

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Too many requests. Try again in a minute.')
    await user.click(within(alert).getByRole('button', { name: 'Retry' }))

    expect(await screen.findByRole('heading', { name: 'Workspace gen-3' })).toBeInTheDocument()
    const keys = requestsTo(requests, 'POST', '/api/generations').map(
      (request) => request.headers['Idempotency-Key'],
    )
    expect(keys).toHaveLength(2)
    expect(keys[1]).toBe(keys[0])
  })

  it('says so when the job in the address does not exist', async () => {
    openJobPage({ jobs: [makeJob()], route: '/job?job=missing' })

    expect(
      await screen.findByRole('heading', { name: 'This job is not available' }),
    ).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Tailor for a new job' })).toHaveAttribute('href', '/job')
  })

  it('shows a retryable error when the job cannot be loaded', async () => {
    let attempts = 0
    const { user } = openJobPage({
      route: JOB_ROUTE,
      routes: {
        'GET /api/jobs/job-1': () => {
          attempts += 1
          return attempts === 1
            ? errorResponse(503, 'database_unavailable', 'The database is unavailable.')
            : jsonResponse(makeJob())
        },
      },
    })

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('The job could not be loaded')
    await user.click(within(alert).getByRole('button', { name: 'Retry' }))

    await jobLoaded()
  })

  it('lists the drafts of this job with a Stale badge where it applies', async () => {
    openJobPage({
      jobs: [makeJob()],
      route: JOB_ROUTE,
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
    await jobLoaded()

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
    openJobPage({ jobs: [makeJob()], route: JOB_ROUTE })
    await jobLoaded()

    expect(
      await screen.findByText('No draft has been generated for this job yet.'),
    ).toBeInTheDocument()
  })

  it('renders HTML in the job text as plain text, never as markup', async () => {
    const description =
      'We need <b>bold</b> people.\n<img src="x" onerror="window.__jobXss = 1">\n<script>window.__jobXss = 2</script>'
    const job = makeJob({
      title: '<u>Engineer</u>',
      company: '<i>Fernhollow</i>',
      description,
      role_summary: 'A role for <i>careful</i> people <script>window.__jobXss = 3</script>',
    })
    const { user } = openJobPage({ jobs: [job], route: JOB_ROUTE })
    await jobLoaded()

    const details = within(screen.getByRole('region', { name: 'Job details' }))
    expect(details.getByText('<u>Engineer</u>')).toBeInTheDocument()
    expect(details.getByText('<i>Fernhollow</i>')).toBeInTheDocument()
    expect(
      details.getByText('A role for <i>careful</i> people <script>window.__jobXss = 3</script>'),
    ).toBeInTheDocument()

    // The full posting, behind its disclosure.
    await user.click(screen.getByRole('button', { name: /Job description as pasted/ }))
    const posting = await screen.findByRole('region', { name: 'Job description text' })
    expect(posting.textContent).toBe(description)

    // None of it became an element, and nothing ran.
    expect(document.body.querySelector('img, script, b, i, u, iframe')).toBeNull()
    expect('__jobXss' in window).toBe(false)
  })
})
