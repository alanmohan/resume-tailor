import { describe, expect, it } from 'vitest'
import { jobPath, workspacePath } from '../jobRoutes'
import { formatDateTime, jobDisplayName, newestJob } from '../jobSummary'
import { makeJob, summaryOf } from './jobFixtures'

describe('jobDisplayName', () => {
  it('combines title and company, and falls back when one or both are missing', () => {
    expect(jobDisplayName({ title: 'Engineer', company: 'Fernhollow AI' })).toBe(
      'Engineer at Fernhollow AI',
    )
    expect(jobDisplayName({ title: 'Engineer', company: null })).toBe('Engineer')
    expect(jobDisplayName({ title: '  ', company: 'Fernhollow AI' })).toBe('Fernhollow AI')
    expect(jobDisplayName({ title: null, company: null })).toBe('Untitled job')
  })
})

describe('newestJob', () => {
  it('returns null when there are no jobs', () => {
    expect(newestJob([])).toBeNull()
  })

  it('picks the latest creation time whatever the order of the list', () => {
    const morning = summaryOf(makeJob({ job_id: 'a', created_at: '2026-10-07T09:00:00.000Z' }))
    const evening = summaryOf(makeJob({ job_id: 'b', created_at: '2026-10-07T21:00:00.000Z' }))
    const noon = summaryOf(makeJob({ job_id: 'c', created_at: '2026-10-07T12:00:00.000Z' }))

    expect(newestJob([morning, evening, noon])?.job_id).toBe('b')
    expect(newestJob([evening, noon, morning])?.job_id).toBe('b')
  })
})

describe('formatDateTime', () => {
  it('formats an API timestamp and leaves unreadable input as it is', () => {
    expect(formatDateTime('2026-10-07T12:00:00.000Z')).toMatch(/2026/)
    expect(formatDateTime('not a date')).toBe('not a date')
  })
})

describe('paths', () => {
  it('encodes IDs into the job and workspace addresses', () => {
    expect(jobPath('job 1/2')).toBe('/job?job=job%201%2F2')
    expect(workspacePath('gen 1/2')).toBe('/workspace/gen%201%2F2')
  })
})
