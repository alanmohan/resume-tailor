import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { CircleCheck, Info, TriangleAlert } from 'lucide-react'
import { ErrorAlert, LoadingBlock } from '@/components/app'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import { isProfileReady } from '@/lib/hooks'
import type { Profile } from '@/lib/types'
import { indexProgressLabel } from './profileDraft'

/** How long the embedded count may stand still before indexing is treated as stopped. */
export const INDEX_STALL_MS = 20_000

/**
 * True once the embedded count has not moved for INDEX_STALL_MS while the
 * server still reports "indexing". That is what a profile looks like after
 * the server was restarted in the middle of a run: nothing is working on it.
 */
function useIndexingStalled(watching: boolean, embedded: number): boolean {
  /** The count the timer last ran out on. A different current count means progress was made. */
  const [stalledCount, setStalledCount] = useState<number | null>(null)
  useEffect(() => {
    if (!watching) return
    // Every change of the count restarts the timer.
    const timer = window.setTimeout(() => setStalledCount(embedded), INDEX_STALL_MS)
    return () => window.clearTimeout(timer)
  }, [watching, embedded])
  return watching && stalledCount === embedded
}

interface IndexStatusProps {
  profile: Profile
  /** A confirm request from this page is running. */
  isConfirming: boolean
  /** Error thrown by the last confirm attempt, if any. */
  confirmError: unknown
  /** The draft has edits that are not saved yet. */
  hasUnsavedChanges: boolean
  /** The session already has a generated draft, so this profile was confirmed before. */
  hasDrafts: boolean
  onRetry: () => void
}

/**
 * Where the profile stands on the way to generation: draft, indexing (with
 * live counts, or stopped), failed (recoverable), confirmed, or changed after
 * confirming.
 */
export function IndexStatus({
  profile,
  isConfirming,
  confirmError,
  hasUnsavedChanges,
  hasDrafts,
  onRetry,
}: IndexStatusProps) {
  const { total, embedded } = profile.index_progress
  // Only a run this page did not start can be orphaned; its own request is still open.
  const stalled = useIndexingStalled(!isConfirming && profile.index_state === 'indexing', embedded)

  if (stalled) {
    return (
      <Alert role="status" className="border-warning/40">
        <TriangleAlert aria-hidden="true" className="text-warning" />
        <AlertTitle>Indexing seems to have stopped</AlertTitle>
        <AlertDescription>
          The count has stayed at {embedded} of {total} evidence records, which usually means the
          server was restarted. Nothing was lost: confirm the profile again to resume indexing.
        </AlertDescription>
        {/* Outside AlertDescription, which styles every link inside it as underlined text. */}
        <div className="col-start-2 mt-2">
          <Button type="button" size="sm" onClick={onRetry}>
            Confirm again
          </Button>
        </div>
      </Alert>
    )
  }

  if (isConfirming || profile.index_state === 'indexing') {
    return (
      <div className="space-y-3 rounded-xl border p-4">
        <LoadingBlock label={indexProgressLabel(profile)} lines={0} />
        {total > 0 ? (
          <Progress value={(embedded / total) * 100} aria-label="Evidence records embedded" />
        ) : null}
        {isConfirming ? null : (
          <p className="text-sm text-muted-foreground">
            Indexing is running on the server. This page updates by itself.
          </p>
        )}
      </div>
    )
  }

  if (confirmError || profile.index_state === 'failed') {
    const error =
      confirmError ??
      new Error(profile.index_error ?? 'The evidence index could not be built. Nothing was lost.')
    return (
      <ErrorAlert
        error={error}
        title="The profile was not confirmed"
        onRetry={onRetry}
        retryLabel="Retry confirmation"
      />
    )
  }

  if (isProfileReady(profile)) {
    return (
      <Alert role="status">
        <CircleCheck aria-hidden="true" className="text-success" />
        <AlertTitle>Profile confirmed</AlertTitle>
        <AlertDescription>
          <p>
            {profile.index_progress.total} evidence records are indexed and ready to support a
            tailored draft.
          </p>
          {hasUnsavedChanges ? (
            <p>You have unsaved edits. Saving them means confirming the profile again.</p>
          ) : null}
        </AlertDescription>
        {/* Outside AlertDescription, which styles every link inside it as underlined text. */}
        {hasUnsavedChanges ? null : (
          <div className="col-start-2 mt-2">
            <Button asChild size="sm">
              <Link to="/job">Continue to target job</Link>
            </Button>
          </div>
        )}
      </Alert>
    )
  }

  // Not ready, although it was confirmed before. The server may or may not keep
  // indexed_version after an edit, so an existing draft counts as proof as well:
  // a draft can only have been generated from a confirmed profile.
  if (profile.indexed_version !== null || hasDrafts) {
    return (
      <Alert role="status" className="border-warning/40">
        <TriangleAlert aria-hidden="true" className="text-warning" />
        <AlertTitle>Confirm again before generating</AlertTitle>
        <AlertDescription>
          You changed the profile after confirming it. Drafts are only generated from a confirmed,
          fully indexed profile, so confirm it again to index your changes.
        </AlertDescription>
      </Alert>
    )
  }

  return (
    <Alert role="status">
      <Info aria-hidden="true" />
      <AlertTitle>Draft profile</AlertTitle>
      <AlertDescription>
        Check each record against its source and correct anything that is wrong. When it is
        accurate, confirm the profile to build the evidence index used for tailoring.
      </AlertDescription>
    </Alert>
  )
}
