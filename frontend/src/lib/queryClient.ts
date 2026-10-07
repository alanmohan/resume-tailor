import { QueryClient } from '@tanstack/react-query'
import { isRetryable } from '@/lib/errors'

const MAX_QUERY_RETRIES = 2

/**
 * Build the app's QueryClient (tests build their own for isolation).
 *
 * Reads retry twice, and only for errors the API marked retryable. Mutations
 * never retry on their own: most of them are quota-charged AI calls, so the
 * user decides with an explicit Retry button.
 */
export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        refetchOnWindowFocus: false,
        retry: (failureCount, error) => isRetryable(error) && failureCount < MAX_QUERY_RETRIES,
      },
      mutations: {
        retry: false,
      },
    },
  })
}
