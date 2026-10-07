import { Link } from 'react-router'
import { ErrorAlert } from '@/components/app'
import { Button } from '@/components/ui/button'
import { isApiError } from '@/lib/errors'
import { isProfileNotReady } from './generateDraft'

interface GenerationErrorProps {
  error: unknown
  /** Repeats the same attempt (same idempotency key). */
  onRetry: () => void
}

/**
 * Why no draft was produced, with the action that can actually help:
 * Retry for temporary failures, a link to the profile when it has to be
 * confirmed first, and the plain message when the session's quota is used up.
 */
export function GenerationError({ error, onRetry }: GenerationErrorProps) {
  const needsProfile = isProfileNotReady(error)
  const canRetry = !needsProfile && !isApiError(error, 'quota_exceeded')
  return (
    <ErrorAlert
      error={error}
      title="The draft was not generated"
      onRetry={canRetry ? onRetry : undefined}
    >
      {needsProfile ? (
        // The alert underlines every link inside it; this one is drawn as a button.
        <Button asChild variant="outline" size="sm" className="no-underline!">
          <Link to="/profile">Review and confirm profile</Link>
        </Button>
      ) : null}
    </ErrorAlert>
  )
}
