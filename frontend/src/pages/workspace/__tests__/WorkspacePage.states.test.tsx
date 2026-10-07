import { act, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { errorResponse, jsonResponse, mockApi, seedSession } from '@/test/mockApi'
import { renderApp } from '@/test/render'
import { GENERATION_POLL_MS } from '../generationData'
import { GENERATION_ID, makeGeneration } from './fixtures'
import { allowForSlowMachine, draftIsShown, generationPath, openWorkspace } from './helpers'

allowForSlowMachine()

const GET_DRAFT = `GET /api/generations/${GENERATION_ID}`

describe('WorkspacePage loading states', () => {
  it('links to Start when the tab has no session', () => {
    mockApi()
    renderApp(`/workspace/${GENERATION_ID}`)

    expect(screen.getByRole('heading', { name: 'No draft to show' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Go to Start' })).toHaveAttribute('href', '/')
  })

  it('shows a labelled placeholder while the draft loads', async () => {
    seedSession()
    mockApi({ [GET_DRAFT]: () => new Promise<Response>(() => undefined) })
    renderApp(`/workspace/${GENERATION_ID}`)

    expect(await screen.findByText('Loading your draft...')).toBeInTheDocument()
    expect(screen.queryByRole('tab')).toBeNull()
  })

  it('shows a friendly message for a draft that does not exist', async () => {
    seedSession()
    mockApi({ [GET_DRAFT]: errorResponse(404, 'not_found', 'Not found') })
    renderApp(`/workspace/${GENERATION_ID}`)

    expect(await screen.findByRole('heading', { name: 'Draft not found' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Go to target job' })).toHaveAttribute('href', '/job')
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('offers Retry when the draft cannot be loaded', async () => {
    seedSession()
    let attempts = 0
    mockApi({
      [GET_DRAFT]: () => {
        attempts += 1
        return attempts === 1
          ? errorResponse(503, 'database_unavailable', 'The database is unavailable.')
          : jsonResponse(makeGeneration())
      },
    })
    const { user } = renderApp(`/workspace/${GENERATION_ID}`)

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('This draft could not be loaded')
    expect(alert).toHaveTextContent('The database is unavailable.')
    await user.click(within(alert).getByRole('button', { name: 'Retry' }))

    await draftIsShown()
  })
})

describe('WorkspacePage while the draft is generated', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it('says "Generating..." and re-reads the draft every 3 seconds until it is ready', async () => {
    expect(GENERATION_POLL_MS).toBe(3_000)
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const running = makeGeneration({ status: 'running', resume: null, cover_letter: null })
    let reads = 0
    const { requests } = openWorkspace(running, {
      [`GET ${generationPath(running)}`]: () => {
        reads += 1
        return jsonResponse(reads < 3 ? running : makeGeneration())
      },
    })
    const draftReads = () => requests.filter((request) => request.path === generationPath(running))

    expect(await screen.findByText('Generating...')).toBeInTheDocument()
    expect(screen.getByText(/Backend Engineer at Acme Analytics/)).toBeInTheDocument()
    // Back to the job this draft is for, not to whichever job is newest.
    expect(screen.getByRole('link', { name: 'Back to target job' })).toHaveAttribute(
      'href',
      '/job?job=job-1',
    )
    expect(screen.queryByRole('tab')).toBeNull()
    expect(draftReads()).toHaveLength(1)

    await act(() => vi.advanceTimersByTimeAsync(GENERATION_POLL_MS))
    expect(draftReads()).toHaveLength(2)
    expect(screen.getByText('Generating...')).toBeInTheDocument()

    await act(() => vi.advanceTimersByTimeAsync(GENERATION_POLL_MS))
    expect(draftReads()).toHaveLength(3)
    await draftIsShown()
    expect(screen.queryByText('Generating...')).toBeNull()

    // A finished draft is not polled any more.
    await act(() => vi.advanceTimersByTimeAsync(GENERATION_POLL_MS * 3))
    expect(draftReads()).toHaveLength(3)
  })
})

describe('WorkspacePage for a failed or stale draft', () => {
  it('shows why generation failed and links back to the target job', async () => {
    openWorkspace(
      makeGeneration({
        status: 'failed',
        error: { code: 'provider_timeout', message: 'The AI provider took too long to answer.' },
        resume: null,
        cover_letter: null,
      }),
    )

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('This draft could not be generated')
    expect(alert).toHaveTextContent('The AI provider took too long to answer.')
    expect(within(alert).getByRole('link', { name: 'Back to target job' })).toHaveAttribute(
      'href',
      '/job?job=job-1',
    )
    expect(screen.queryByRole('tab')).toBeNull()
  })

  it('labels a stale draft with the reason and still shows it', async () => {
    openWorkspace(makeGeneration({ stale: true, stale_reasons: ['profile_changed'] }))
    await draftIsShown()

    const banner = screen.getByText('This draft is out of date').closest('[role="status"]') as HTMLElement
    expect(banner).toHaveTextContent('Your profile changed after this draft was generated.')
    expect(banner).toHaveTextContent('You can still read, copy and print it')
    // The draft's own job: with several jobs, the bare /job would open the newest one.
    expect(within(banner).getByRole('link', { name: 'Generate a new draft' })).toHaveAttribute(
      'href',
      '/job?job=job-1',
    )
    expect(screen.getByRole('heading', { name: 'Riley Example' })).toBeInTheDocument()
  })

  it('names both reasons when the profile and the job changed', async () => {
    openWorkspace(makeGeneration({ stale: true, stale_reasons: ['profile_changed', 'job_changed'] }))
    await draftIsShown()

    expect(
      screen.getByText(/Your profile and the target job changed after this draft was generated\./),
    ).toBeInTheDocument()
  })

  it('shows no stale banner for a current draft', async () => {
    openWorkspace()
    await draftIsShown()

    expect(screen.queryByText('This draft is out of date')).toBeNull()
  })
})
