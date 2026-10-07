/**
 * Start a generation and see it through to a finished draft.
 *
 * The server generates synchronously: POST /api/generations normally answers
 * with the completed draft. When the same idempotency key already has a
 * generation running (for example because the first response was lost on the
 * way back), the server answers 409 generation_in_progress instead, and this
 * module checks that generation every few seconds until it finishes.
 */
import { createGeneration, getGeneration } from '@/lib/api'
import { ApiError, isApiError } from '@/lib/errors'
import type { Generation } from '@/lib/types'

/** How often a generation that is still running is checked. */
export const POLL_INTERVAL_MS = 3_000

/**
 * How long to keep checking. The server treats a generation that has been
 * running for five minutes as failed, so one more minute is always enough.
 */
export const MAX_WAIT_MS = 6 * 60_000

/** Client-side error codes, for failures that are not an HTTP error response. */
export const GENERATION_STILL_RUNNING = 'generation_still_running'
export const GENERATION_FAILED = 'generation_failed'

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

/** The ID carried by a generation_in_progress error, or null for any other error. */
function runningGenerationId(error: unknown): string | null {
  if (!isApiError(error, 'generation_in_progress')) return null
  const id = error.details.generation_id
  return typeof id === 'string' ? id : null
}

/** Generation was refused because the profile is not confirmed and fully indexed. */
export function isProfileNotReady(error: unknown): boolean {
  return isApiError(error, 'profile_not_confirmed') || isApiError(error, 'profile_not_indexed')
}

/**
 * POST the generation. If this key already has one running, read that one
 * instead of starting a second, paid, generation.
 */
async function startOrJoin(jobId: string, idempotencyKey: string): Promise<Generation> {
  try {
    return await createGeneration(jobId, idempotencyKey)
  } catch (error) {
    const runningId = runningGenerationId(error)
    if (runningId === null) throw error
    return getGeneration(runningId)
  }
}

/**
 * A failure the server stored on the generation record. It is raised as an
 * ApiError carrying the stored code, so every failure reaches the same alert.
 * Status 0: it was read from a record, not from an HTTP error response.
 */
function failedGenerationError(generation: Generation): ApiError {
  return new ApiError({
    code: generation.error?.code ?? GENERATION_FAILED,
    message: generation.error?.message ?? 'The draft could not be generated.',
    status: 0,
    // The API accepts the same idempotency key again after a failure.
    retryable: true,
  })
}

export interface GenerateOptions {
  /** Aborted when the user leaves the screen, so the checking stops. */
  signal: AbortSignal
  /** Called once if the generation is still running and has to be waited for. */
  onWaiting: () => void
}

/**
 * Resolve with the completed generation, or throw: the API's own error, a
 * stored failure, or `generation_still_running` after MAX_WAIT_MS. Retrying
 * with the same `idempotencyKey` never produces a second draft.
 */
export async function generateDraft(
  jobId: string,
  idempotencyKey: string,
  { signal, onWaiting }: GenerateOptions,
): Promise<Generation> {
  let generation = await startOrJoin(jobId, idempotencyKey)
  if (generation.status === 'running') onWaiting()

  const deadline = Date.now() + MAX_WAIT_MS
  while (generation.status === 'running') {
    if (Date.now() >= deadline) {
      throw new ApiError({
        code: GENERATION_STILL_RUNNING,
        message: 'The draft is still being generated. Wait a moment, then retry to check on it.',
        status: 0,
        retryable: true,
      })
    }
    await sleep(POLL_INTERVAL_MS)
    if (signal.aborted) throw new DOMException('Stopped waiting for the draft.', 'AbortError')
    generation = await getGeneration(generation.generation_id)
  }

  if (generation.status === 'failed') throw failedGenerationError(generation)
  return generation
}
