/**
 * TanStack Query keys, in one place so every screen reads and invalidates the
 * same cache entries. Lists and their items share a prefix, so invalidating
 * `queryKeys.jobs` also refreshes every `queryKeys.job(id)`.
 */
export const queryKeys = {
  ready: ['ready'] as const,
  session: ['session'] as const,
  profile: ['profile'] as const,
  jobs: ['jobs'] as const,
  job: (jobId: string) => ['jobs', jobId] as const,
  generations: ['generations'] as const,
  generation: (generationId: string) => ['generations', generationId] as const,
  evidence: (evidenceId: string) => ['evidence', evidenceId] as const,
}

/** Keys for mutations that other components need to observe with useIsMutating. */
export const mutationKeys = {
  confirmProfile: ['confirm-profile'] as const,
}
