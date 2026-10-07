import { screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { DEMO_MODE_MESSAGE, FOOTER_DISCLAIMER } from '@/components/app'
import { getSessionStatus } from '@/lib/session'
import { makeProfile } from '@/test/fixtures'
import { TEST_LIMITS, errorResponse, jsonResponse, mockApi, seedSession, sessionInfo } from '@/test/mockApi'
import { renderApp } from '@/test/render'

describe('AppShell', () => {
  it('shows the app name, the steps and the footer disclaimer', async () => {
    mockApi()
    renderApp('/')

    expect(screen.getByRole('link', { name: 'Resume Tailor' })).toBeInTheDocument()
    const steps = within(screen.getByRole('navigation', { name: 'Steps' }))
    for (const label of ['Start', 'Profile', 'Target job', 'Workspace']) {
      expect(steps.getByText(label)).toBeInTheDocument()
    }
    expect(screen.getByText(FOOTER_DISCLAIMER)).toBeInTheDocument()
    // No session yet: nothing to clear, nothing to expire.
    expect(screen.queryByRole('button', { name: 'Clear my data' })).toBeNull()
  })

  it('locks steps that are not reachable yet and marks the current one', () => {
    mockApi()
    renderApp('/')

    const steps = within(screen.getByRole('navigation', { name: 'Steps' }))
    expect(steps.getByText('Start').closest('[aria-current="step"]')).not.toBeNull()
    expect(steps.queryByRole('link', { name: /Profile/ })).toBeNull()
    expect(steps.queryByRole('link', { name: /Target job/ })).toBeNull()
    expect(steps.queryByRole('link', { name: /Workspace/ })).toBeNull()
    expect(steps.getByText(/locked: Confirm your profile first/)).toBeInTheDocument()
  })

  it('enables steps as the workflow progresses', async () => {
    seedSession()
    mockApi({
      'GET /api/session': jsonResponse(sessionInfo({ has_profile: true })),
      'GET /api/profile': jsonResponse(
        makeProfile({ status: 'confirmed', index_state: 'indexed', indexed_version: 1 }),
      ),
      'GET /api/generations': jsonResponse({
        generations: [
          { generation_id: 'gen-old', job_id: 'j', job_title: null, company: null, status: 'completed', stale: false, created_at: '2026-10-07T10:00:00Z' },
          { generation_id: 'gen-new', job_id: 'j', job_title: null, company: null, status: 'completed', stale: false, created_at: '2026-10-07T11:00:00Z' },
        ],
      }),
    })
    renderApp('/')

    const steps = within(screen.getByRole('navigation', { name: 'Steps' }))
    expect(await steps.findByRole('link', { name: /Profile/ })).toHaveAttribute('href', '/profile')
    expect(await steps.findByRole('link', { name: /Target job/ })).toHaveAttribute('href', '/job')
    expect(await steps.findByRole('link', { name: /Workspace/ })).toHaveAttribute(
      'href',
      '/workspace/gen-new',
    )
  })

  it('shows the demo banner when the provider is the fake one', async () => {
    mockApi({
      'GET /readyz': jsonResponse({ status: 'ready', checks: {}, provider_mode: 'fake' }),
    })
    renderApp('/')

    expect(await screen.findByText(DEMO_MODE_MESSAGE)).toBeInTheDocument()
  })

  it('shows the demo banner from the session even if readiness cannot be read', async () => {
    seedSession()
    mockApi({
      'GET /readyz': errorResponse(503, 'database_unavailable', 'down'),
      'GET /api/session': jsonResponse(sessionInfo({ provider_mode: 'fake' })),
    })
    renderApp('/')

    expect(await screen.findByText(DEMO_MODE_MESSAGE)).toBeInTheDocument()
  })

  it('shows no demo banner for the real provider', async () => {
    seedSession()
    const requests = mockApi()
    renderApp('/')

    await waitFor(() => expect(requests.some((r) => r.path === '/api/session')).toBe(true))
    await screen.findByRole('button', { name: 'Clear my data' })
    expect(screen.queryByText(DEMO_MODE_MESSAGE)).toBeNull()
  })

  it('replaces the page with a restart prompt when the session has expired', async () => {
    seedSession()
    mockApi({
      'GET /api/session': errorResponse(401, 'session_expired', 'Session expired'),
      'GET /api/profile': errorResponse(401, 'session_expired', 'Session expired'),
    })
    const { user } = renderApp('/profile')

    expect(await screen.findByRole('heading', { name: 'Your session has ended' })).toBeInTheDocument()
    expect(window.sessionStorage.length).toBe(0)

    await user.click(screen.getByRole('button', { name: 'Start over' }))

    expect(await screen.findByRole('heading', { name: 'Your background' })).toBeInTheDocument()
    expect(getSessionStatus()).toBe('none')
  })

  it("states the server's session length on the expired screen", async () => {
    seedSession()
    mockApi({
      'GET /readyz': jsonResponse({
        status: 'ready',
        checks: {},
        provider_mode: 'openai',
        limits: { ...TEST_LIMITS, session_ttl_hours: 72 },
      }),
      'GET /api/session': errorResponse(401, 'session_expired', 'Session expired'),
      'GET /api/profile': errorResponse(401, 'session_expired', 'Session expired'),
    })
    renderApp('/profile')

    await screen.findByRole('heading', { name: 'Your session has ended' })
    expect(await screen.findByText(/Sessions last 72 hours/)).toBeInTheDocument()
  })

  it('moves keyboard focus to the new screen after navigating, but not on first load', async () => {
    seedSession()
    mockApi({
      'GET /api/session': jsonResponse(sessionInfo({ has_profile: true })),
      'GET /api/profile': jsonResponse(makeProfile()),
    })
    const { user } = renderApp('/')
    const main = screen.getByRole('main')
    expect(main).not.toHaveFocus()

    const steps = within(screen.getByRole('navigation', { name: 'Steps' }))
    await user.click(await steps.findByRole('link', { name: /Profile/ }))

    await screen.findByRole('heading', { name: 'Review your profile' })
    expect(main).toHaveFocus()
  })

  it('renders a not-found page for unknown addresses', () => {
    mockApi()
    renderApp('/nope')

    expect(screen.getByRole('heading', { name: 'Page not found' })).toBeInTheDocument()
  })
})

describe('Clear my data', () => {
  it('explains what is deleted, calls DELETE and returns to Start', async () => {
    seedSession()
    const requests = mockApi({
      'GET /api/session': jsonResponse(sessionInfo({ has_profile: true })),
      'GET /api/profile': jsonResponse(makeProfile()),
      'DELETE /api/session': jsonResponse({
        deleted: true,
        deleted_counts: { sources: 1, profiles: 1, evidence: 0, jobs: 0, generations: 0 },
      }),
    })
    const { user } = renderApp('/profile')
    await screen.findByRole('heading', { name: 'Review your profile' })

    await user.click(screen.getByRole('button', { name: 'Clear my data' }))
    const dialog = await screen.findByRole('alertdialog')
    expect(within(dialog).getByText(/Your extracted profile and its evidence index/)).toBeInTheDocument()
    expect(within(dialog).getByText(/Every generated resume and cover letter/)).toBeInTheDocument()
    // Opening the dialog deletes nothing.
    expect(requests.some((r) => r.method === 'DELETE')).toBe(false)

    await user.click(within(dialog).getByRole('button', { name: 'Delete everything' }))

    expect(await screen.findByRole('heading', { name: 'Your background' })).toBeInTheDocument()
    expect(requests.filter((r) => r.method === 'DELETE' && r.path === '/api/session')).toHaveLength(1)
    expect(window.sessionStorage.length).toBe(0)
    expect(getSessionStatus()).toBe('none')
    expect(screen.queryByRole('button', { name: 'Clear my data' })).toBeNull()
  })

  it('keeps the session and offers a retry when deletion fails', async () => {
    seedSession()
    mockApi({
      'GET /api/profile': jsonResponse(makeProfile()),
      'DELETE /api/session': errorResponse(503, 'database_unavailable', 'Database is unavailable', {
        retryable: true,
      }),
    })
    const { user } = renderApp('/profile')
    await screen.findByRole('heading', { name: 'Review your profile' })

    await user.click(screen.getByRole('button', { name: 'Clear my data' }))
    const dialog = await screen.findByRole('alertdialog')
    await user.click(within(dialog).getByRole('button', { name: 'Delete everything' }))

    expect(await within(dialog).findByText('Your data was not deleted')).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Try again' })).toBeEnabled()
    expect(getSessionStatus()).toBe('active')
  })
})
