import { screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { SAMPLE_JOB } from '@/sample/sampleData'
import { makeProfile } from '@/test/fixtures'
import {
  TEST_LIMITS,
  deferred,
  errorResponse,
  jsonResponse,
  mockApi,
  sessionInfo,
} from '@/test/mockApi'
import { JOB_DESCRIPTION, makeJob, makeRequirement, sourceSpan } from './jobFixtures'
import { openJobPage, renderJobPage } from './renderJob'

const GENERATE_LABEL = 'Generate tailored resume and cover letter'

function descriptionBox() {
  return screen.getByLabelText('Job description') as HTMLTextAreaElement
}

/** Put text into the job description the way a user does: by pasting it. */
async function pasteDescription(user: ReturnType<typeof renderJobPage>['user'], text: string) {
  await user.click(descriptionBox())
  await user.paste(text)
}

describe('JobPage guards', () => {
  it('sends a visitor without a session to Start', () => {
    const requests = mockApi()
    renderJobPage()

    expect(screen.getByRole('heading', { name: 'No profile yet' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Go to Start' })).toHaveAttribute('href', '/')
    expect(requests.some((request) => request.path.startsWith('/api/jobs'))).toBe(false)
  })

  it('asks for a profile first when the session has none', async () => {
    openJobPage({ profile: null })

    expect(await screen.findByRole('heading', { name: 'No profile yet' })).toBeInTheDocument()
    expect(screen.queryByLabelText('Job description')).toBeNull()
  })

  it('shows a retryable error when the profile cannot be loaded', async () => {
    let attempts = 0
    const { user } = openJobPage({
      routes: {
        'GET /api/profile': () => {
          attempts += 1
          return attempts === 1
            ? errorResponse(503, 'database_unavailable', 'The database is unavailable.')
            : jsonResponse(makeProfile())
        },
      },
    })

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Your profile could not be loaded')
    await user.click(within(alert).getByRole('button', { name: 'Retry' }))

    expect(await screen.findByRole('heading', { name: 'Target job' })).toBeInTheDocument()
  })

  it('explains that an unconfirmed profile blocks generation but not job analysis', async () => {
    const job = makeJob()
    const { user } = openJobPage({
      profile: makeProfile({ status: 'draft' }),
      routes: {
        'POST /api/jobs': jsonResponse(job, 201),
        'GET /api/jobs/job-1': jsonResponse(job),
      },
    })

    expect(await screen.findByText('Your profile is not confirmed yet')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Review and confirm profile' })).toHaveAttribute(
      'href',
      '/profile',
    )

    // Job analysis still works.
    await pasteDescription(user, JOB_DESCRIPTION)
    await user.click(screen.getByRole('button', { name: 'Analyze job' }))
    expect(
      await screen.findByRole('heading', { name: 'Review the job requirements' }),
    ).toBeInTheDocument()

    // Generation is disabled, and the reason is written next to the button.
    const generate = screen.getByRole('button', { name: GENERATE_LABEL })
    expect(generate).toBeDisabled()
    expect(generate).toHaveAccessibleDescription(
      /Generating is unavailable until your profile is confirmed/,
    )
    expect(screen.getByRole('link', { name: 'Open profile' })).toHaveAttribute('href', '/profile')
  })
})

describe('JobPage form', () => {
  it('shows an empty form with the provider note when the session has no job yet', async () => {
    const { requests } = openJobPage()

    expect(await screen.findByRole('heading', { name: 'Target job' })).toBeInTheDocument()
    expect(screen.getByLabelText('Role title (optional)')).toHaveValue('')
    expect(screen.getByLabelText('Company (optional)')).toHaveValue('')
    expect(descriptionBox()).toHaveValue('')
    expect(screen.getByText('0 / 25,000 characters')).toBeInTheDocument()
    expect(descriptionBox()).toHaveAccessibleDescription(
      /sent to the configured AI provider \(OpenAI\).*stored with your session for at most 24 hours/,
    )
    expect(requests.some((request) => request.method === 'POST')).toBe(false)
  })

  it('says the job stays on the server in demo mode', async () => {
    openJobPage({
      routes: { 'GET /api/session': jsonResponse(sessionInfo({ provider_mode: 'fake', has_profile: true })) },
    })

    await screen.findByRole('heading', { name: 'Target job' })
    await waitFor(() =>
      expect(descriptionBox()).toHaveAccessibleDescription(/not sent to an AI provider/),
    )
  })

  it('asks for a description when the form is submitted empty', async () => {
    const { requests, user } = openJobPage()

    await user.click(await screen.findByRole('button', { name: 'Analyze job' }))

    expect(
      await screen.findByText('Paste the job description so its requirements can be extracted.'),
    ).toBeInTheDocument()
    expect(descriptionBox()).toBeInvalid()
    expect(requests.some((request) => request.method === 'POST')).toBe(false)
  })

  it('rejects a description over the limit with a clear message and a live counter', async () => {
    const { requests, user } = openJobPage({
      routes: {
        'GET /api/session': jsonResponse(
          sessionInfo({ has_profile: true, limits: { ...TEST_LIMITS, max_job_chars: 20 } }),
        ),
      },
    })
    await screen.findByText('0 / 20 characters')

    await pasteDescription(user, 'This sentence is longer than twenty characters.')
    expect(screen.getByText('47 / 20 characters')).toBeInTheDocument()
    expect(screen.getByText('(27 over the limit)')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Analyze job' }))

    expect(
      await screen.findByText(/The job description is longer than the 20 character limit/),
    ).toBeInTheDocument()
    expect(requests.some((request) => request.method === 'POST')).toBe(false)
  })

  it('rejects a role title that is too long', async () => {
    const { requests, user } = openJobPage()

    await user.click(await screen.findByLabelText('Role title (optional)'))
    await user.paste('x'.repeat(201))
    await pasteDescription(user, JOB_DESCRIPTION)
    await user.click(screen.getByRole('button', { name: 'Analyze job' }))

    expect(
      await screen.findByText('Keep the role title to 200 characters or fewer.'),
    ).toBeInTheDocument()
    expect(requests.some((request) => request.method === 'POST')).toBe(false)
  })

  it('fills the form with the fictional sample job', async () => {
    const { user } = openJobPage()

    await user.click(await screen.findByRole('button', { name: 'Use sample job' }))

    expect(screen.getByLabelText('Role title (optional)')).toHaveValue(SAMPLE_JOB.title)
    expect(screen.getByLabelText('Company (optional)')).toHaveValue(SAMPLE_JOB.company)
    expect(descriptionBox()).toHaveValue(SAMPLE_JOB.description)
    expect(screen.getByText(/Fictional sample job loaded/)).toBeInTheDocument()
  })

  it('analyzes the job and lists its requirements grouped by importance', async () => {
    const job = makeJob()
    const response = deferred<Response>()
    const { requests, user } = openJobPage({
      routes: {
        'POST /api/jobs': () => response.promise,
        'GET /api/jobs/job-1': jsonResponse(job),
      },
    })

    await user.type(await screen.findByLabelText('Role title (optional)'), '  ML Engineer ')
    await pasteDescription(user, JOB_DESCRIPTION)
    vi.mocked(window.scrollTo).mockClear()
    await user.click(screen.getByRole('button', { name: 'Analyze job' }))

    // While the request runs: an honest label and no second submission.
    expect(await screen.findByText('Analyzing job description...')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Analyzing...' })).toBeDisabled()
    expect(descriptionBox()).toBeDisabled()

    response.resolve(jsonResponse(job, 201))
    expect(
      await screen.findByRole('heading', { name: 'Review the job requirements' }),
    ).toBeInTheDocument()
    // The review replaces the form in place, so the page returns to its top.
    expect(window.scrollTo).toHaveBeenCalledWith(0, 0)

    const posts = requests.filter((request) => request.method === 'POST')
    expect(posts).toHaveLength(1)
    expect(posts[0].path).toBe('/api/jobs')
    // The description is sent untouched; blank optional fields are sent as null.
    expect(posts[0].body).toEqual({
      description: JOB_DESCRIPTION,
      title: 'ML Engineer',
      company: null,
    })

    const required = within(screen.getByRole('region', { name: 'Required (2)' }))
    expect(required.getByLabelText('Requirement 1 text')).toHaveValue('Strong Python skills')
    expect(required.getByLabelText('Requirement 2 text')).toHaveValue('Experience with Docker')
    expect(required.queryByText('Inferred')).toBeNull()

    const preferred = within(screen.getByRole('region', { name: 'Preferred (2)' }))
    expect(preferred.getByLabelText('Requirement 3 text')).toHaveValue('Experience with AWS')
    expect(preferred.getByLabelText('Requirement 4 text')).toHaveValue(
      'Comfortable working with support teams',
    )
    // Only the requirement that is not stated in the posting is marked.
    const inferredRow = within(preferred.getByRole('group', { name: 'Requirement 4' }))
    expect(inferredRow.getByRole('button', { name: 'Inferred' })).toBeInTheDocument()
    expect(preferred.getAllByText('Inferred')).toHaveLength(1)

    expect(screen.getByText('4 / 25 requirements')).toBeInTheDocument()
    expect(screen.getByLabelText('Role title')).toHaveValue('Applied Machine Learning Engineer')
    expect(screen.getByLabelText('Company')).toHaveValue('Fernhollow AI')
  })

  it('keeps the input and offers Retry when the analysis fails', async () => {
    const job = makeJob()
    let attempts = 0
    const { requests, user } = openJobPage({
      routes: {
        'POST /api/jobs': () => {
          attempts += 1
          return attempts === 1
            ? errorResponse(504, 'provider_timeout', 'The AI provider took too long to answer.', {
                retryable: true,
              })
            : jsonResponse(job, 201)
        },
        'GET /api/jobs/job-1': jsonResponse(job),
      },
    })

    await user.type(await screen.findByLabelText('Company (optional)'), 'Fernhollow AI')
    await pasteDescription(user, JOB_DESCRIPTION)
    await user.click(screen.getByRole('button', { name: 'Analyze job' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('The job description was not analyzed')
    expect(alert).toHaveTextContent('The AI provider took too long to answer.')
    expect(alert).toHaveTextContent('req-test-123')
    // Nothing the user entered was lost.
    expect(descriptionBox()).toHaveValue(JOB_DESCRIPTION)
    expect(screen.getByLabelText('Company (optional)')).toHaveValue('Fernhollow AI')

    await user.click(within(alert).getByRole('button', { name: 'Retry' }))

    expect(
      await screen.findByRole('heading', { name: 'Review the job requirements' }),
    ).toBeInTheDocument()
    const posts = requests.filter((request) => request.method === 'POST')
    expect(posts).toHaveLength(2)
    expect(posts[1].body).toEqual(posts[0].body)
  })

  it('shows a field error from the API next to the field it belongs to', async () => {
    const { user } = openJobPage({
      routes: {
        'POST /api/jobs': errorResponse(422, 'validation_error', 'The request is not valid.', {
          field_errors: [{ field: 'body.description', message: 'Job description must not be empty' }],
        }),
      },
    })

    await screen.findByRole('heading', { name: 'Target job' })
    await pasteDescription(user, 'x')
    await user.click(screen.getByRole('button', { name: 'Analyze job' }))

    await waitFor(() => expect(descriptionBox()).toBeInvalid())
    expect(descriptionBox()).toHaveAccessibleDescription(/Job description must not be empty/)
  })
})

describe('JobPage with existing jobs', () => {
  const olderJob = makeJob({
    job_id: 'job-old',
    title: 'Data Analyst',
    company: 'Quillmere Labs',
    created_at: '2026-10-07T09:00:00.000Z',
    requirements: [makeRequirement({ requirement_id: 'old-1', text: 'Working knowledge of SQL' })],
  })
  const newerJob = makeJob({ job_id: 'job-new', created_at: '2026-10-07T15:00:00.000Z' })

  it('opens the most recent job on load, so a refresh keeps the place', async () => {
    // Listed oldest first on purpose: the page must pick by creation time.
    const { requests } = openJobPage({ jobs: [olderJob, newerJob] })

    expect(
      await screen.findByRole('heading', { name: 'Review the job requirements' }),
    ).toBeInTheDocument()
    expect(screen.getByLabelText('Role title')).toHaveValue('Applied Machine Learning Engineer')
    expect(screen.getByLabelText('Requirement 1 text')).toHaveValue('Strong Python skills')
    expect(requests.some((request) => request.path === '/api/jobs/job-new')).toBe(true)
    expect(requests.some((request) => request.path === '/api/jobs/job-old')).toBe(false)
  })

  it('returns to the empty form with "Start a different job" and can go back', async () => {
    const { user } = openJobPage({ jobs: [olderJob, newerJob] })

    await user.click(await screen.findByRole('button', { name: 'Start a different job' }))

    expect(await screen.findByRole('heading', { name: 'Target job' })).toBeInTheDocument()
    expect(descriptionBox()).toHaveValue('')
    expect(screen.getByLabelText('Role title (optional)')).toHaveValue('')

    const earlier = within(screen.getByRole('region', { name: 'Jobs you already analyzed' }))
    expect(
      earlier.getByRole('link', {
        name: 'Review Applied Machine Learning Engineer at Fernhollow AI',
      }),
    ).toHaveAttribute('href', '/job?job=job-new')
    expect(earlier.getByText(/^1 requirement, analyzed/)).toBeInTheDocument()

    await user.click(earlier.getByRole('link', { name: 'Review Data Analyst at Quillmere Labs' }))

    expect(
      await screen.findByRole('heading', { name: 'Review the job requirements' }),
    ).toBeInTheDocument()
    expect(screen.getByLabelText('Requirement 1 text')).toHaveValue('Working knowledge of SQL')
  })

  it('opens the form directly at /job?new=1 and a given job at /job?job=<id>', async () => {
    const first = openJobPage({ jobs: [olderJob, newerJob], route: '/job?new=1' })
    expect(await screen.findByRole('heading', { name: 'Target job' })).toBeInTheDocument()
    first.unmount()

    openJobPage({ jobs: [olderJob, newerJob], route: '/job?job=job-old' })
    expect(await screen.findByLabelText('Role title')).toHaveValue('Data Analyst')
  })

  it('says so when the job in the address does not exist', async () => {
    openJobPage({ jobs: [newerJob], route: '/job?job=missing' })

    expect(
      await screen.findByRole('heading', { name: 'This job is not available' }),
    ).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Open my latest job' })).toHaveAttribute('href', '/job')
  })

  it('shows a retryable error when the job list cannot be loaded', async () => {
    let attempts = 0
    const { user } = openJobPage({
      routes: {
        'GET /api/jobs': () => {
          attempts += 1
          return attempts === 1
            ? errorResponse(503, 'database_unavailable', 'The database is unavailable.')
            : jsonResponse({ jobs: [] })
        },
      },
    })

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Your jobs could not be loaded')
    await user.click(within(alert).getByRole('button', { name: 'Retry' }))

    expect(await screen.findByRole('heading', { name: 'Target job' })).toBeInTheDocument()
  })
})

describe('JobPage untrusted text', () => {
  it('renders HTML in the job text as plain text, never as markup', async () => {
    const description =
      'We need <b>bold</b> people.\n<img src="x" onerror="window.__jobXss = 1">\n<script>window.__jobXss = 2</script>'
    const job = makeJob({
      title: '<u>Engineer</u>',
      description,
      role_summary: 'A role for <i>careful</i> people <script>window.__jobXss = 3</script>',
      requirements: [
        makeRequirement({
          requirement_id: 'req-x',
          text: 'Knows <b>HTML</b> & <script>alert(1)</script>',
          source_span: sourceSpan('<img src="x" onerror="window.__jobXss = 1">', description),
        }),
      ],
    })
    const { user } = openJobPage({ jobs: [job] })

    expect(await screen.findByLabelText('Requirement 1 text')).toHaveValue(
      'Knows <b>HTML</b> & <script>alert(1)</script>',
    )
    expect(screen.getByLabelText('Role title')).toHaveValue('<u>Engineer</u>')
    expect(
      screen.getByText('A role for <i>careful</i> people <script>window.__jobXss = 3</script>'),
    ).toBeInTheDocument()

    // The full posting, behind its disclosure.
    await user.click(screen.getByRole('button', { name: /Job description as pasted/ }))
    const posting = await screen.findByRole('region', { name: 'Job description text' })
    expect(posting.textContent).toBe(description)

    // The supporting excerpt of the requirement.
    await user.click(screen.getByRole('button', { name: 'View source of requirement 1' }))
    expect(
      await screen.findByText('<img src="x" onerror="window.__jobXss = 1">'),
    ).toBeInTheDocument()
    expect(screen.getByText('Source: Job description')).toBeInTheDocument()

    // None of it became an element, and nothing ran.
    expect(document.body.querySelector('img, script, b, i, u, iframe')).toBeNull()
    expect('__jobXss' in window).toBe(false)
  })
})
