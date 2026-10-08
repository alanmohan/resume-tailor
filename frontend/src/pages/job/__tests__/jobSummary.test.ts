import { describe, expect, it } from 'vitest'
import { jobPath, workspacePath } from '../jobRoutes'
import { formatDateTime, jobDisplayName } from '../jobSummary'

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
