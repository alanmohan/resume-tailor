import { Link } from 'react-router'
import { CircleCheck, Info, TriangleAlert } from 'lucide-react'
import { ErrorAlert, LoadingBlock } from '@/components/app'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import { isProfileReady } from '@/lib/hooks'
import type { Profile } from '@/lib/types'
import { indexProgressLabel } from './profileDraft'

interface IndexStatusProps {
  profile: Profile
  /** A confirm request from this page is running. */
  isConfirming: boolean
  /** Error thrown by the last confirm attempt, if any. */
  confirmError: unknown
  /** The draft has edits that are not saved yet. */
  hasUnsavedChanges: boolean
  onRetry: () => void
}

/**
 * Where the profile stands on the way to generation: draft, indexing (with
 * live counts), failed (recoverable), confirmed, or edited after confirming.
 */
export function IndexStatus({
  profile,
  isConfirming,
  confirmError,
  hasUnsavedChanges,
  onRetry,
}: IndexStatusProps) {
  if (isConfirming || profile.index_state === 'indexing') {
    const { total, embedded } = profile.index_progress
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

  if (profile.indexed_version !== null) {
    return (
      <Alert role="status" className="border-warning/40">
        <TriangleAlert aria-hidden="true" className="text-warning" />
        <AlertTitle>Confirm again before generating</AlertTitle>
        <AlertDescription>
          You edited the profile after confirming it. Drafts are only generated from a confirmed,
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
