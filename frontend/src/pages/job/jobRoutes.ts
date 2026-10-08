/**
 * Addresses of the Target job screen.
 *
 * What the screen shows is decided by the URL alone, so a refresh or the
 * browser's Back button always returns to the same place:
 *   /job            the form for a new job description
 *   /job?job=<id>   a job that was already analyzed, to generate a draft for it
 */
export const JOB_ID_PARAM = 'job'

export function jobPath(jobId: string): string {
  return `/job?${JOB_ID_PARAM}=${encodeURIComponent(jobId)}`
}

export function workspacePath(generationId: string): string {
  return `/workspace/${encodeURIComponent(generationId)}`
}
