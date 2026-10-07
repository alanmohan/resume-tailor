import { screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
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
