/**
 * Addresses of the Target job screen.
 *
 * Which job the screen shows is decided by the URL alone, so a refresh or the
 * browser's Back button always returns to the same place:
 *   /job            the most recently analyzed job, or the empty form if none
 *   /job?new=1      the empty form, to analyze another job
 *   /job?job=<id>   one specific job
 */
export const NEW_JOB_PARAM = 'new'
export const JOB_ID_PARAM = 'job'

export const NEW_JOB_PATH = `/job?${NEW_JOB_PARAM}=1`

export function jobPath(jobId: string): string {
  return `/job?${JOB_ID_PARAM}=${encodeURIComponent(jobId)}`
}

export function workspacePath(generationId: string): string {
  return `/workspace/${encodeURIComponent(generationId)}`
}
