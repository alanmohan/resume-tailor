import { screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { getToken } from '@/lib/session'
import { SAMPLE_SOURCES } from '@/sample/sampleData'
import { makeProfile } from '@/test/fixtures'
import {
  TEST_LIMITS,
  TEST_TOKEN,
  deferred,
  errorResponse,
  futureIso,
  jsonResponse,
  mockApi,
  seedSession,
  sessionInfo,
} from '@/test/mockApi'
import { renderApp } from '@/test/render'

const SESSION_CREATED = jsonResponse(
  { token: TEST_TOKEN, expires_at: futureIso(), provider_mode: 'openai', limits: TEST_LIMITS },
  201,
)

function resumeBox() {
  return screen.getByLabelText('Resume/CV text') as HTMLTextAreaElement
}

async function fillAndAcknowledge(user: ReturnType<typeof renderApp>['user'], text: string) {
  await user.type(resumeBox(), text)
  await user.click(screen.getByLabelText('I have read how my data is sent, stored and deleted.'))
}

describe('StartPage', () => {
  it('shows the privacy notice and three labelled paste areas before anything is sent', () => {
    const requests = mockApi()
    renderApp('/')

    const notice = within(screen.getByRole('region', { name: 'How your data is handled' }))
    expect(notice.getByText(/sent to the configured AI provider \(OpenAI\)/)).toBeInTheDocument()
    expect(notice.getByText(/for at most 24 hours/)).toBeInTheDocument()
    expect(notice.getByText(/tied to this browser tab/)).toBeInTheDocument()
    expect(notice.getByText(/deletes\s+everything immediately/)).toBeInTheDocument()

    expect(screen.getByLabelText('Resume/CV text')).toBeInTheDocument()
    expect(screen.getByLabelText('LinkedIn profile text')).toBeInTheDocument()
    expect(screen.getByLabelText('Background notes text')).toBeInTheDocument()
    expect(screen.getByLabelText('Resume/CV source label')).toHaveValue('Resume')
    expect(screen.getByText('0 / 60,000 characters in total')).toBeInTheDocument()
    // Nothing the user typed has gone anywhere, and no session exists yet.
    expect(requests.every((request) => request.method === 'GET')).toBe(true)
    expect(getToken()).toBeNull()
  })

  it('explains what is missing when the form is submitted empty', async () => {
    const requests = mockApi()
    const { user } = renderApp('/')

    await user.click(screen.getByRole('button', { name: 'Extract my profile' }))

    expect(
      await screen.findByText('Paste your resume, LinkedIn profile or notes into at least one box.'),
    ).toBeInTheDocument()
    expect(
      screen.getByText('Confirm that you have read how your data is handled.'),
    ).toBeInTheDocument()
    expect(requests.some((request) => request.method === 'POST')).toBe(false)
  })

  it('rejects text over the limit with a clear message and a live counter', async () => {
    seedSession()
    const requests = mockApi({
      'GET /api/session': jsonResponse(
        sessionInfo({ limits: { ...TEST_LIMITS, max_profile_chars: 20 } }),
      ),
    })
    const { user } = renderApp('/')
    await screen.findByText('0 / 20 characters in total')

    await fillAndAcknowledge(user, 'This sentence is longer than twenty characters.')

    expect(screen.getByText('47 / 20 characters in total')).toBeInTheDocument()
    expect(screen.getByText('(27 over the limit)')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Extract my profile' }))

    expect(
      await screen.findByText(/Your text is longer than the 20 character limit/),
    ).toBeInTheDocument()
    expect(requests.some((request) => request.method === 'POST')).toBe(false)
  })

  it('fills the fictional sample into the matching boxes', async () => {
    mockApi()
    const { user } = renderApp('/')

    await user.click(screen.getByRole('button', { name: 'Try sample profile' }))

    const sample = (type: string) => SAMPLE_SOURCES.find((source) => source.source_type === type)!
    expect(resumeBox().value).toBe(sample('resume').text)
    expect(screen.getByLabelText('LinkedIn profile text')).toHaveValue(sample('linkedin').text)
    expect(screen.getByLabelText('Background notes text')).toHaveValue(sample('notes').text)
    expect(screen.getByLabelText('Resume/CV source label')).toHaveValue(sample('resume').label)
    expect(screen.getByText(/Fictional sample loaded/)).toBeInTheDocument()
  })

  it('creates a session, ingests and opens the profile review', async () => {
    const requests = mockApi({
      'POST /api/sessions': SESSION_CREATED,
      'POST /api/profiles/ingest': jsonResponse(makeProfile()),
      'GET /api/profile': jsonResponse(makeProfile()),
    })
    const { user } = renderApp('/')

    await fillAndAcknowledge(user, 'Software Engineer at Northwind Robotics')
    await user.click(screen.getByRole('button', { name: 'Extract my profile' }))

    expect(await screen.findByRole('heading', { name: 'Review your profile' })).toBeInTheDocument()
    const ingest = requests.find((request) => request.path === '/api/profiles/ingest')!
    expect(ingest.headers.Authorization).toBe(`Bearer ${TEST_TOKEN}`)
    // Blank boxes are not sent.
    expect(ingest.body).toEqual({
      sources: [
        { source_type: 'resume', label: 'Resume', text: 'Software Engineer at Northwind Robotics' },
      ],
    })
    expect(requests.filter((request) => request.path === '/api/sessions')).toHaveLength(1)
  })

  it('disables submit and shows an honest label while extraction is running', async () => {
    seedSession()
    const ingestResponse = deferred<Response>()
    const requests = mockApi({
      'POST /api/profiles/ingest': () => ingestResponse.promise,
      'GET /api/profile': errorResponse(404, 'not_found', 'No profile'),
    })
    const { user } = renderApp('/')

    await fillAndAcknowledge(user, 'Some resume text')
    await user.click(screen.getByRole('button', { name: 'Extract my profile' }))

    expect(
      await screen.findByText('Extracting profile... this usually takes 30 to 90 seconds'),
    ).toBeInTheDocument()
    const submit = screen.getByRole('button', { name: 'Working...' })
    expect(submit).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Try sample profile' })).toBeDisabled()
    // No simulated percentage anywhere.
    expect(screen.queryByText(/\d+\s?%/)).toBeNull()

    await user.click(submit)
    expect(requests.filter((request) => request.path === '/api/profiles/ingest')).toHaveLength(1)

    ingestResponse.resolve(jsonResponse(makeProfile()))
    expect(await screen.findByRole('heading', { name: 'Review your profile' })).toBeInTheDocument()
  })

  it('keeps the input and offers Retry when extraction fails', async () => {
    seedSession()
    let attempts = 0
    const requests = mockApi({
      'GET /api/profile': errorResponse(404, 'not_found', 'No profile'),
      'POST /api/profiles/ingest': () => {
        attempts += 1
        return attempts === 1
          ? errorResponse(504, 'provider_timeout', 'The AI provider took too long to respond.', {
              retryable: true,
            })
          : jsonResponse(makeProfile())
      },
    })
    const { user } = renderApp('/')

    await fillAndAcknowledge(user, 'Resume text I do not want to lose')
    await user.click(screen.getByRole('button', { name: 'Extract my profile' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Your profile was not extracted')
    expect(alert).toHaveTextContent('The AI provider took too long to respond.')
    expect(alert).toHaveTextContent('req-test-123')
    expect(resumeBox().value).toBe('Resume text I do not want to lose')
    expect(screen.getByRole('button', { name: 'Extract my profile' })).toBeEnabled()

    await user.click(within(alert).getByRole('button', { name: 'Retry' }))

    expect(await screen.findByRole('heading', { name: 'Review your profile' })).toBeInTheDocument()
    expect(requests.filter((request) => request.path === '/api/profiles/ingest')).toHaveLength(2)
  })

  it('shows API field errors next to the field they belong to', async () => {
    seedSession()
    mockApi({
      'GET /api/profile': errorResponse(404, 'not_found', 'No profile'),
      'POST /api/profiles/ingest': errorResponse(422, 'validation_error', 'Some fields are invalid.', {
        // Index 0 of the sent array is the LinkedIn box, because the resume box is blank.
        field_errors: [{ field: 'sources.0.text', message: 'This source has no readable text.' }],
      }),
    })
    const { user } = renderApp('/')

    await user.type(screen.getByLabelText('LinkedIn profile text'), '???')
    await user.click(screen.getByLabelText('I have read how my data is sent, stored and deleted.'))
    await user.click(screen.getByRole('button', { name: 'Extract my profile' }))

    // The message sits next to its field, and the error alert lists it as well.
    const linkedInArea = within(screen.getByRole('group', { name: 'LinkedIn profile' }))
    expect(await linkedInArea.findByText('This source has no readable text.')).toBeInTheDocument()
    const summary = screen.getByText('Your profile was not extracted').closest('[role="alert"]')
    expect(summary).toHaveTextContent('This source has no readable text. (sources.0.text)')
    expect(screen.getByLabelText('LinkedIn profile text')).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByLabelText('LinkedIn profile text')).toHaveValue('???')
  })

  it('offers to continue when this tab already has a profile', async () => {
    seedSession()
    mockApi({
      'GET /api/session': jsonResponse(sessionInfo({ has_profile: true })),
      'GET /api/profile': jsonResponse(makeProfile()),
    })
    renderApp('/')

    expect(await screen.findByText('Continue where you left off')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Open my profile' })).toHaveAttribute('href', '/profile')
  })

  it('asks for confirmation before extracting again replaces an existing profile', async () => {
    seedSession()
    const requests = mockApi({
      'GET /api/session': jsonResponse(sessionInfo({ has_profile: true })),
      'GET /api/profile': jsonResponse(makeProfile()),
      'POST /api/profiles/ingest': jsonResponse(makeProfile({ version: 2 })),
    })
    const ingests = () => requests.filter((request) => request.path === '/api/profiles/ingest')
    const { user } = renderApp('/')
    await screen.findByText('Continue where you left off')
    await fillAndAcknowledge(user, 'Software Engineer at Northwind Robotics')
    const extract = screen.getByRole('button', { name: 'Extract my profile' })

    await user.click(extract)

    const dialog = within(await screen.findByRole('alertdialog', { name: 'Replace your profile?' }))
    expect(dialog.getByText(/every edit and conflict decision, is replaced/)).toBeInTheDocument()
    expect(dialog.getByText(/marked as out of date/)).toBeInTheDocument()
    expect(ingests()).toHaveLength(0)

    // Keeping the profile sends nothing, keeps the pasted text and returns to the button.
    await user.click(dialog.getByRole('button', { name: 'Keep my profile' }))
    await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull())
    expect(ingests()).toHaveLength(0)
    expect(resumeBox()).toHaveValue('Software Engineer at Northwind Robotics')
    await waitFor(() => expect(extract).toHaveFocus())

    await user.click(extract)
    await user.click(
      within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Replace my profile' }),
    )

    expect(await screen.findByRole('heading', { name: 'Review your profile' })).toBeInTheDocument()
    expect(ingests()).toHaveLength(1)
  })

  it("shows the server's own limits and retention before a session exists", async () => {
    mockApi({
      'GET /readyz': jsonResponse({
        status: 'ready',
        checks: {},
        provider_mode: 'openai',
        limits: { ...TEST_LIMITS, max_profile_chars: 100_000, session_ttl_hours: 72 },
      }),
    })
    renderApp('/')

    expect(await screen.findByText('0 / 100,000 characters in total')).toBeInTheDocument()
    expect(screen.getByText(/for at most 72 hours/)).toBeInTheDocument()
    expect(getToken()).toBeNull()
  })

  it('says so in the privacy notice when the server runs in demo mode', async () => {
    mockApi({
      'GET /readyz': jsonResponse({ status: 'ready', checks: {}, provider_mode: 'fake' }),
    })
    renderApp('/')

    await waitFor(() =>
      expect(screen.getByText(/not sent to an AI provider/)).toBeInTheDocument(),
    )
  })
})
