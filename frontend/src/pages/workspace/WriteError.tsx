import { useQueryClient } from '@tanstack/react-query'
import { ErrorAlert } from '@/components/app'
import { Button } from '@/components/ui/button'
import { isApiError } from '@/lib/errors'
import { queryKeys } from '@/lib/queryKeys'
import { useWorkspace } from './workspaceContext'

interface WriteErrorProps {
  error: unknown
  title: string
  /** Repeat the failed change exactly as it was sent. */
  onRetry: () => void
  /** Clear the error after the latest draft has been loaded. */
  onReloaded: () => void
}

/**
 * Why a change to the draft failed, with the right way forward.
 *
 * Usually that is Retry. After a version conflict (the draft changed on the
 * server since this page loaded it) retrying the same request would fail
 * again, so the action is to load the latest draft; what the user typed
 * stays in the form and can then be saved against the new revision.
 */
export function WriteError({ error, title, onRetry, onReloaded }: WriteErrorProps) {
  const { generation } = useWorkspace()
  const queryClient = useQueryClient()
  const isConflict = isApiError(error, 'version_conflict')

  async function reloadLatest() {
    await queryClient.refetchQueries({ queryKey: queryKeys.generation(generation.generation_id) })
    onReloaded()
  }

  return (
    <ErrorAlert error={error} title={title} onRetry={isConflict ? undefined : onRetry}>
      {isConflict ? (
        <Button type="button" variant="outline" size="sm" onClick={() => void reloadLatest()}>
          Reload latest version
        </Button>
      ) : null}
    </ErrorAlert>
  )
}
