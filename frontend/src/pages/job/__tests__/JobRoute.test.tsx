import { screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { jsonResponse, mockApi, seedSession, sessionInfo } from '@/test/mockApi'
import { renderApp } from '@/test/render'
import { makeJob, readyProfile, summaryOf } from './jobFixtures'

/**
 * The Target job screen inside the real application shell and route table.
 * Kept apart from the other job tests, which mount this screen on its own.
 */
describe('Target job route in the app', () => {
  it('is served at /job, with Target job as the current step', async () => {
    const job = makeJob()
    seedSession()
    mockApi({
      'GET /api/session': jsonResponse(sessionInfo({ has_profile: true })),
      'GET /api/profile': jsonResponse(readyProfile()),
      'GET /api/jobs': jsonResponse({ jobs: [summaryOf(job)] }),
      'GET /api/jobs/job-1': jsonResponse(job),
    })
    renderApp('/job')

    expect(
      await screen.findByRole('heading', { level: 1, name: 'Review the job requirements' }),
    ).toBeInTheDocument()
    const steps = within(screen.getByRole('navigation', { name: 'Steps' }))
    expect(steps.getByText('Target job').closest('[aria-current="step"]')).not.toBeNull()
    expect(steps.getByRole('link', { name: /Profile/ })).toHaveAttribute('href', '/profile')
  })

  it('returns to the top and moves focus to the review once a job is analyzed', async () => {
    const job = makeJob()
    seedSession()
    mockApi({
      'GET /api/session': jsonResponse(sessionInfo({ has_profile: true })),
      'GET /api/profile': jsonResponse(readyProfile()),
      'GET /api/jobs': jsonResponse({ jobs: [] }),
      'POST /api/jobs': jsonResponse(job, 201),
      'GET /api/jobs/job-1': jsonResponse(job),
    })
    const { user } = renderApp('/job')
    await user.click(await screen.findByRole('button', { name: 'Use sample job' }))
    vi.mocked(window.scrollTo).mockClear()

    await user.click(screen.getByRole('button', { name: 'Analyze job' }))

    // The review replaces the form at the same path (only the query string
    // changes), and the shell treats that as a new screen.
    await screen.findByRole('heading', { level: 1, name: 'Review the job requirements' })
    expect(window.scrollTo).toHaveBeenCalledWith(0, 0)
    expect(screen.getByRole('main')).toHaveFocus()
  })

  it('shows the demo banner next to the form when the provider is the fake one', async () => {
    seedSession()
    mockApi({
      'GET /api/session': jsonResponse(sessionInfo({ has_profile: true, provider_mode: 'fake' })),
      'GET /api/profile': jsonResponse(readyProfile()),
      'GET /api/jobs': jsonResponse({ jobs: [] }),
    })
    renderApp('/job')

    expect(await screen.findByRole('heading', { level: 1, name: 'Target job' })).toBeInTheDocument()
    expect(await screen.findByText(/Demo mode - responses come from a deterministic fake provider/)).toBeInTheDocument()
    expect(screen.getByLabelText('Job description')).toHaveAccessibleDescription(
      /not sent to an AI provider/,
    )
  })
})
