/**
 * Server state for one draft: the query that loads (and polls) it, and the
 * mutation wrapper every change to it goes through.
 */
import { useIsMutating, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { getGeneration } from '@/lib/api'
import { useSessionStatus } from '@/lib/hooks'
import { queryKeys } from '@/lib/queryKeys'
import type { Generation } from '@/lib/types'

/** How often to re-read a draft the server is still generating. */
export const GENERATION_POLL_MS = 3_000

/** Load a draft, re-reading it every few seconds for as long as its status is "running". */
export function useGeneration(generationId: string) {
  const sessionStatus = useSessionStatus()
  return useQuery({
    queryKey: queryKeys.generation(generationId),
    queryFn: () => getGeneration(generationId),
    enabled: sessionStatus === 'active' && generationId !== '',
    refetchInterval: (query) =>
      query.state.data?.status === 'running' ? GENERATION_POLL_MS : false,
  })
}

function writeKey(generationId: string) {
  return ['generation-write', generationId] as const
}

interface GenerationWriteOptions<TVariables> {
  generationId: string
  /** The API call. It must resolve to the updated draft. */
  mutationFn: (variables: TVariables) => Promise<Generation>
  /** Toast shown once the server has accepted the change. */
  successMessage: string
}

/**
 * A change to a draft (edit, regenerate, revalidate, coverage correction).
 *
 * Every such endpoint answers with the whole updated Generation, which
 * replaces the cached one. A read still in flight is cancelled first so that
 * its older answer cannot overwrite the newer one.
 */
export function useGenerationWrite<TVariables = void>({
  generationId,
  mutationFn,
  successMessage,
}: GenerationWriteOptions<TVariables>) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationKey: writeKey(generationId),
    mutationFn,
    onSuccess: async (updated) => {
      const queryKey = queryKeys.generation(generationId)
      await queryClient.cancelQueries({ queryKey })
      queryClient.setQueryData(queryKey, updated)
      toast.success(successMessage)
    },
  })
}

/**
 * True while any change to this draft is being saved. The screen disables
 * its other save buttons meanwhile: each change carries the revision it was
 * based on, so two at once would make the second one fail with a conflict.
 */
export function useIsWriting(generationId: string): boolean {
  return useIsMutating({ mutationKey: writeKey(generationId) }) > 0
}
