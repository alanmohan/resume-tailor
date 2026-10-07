import { useEffect, useRef, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router'
import { toast } from 'sonner'
import { newIdempotencyKey } from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'
import type { Job } from '@/lib/types'
import { generateDraft, isProfileNotReady } from './generateDraft'
import { workspacePath } from './jobRoutes'

/** 'generating': the request is running. 'waiting': checking on a generation that is already running. */
export type GenerationPhase = 'generating' | 'waiting'

/** The idempotency key of the current attempt and the job version it was made for. */
interface Attempt {
  idempotencyKey: string
  jobVersion: number
}

/**
 * "Generate tailored resume and cover letter" for one job.
 *
 * Every attempt has one idempotency key. Pressing Retry after a failure sends
 * the same key again, so the server returns the stored draft (or resumes the
 * failed one) instead of charging for a second generation. A new key is only
 * made when the job itself has changed since the last attempt.
 */
export function useGenerateDraft(job: Job) {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [attempt, setAttempt] = useState<Attempt | null>(null)
  const [phase, setPhase] = useState<GenerationPhase>('generating')
  const abortRef = useRef<AbortController | null>(null)

  // Leaving the screen stops the status checks of a generation in progress.
  useEffect(() => () => abortRef.current?.abort(), [])

  const mutation = useMutation({
    mutationFn: ({ idempotencyKey, signal }: { idempotencyKey: string; signal: AbortSignal }) =>
      generateDraft(job.job_id, idempotencyKey, { signal, onWaiting: () => setPhase('waiting') }),
    onSuccess: (generation) => {
      // The workspace opens with this draft already loaded.
      queryClient.setQueryData(queryKeys.generation(generation.generation_id), generation)
      // Only the list: it feeds the Workspace step in the navigation.
      void queryClient.invalidateQueries({ queryKey: queryKeys.generations, exact: true })
    },
    onError: (error) => {
      // The profile changed underneath this screen; reload it so the page says so.
      if (isProfileNotReady(error)) {
        void queryClient.invalidateQueries({ queryKey: queryKeys.profile })
      }
    },
  })

  function start() {
    const idempotencyKey =
      attempt?.jobVersion === job.version ? attempt.idempotencyKey : newIdempotencyKey()
    setAttempt({ idempotencyKey, jobVersion: job.version })
    setPhase('generating')

    const controller = new AbortController()
    abortRef.current = controller
    mutation.mutate(
      { idempotencyKey, signal: controller.signal },
      {
        // Runs only while this screen is still open.
        onSuccess: (generation) => {
          toast.success('Draft generated')
          void navigate(workspacePath(generation.generation_id))
        },
      },
    )
  }

  return {
    start,
    /** Forget the last failure, e.g. after the job was saved again. */
    reset: mutation.reset,
    isPending: mutation.isPending,
    phase,
    error: mutation.error,
  }
}
