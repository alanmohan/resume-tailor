import { useEffect, useRef, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router'
import { toast } from 'sonner'
import { newIdempotencyKey } from '@/lib/api'
import { queryKeys } from '@/lib/queryKeys'
import { generateDraft, isProfileNotReady } from './generateDraft'
import { workspacePath } from './jobRoutes'

/** 'generating': the request is running. 'waiting': checking on a generation that is already running. */
type GenerationPhase = 'generating' | 'waiting'

/** The idempotency key of the current attempt and the job it was made for. */
interface Attempt {
  idempotencyKey: string
  jobId: string
}

interface GenerationRequest extends Attempt {
  signal: AbortSignal
}

/**
 * Writes the resume and cover letter for a job and opens the workspace.
 *
 * Every attempt has one idempotency key. Starting again for the same job
 * after a failure (Retry) sends the same key, so the server returns the
 * stored draft (or resumes the failed one) instead of charging for a second
 * generation. A new key is only made for a different job, or when the Target
 * job screen is opened again.
 */
export function useGenerateDraft() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [attempt, setAttempt] = useState<Attempt | null>(null)
  const [phase, setPhase] = useState<GenerationPhase>('generating')
  const abortRef = useRef<AbortController | null>(null)

  // Leaving the screen stops the status checks of a generation in progress.
  useEffect(() => () => abortRef.current?.abort(), [])

  const mutation = useMutation({
    mutationFn: ({ jobId, idempotencyKey, signal }: GenerationRequest) =>
      generateDraft(jobId, idempotencyKey, { signal, onWaiting: () => setPhase('waiting') }),
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

  function start(jobId: string) {
    const idempotencyKey = attempt?.jobId === jobId ? attempt.idempotencyKey : newIdempotencyKey()
    setAttempt({ idempotencyKey, jobId })
    setPhase('generating')

    const controller = new AbortController()
    abortRef.current = controller
    mutation.mutate(
      { jobId, idempotencyKey, signal: controller.signal },
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
    /** The job of the current attempt, so a screen can tell whether this state is about its job. */
    jobId: attempt?.jobId ?? null,
    isPending: mutation.isPending,
    isSuccess: mutation.isSuccess,
    phase,
    error: mutation.error,
  }
}

export type DraftGeneration = ReturnType<typeof useGenerateDraft>
