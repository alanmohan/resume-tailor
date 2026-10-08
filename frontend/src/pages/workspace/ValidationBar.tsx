import { useRef } from 'react'
import { ArrowDown, PencilLine, ShieldCheck } from 'lucide-react'
import { StatusBadge } from '@/components/app'
import { Button } from '@/components/ui/button'
import { validateGeneration } from '@/lib/api'
import { useGenerationWrite } from './generationData'
import { useWorkspace } from './workspaceContext'
import { formatDateTime, type LocatedClaim } from './workspaceModel'
import { WriteError } from './WriteError'

interface ValidationBarProps {
  /** The draft's flagged statements in reading order (both documents). */
  flagged: LocatedClaim[]
}

/**
 * Where the draft stands with validation: when it was last validated or that
 * edits are waiting for revalidation, how many statements are flagged, and a
 * way to step through them.
 */
export function ValidationBar({ flagged }: ValidationBarProps) {
  const { generation, isWriting, revealClaim } = useWorkspace()
  const { validation } = generation
  const statusLine = useRef<HTMLDivElement>(null)
  const lastVisited = useRef<string | null>(null)

  const revalidate = useGenerationWrite({
    generationId: generation.generation_id,
    mutationFn: () => validateGeneration(generation.generation_id),
    successMessage: 'Edited statements were revalidated',
  })

  function runRevalidation() {
    // The Revalidate button disappears on success, so move keyboard focus to the result.
    revalidate.mutate(undefined, { onSuccess: () => statusLine.current?.focus() })
  }

  /** Go to the flagged statement after the one visited last, wrapping around at the end. */
  function showNextFlagged() {
    const lastIndex = flagged.findIndex(({ claim }) => claim.item_id === lastVisited.current)
    const next = flagged[(lastIndex + 1) % flagged.length]
    lastVisited.current = next.claim.item_id
    revealClaim(next.claim.item_id)
  }

  const needsRevalidation = validation.state === 'needs_revalidation'
  const validatedAt = validation.validated_at ? formatDateTime(validation.validated_at) : null
  const {
    needs_review_count: needsReview,
    unsupported_count: unsupported,
    user_edited_count: edited,
  } = validation
  const nothingFlagged = needsReview + unsupported + edited === 0

  return (
    <div className="space-y-2 rounded-xl border bg-card px-3 py-2.5 text-sm print:hidden">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <div
          ref={statusLine}
          tabIndex={-1}
          role="status"
          className="flex flex-wrap items-center gap-x-2 gap-y-1.5 rounded-sm focus:outline-2 focus:outline-offset-2 focus:outline-ring"
        >
          {needsRevalidation ? (
            <span className="inline-flex items-center gap-1.5 font-medium">
              <PencilLine aria-hidden="true" className="size-4" />
              <span>Edited - revalidate before export</span>
            </span>
          ) : (
            <span className="inline-flex flex-wrap items-center gap-x-1.5 font-medium">
              <ShieldCheck aria-hidden="true" className="size-4" />
              <span>Validated</span>
              {validation.validated_at && validatedAt ? (
                <time dateTime={validation.validated_at} className="font-normal text-muted-foreground">
                  {validatedAt}
                </time>
              ) : null}
            </span>
          )}
          {needsReview > 0 ? (
            <StatusBadge
              status="needs_review"
              label={`${needsReview} ${needsReview === 1 ? 'needs' : 'need'} review`}
            />
          ) : null}
          {unsupported > 0 ? (
            <StatusBadge status="unsupported" label={`${unsupported} unsupported`} />
          ) : null}
          {edited > 0 ? (
            <StatusBadge status="user_edited" label={`${edited} edited, not revalidated`} />
          ) : null}
          {nothingFlagged ? (
            <span className="text-muted-foreground">No statements are flagged.</span>
          ) : null}
        </div>

        <div className="ml-auto flex flex-wrap gap-2">
          {needsRevalidation ? (
            <Button type="button" size="sm" disabled={isWriting} onClick={runRevalidation}>
              {revalidate.isPending ? 'Revalidating...' : 'Revalidate'}
            </Button>
          ) : null}
          {flagged.length > 0 ? (
            <Button type="button" variant="outline" size="sm" onClick={showNextFlagged}>
              <ArrowDown aria-hidden="true" />
              Next flagged item
            </Button>
          ) : null}
        </div>
      </div>


      {revalidate.isError ? (
        <WriteError
          error={revalidate.error}
          title="The draft was not revalidated"
          onRetry={runRevalidation}
          onReloaded={revalidate.reset}
        />
      ) : null}
    </div>
  )
}
