import { screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { jsonResponse, mockApi, seedSession, sessionInfo } from '@/test/mockApi'
import { renderApp } from '@/test/render'
import { makeJob, readyProfile } from './jobFixtures'

/**
 * The Target job screen inside the real application shell and route table.
 * Kept apart from the other job tests, which mount this screen on its own.
 */
describe('Target job route in the app', () => {
  it('is served at /job, with Target job as the current step', async () => {
    seedSession()
    mockApi({
      'GET /api/session': jsonResponse(sessionInfo({ has_profile: true })),
      'GET /api/profile': jsonResponse(readyProfile()),
    })
    renderApp('/job')

    expect(await screen.findByRole('heading', { level: 1, name: 'Target job' })).toBeInTheDocument()
    const steps = within(screen.getByRole('navigation', { name: 'Steps' }))
    expect(steps.getByText('Target job').closest('[aria-current="step"]')).not.toBeNull()
    expect(steps.getByRole('link', { name: /Profile/ })).toHaveAttribute('href', '/profile')
  })

  it('returns to the top and moves focus to the job once it is analyzed', async () => {
    const job = makeJob()
    seedSession()
    mockApi({
      'GET /api/session': jsonResponse(sessionInfo({ has_profile: true })),
      'GET /api/profile': jsonResponse(readyProfile()),
      'POST /api/jobs': jsonResponse(job, 201),
      'GET /api/jobs/job-1': jsonResponse(job),
      // Held open: this test is about the moment between the two steps.
      'POST /api/generations': () => new Promise<Response>(() => undefined),
    })
    const { user } = renderApp('/job')
    await user.click(await screen.findByRole('button', { name: 'Use sample job' }))
    vi.mocked(window.scrollTo).mockClear()

    await user.click(screen.getByRole('button', { name: 'Tailor my resume' }))

    // The stored job replaces the form at the same path (only the query
    // string changes), and the shell treats that as a new screen.
    await screen.findByRole('heading', { level: 1, name: 'Your target job' })
    expect(window.scrollTo).toHaveBeenCalledWith(0, 0)
    expect(screen.getByRole('main')).toHaveFocus()
    expect(screen.getByRole('button', { name: 'Generating...' })).toBeDisabled()
  })

  it('shows the demo banner next to the form when the provider is the fake one', async () => {
    seedSession()
    mockApi({
      'GET /api/session': jsonResponse(sessionInfo({ has_profile: true, provider_mode: 'fake' })),
      'GET /api/profile': jsonResponse(readyProfile()),
    })
    renderApp('/job')

    expect(await screen.findByRole('heading', { level: 1, name: 'Target job' })).toBeInTheDocument()
    expect(await screen.findByText(/Demo mode - responses come from a deterministic fake provider/)).toBeInTheDocument()
    expect(screen.getByLabelText('Job description')).toHaveAccessibleDescription(
      /not sent to an AI provider/,
    )
  })
})
