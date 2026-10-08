import { screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { SAMPLE_JOB } from '@/sample/sampleData'
import { makeProfile } from '@/test/fixtures'
import {
  TEST_LIMITS,
  deferred,
  errorResponse,
  jsonResponse,
  mockApi,
  sessionInfo,
  type RecordedRequest,
} from '@/test/mockApi'
import { JOB_DESCRIPTION, makeGeneration, makeGenerationSummary, makeJob } from './jobFixtures'
import { openJobPage, renderJobPage } from './renderJob'

const SUBMIT_LABEL = 'Tailor my resume'

function descriptionBox() {
  return screen.getByLabelText('Job description') as HTMLTextAreaElement
}

/** Put text into the job description the way a user does: by pasting it. */
async function pasteDescription(user: ReturnType<typeof renderJobPage>['user'], text: string) {
  await user.click(descriptionBox())
  await user.paste(text)
}

function posts(requests: RecordedRequest[], path: string) {
  return requests.filter((request) => request.method === 'POST' && request.path === path)
}

/** The text of each step in the progress list, e.g. "1. Analyzing the job description In progress". */
function progressSteps(): string[] {
  const progress = screen.getByRole('status', { name: 'Progress' })
  return within(progress)
    .queryAllByRole('listitem')
    .map((item) => [...item.querySelectorAll('span')].map((span) => span.textContent?.trim()).join(' '))
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

  it('explains an unconfirmed profile and does not offer to tailor', async () => {
    const { requests, user } = openJobPage({ profile: makeProfile({ status: 'draft' }) })

    expect(await screen.findByText('Your profile is not confirmed yet')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Review and confirm profile' })).toHaveAttribute(
      'href',
      '/profile',
    )

    // The job would be analyzed for nothing, so the whole run is unavailable.
    const submit = screen.getByRole('button', { name: SUBMIT_LABEL })
    expect(submit).toBeDisabled()
    expect(submit).toHaveAccessibleDescription(
      'Tailoring is unavailable until your profile is confirmed and indexed.',
    )
    // Not even with Enter, which submits a form whatever its button says.
    await pasteDescription(user, JOB_DESCRIPTION)
    await user.type(screen.getByLabelText('Company (optional)'), 'Fernhollow AI{Enter}')
    expect(requests.some((request) => request.method === 'POST')).toBe(false)
  })

  it('says so while the profile is still being indexed', async () => {
    openJobPage({
      profile: makeProfile({ status: 'confirmed', index_state: 'indexing', indexed_version: null }),
    })

    expect(await screen.findByText('Your profile is still being indexed')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: SUBMIT_LABEL })).toBeDisabled()
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

    await user.click(await screen.findByRole('button', { name: SUBMIT_LABEL }))

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
    await user.click(screen.getByRole('button', { name: SUBMIT_LABEL }))

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
    await user.click(screen.getByRole('button', { name: SUBMIT_LABEL }))

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

  it('analyzes the job, writes the draft and opens the workspace in one run', async () => {
    const job = makeJob()
    const analysis = deferred<Response>()
    const generation = deferred<Response>()
    const { requests, user } = openJobPage({
      routes: {
        'POST /api/jobs': () => analysis.promise,
        'GET /api/jobs/job-1': jsonResponse(job),
        'POST /api/generations': () => generation.promise,
      },
    })

    await user.type(await screen.findByLabelText('Role title (optional)'), '  ML Engineer ')
    await pasteDescription(user, JOB_DESCRIPTION)
    expect(progressSteps()).toEqual([])
    await user.click(screen.getByRole('button', { name: SUBMIT_LABEL }))

    // Step 1 runs: honest step statuses, and nothing can be submitted twice.
    await waitFor(() =>
      expect(progressSteps()).toEqual([
        '1. Analyzing the job description In progress',
        '2. Writing your resume and cover letter Waiting',
      ]),
    )
    expect(screen.getByRole('status', { name: 'Progress' })).toHaveTextContent(
      'This usually takes about a minute.',
    )
    expect(screen.getByRole('button', { name: 'Tailoring...' })).toBeDisabled()
    expect(descriptionBox()).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Use sample job' })).toBeDisabled()
    expect(posts(requests, '/api/generations')).toHaveLength(0)

    // Step 2 starts by itself once the job is stored.
    analysis.resolve(jsonResponse(job, 201))
    expect(await screen.findByRole('heading', { name: 'Your target job' })).toBeInTheDocument()
    await waitFor(() =>
      expect(progressSteps()).toEqual([
        '1. Analyzing the job description Done',
        '2. Writing your resume and cover letter In progress',
      ]),
    )
    expect(screen.getByRole('button', { name: 'Generating...' })).toBeDisabled()

    generation.resolve(jsonResponse(makeGeneration({ generation_id: 'gen-42' }), 201))
    expect(await screen.findByRole('heading', { name: 'Workspace gen-42' })).toBeInTheDocument()

    const sent = requests.filter((request) => request.method === 'POST')
    expect(sent.map((request) => request.path)).toEqual(['/api/jobs', '/api/generations'])
    // The description is sent untouched; blank optional fields are sent as null.
    expect(sent[0].body).toEqual({
      description: JOB_DESCRIPTION,
      title: 'ML Engineer',
      company: null,
    })
    expect(sent[1].body).toEqual({ job_id: 'job-1' })
    expect(sent[1].headers['Idempotency-Key']).toMatch(/^[0-9a-f-]{36}$/)
  })

  it('keeps the input when the analysis fails, and Retry runs both steps', async () => {
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
        'POST /api/generations': jsonResponse(makeGeneration({ generation_id: 'gen-5' }), 201),
      },
    })

    await user.type(await screen.findByLabelText('Company (optional)'), 'Fernhollow AI')
    await pasteDescription(user, JOB_DESCRIPTION)
    await user.click(screen.getByRole('button', { name: SUBMIT_LABEL }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('The job description was not analyzed')
    expect(alert).toHaveTextContent('The AI provider took too long to answer.')
    expect(alert).toHaveTextContent('req-test-123')
    expect(progressSteps()).toEqual([
      '1. Analyzing the job description Failed',
      '2. Writing your resume and cover letter Waiting',
    ])
    // Nothing the user entered was lost, and no draft was attempted.
    expect(descriptionBox()).toHaveValue(JOB_DESCRIPTION)
    expect(screen.getByLabelText('Company (optional)')).toHaveValue('Fernhollow AI')
    expect(screen.getByRole('button', { name: SUBMIT_LABEL })).toBeEnabled()
    expect(posts(requests, '/api/generations')).toHaveLength(0)

    await user.click(within(alert).getByRole('button', { name: 'Retry' }))

    expect(await screen.findByRole('heading', { name: 'Workspace gen-5' })).toBeInTheDocument()
    const analyses = posts(requests, '/api/jobs')
    expect(analyses).toHaveLength(2)
    expect(analyses[1].body).toEqual(analyses[0].body)
    expect(posts(requests, '/api/generations')).toHaveLength(1)
  })

  it('retries only the generation, with the same key, when writing the draft fails', async () => {
    const job = makeJob()
    let attempts = 0
    const { requests, user } = openJobPage({
      routes: {
        'POST /api/jobs': jsonResponse(job, 201),
        'GET /api/jobs/job-1': jsonResponse(job),
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

    await screen.findByRole('heading', { name: 'Target job' })
    await pasteDescription(user, JOB_DESCRIPTION)
    await user.click(screen.getByRole('button', { name: SUBMIT_LABEL }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('The draft was not generated')
    expect(alert).toHaveTextContent('The AI provider is unavailable.')
    expect(progressSteps()).toEqual([
      '1. Analyzing the job description Done',
      '2. Writing your resume and cover letter Failed',
    ])
    // The analyzed job is kept and shown; it is not paid for a second time.
    expect(screen.getByText('Applied Machine Learning Engineer at Fernhollow AI', { exact: false })).toBeInTheDocument()

    await user.click(within(alert).getByRole('button', { name: 'Retry' }))

    expect(await screen.findByRole('heading', { name: 'Workspace gen-7' })).toBeInTheDocument()
    expect(posts(requests, '/api/jobs')).toHaveLength(1)
    const keys = posts(requests, '/api/generations').map((request) => request.headers['Idempotency-Key'])
    expect(keys).toHaveLength(2)
    expect(keys[0]).toBeTruthy()
    expect(keys[1]).toBe(keys[0])
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
    await user.click(screen.getByRole('button', { name: SUBMIT_LABEL }))

    await waitFor(() => expect(descriptionBox()).toBeInvalid())
    expect(descriptionBox()).toHaveAccessibleDescription(/Job description must not be empty/)
  })
})

describe('JobPage drafts under the form', () => {
  it('lists every draft of the session with its job, newest first', async () => {
    openJobPage({
      routes: {
        'GET /api/generations': jsonResponse({
          generations: [
            makeGenerationSummary({ generation_id: 'gen-a', created_at: '2026-10-07T13:00:00.000Z', stale: true }),
            makeGenerationSummary({
              generation_id: 'gen-b',
              job_id: 'job-2',
              job_title: 'Data Analyst',
              company: 'Quillmere Labs',
              created_at: '2026-10-07T14:00:00.000Z',
            }),
          ],
        }),
      },
    })

    const drafts = within(await screen.findByRole('region', { name: 'Your drafts' }))
    const links = await drafts.findAllByRole('link')
    expect(links.map((link) => link.getAttribute('href'))).toEqual([
      '/workspace/gen-b',
      '/workspace/gen-a',
    ])
    const items = drafts.getAllByRole('listitem')
    expect(within(items[0]).getByText('Data Analyst at Quillmere Labs')).toBeInTheDocument()
    expect(within(items[1]).getByText('Applied Machine Learning Engineer at Fernhollow AI')).toBeInTheDocument()
    expect(within(items[1]).getByText('Stale')).toBeInTheDocument()
  })

  it('says so when nothing has been generated yet', async () => {
    openJobPage()

    expect(await screen.findByText('No draft has been generated yet.')).toBeInTheDocument()
  })

  it('always opens the form at /job, even when the session already has jobs', async () => {
    const { requests } = openJobPage({ jobs: [makeJob()] })

    expect(await screen.findByRole('heading', { name: 'Target job' })).toBeInTheDocument()
    expect(descriptionBox()).toHaveValue('')
    expect(requests.some((request) => request.path.startsWith('/api/jobs'))).toBe(false)
  })
})
