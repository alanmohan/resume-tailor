import type { ReactNode } from 'react'
import { CircleAlert, RotateCw } from 'lucide-react'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { errorMessage, isApiError } from '@/lib/errors'

interface ErrorAlertProps {
  /** Anything that was thrown; ApiError details are shown when available. */
  error: unknown
  title?: string
  /** Shows a Retry button that repeats the failed action. */
  onRetry?: () => void
  retryLabel?: string
  /** Disables the Retry button while the retry is running. */
  isRetrying?: boolean
  /** Extra actions next to Retry, e.g. "Reload latest version". */
  children?: ReactNode
  className?: string
}

/**
 * A failure the user can act on: what went wrong, which fields the API
 * rejected, the request ID for support, and Retry.
 */
export function ErrorAlert({
  error,
  title = 'Something went wrong',
  onRetry,
  retryLabel = 'Retry',
  isRetrying = false,
  children,
  className,
}: ErrorAlertProps) {
  const requestId = isApiError(error) ? error.requestId : null
  const fieldErrors = isApiError(error) ? error.fieldErrors : []
  return (
    <Alert variant="destructive" className={className}>
      <CircleAlert aria-hidden="true" />
      <AlertTitle>{title}</AlertTitle>
      <AlertDescription>
        <p className="break-words">{errorMessage(error)}</p>
        {fieldErrors.length > 0 ? (
          <ul className="list-disc pl-5">
            {fieldErrors.map(({ field, message }) => (
              <li key={`${field}-${message}`} className="break-words">
                {message} <span className="text-xs text-muted-foreground">({field})</span>
              </li>
            ))}
          </ul>
        ) : null}
        {requestId ? (
          <p className="text-xs text-muted-foreground">
            Request ID: <span className="break-all select-all">{requestId}</span>
          </p>
        ) : null}
        {onRetry || children ? (
          <div className="mt-2 flex flex-wrap gap-2">
            {onRetry ? (
              <Button type="button" variant="outline" size="sm" onClick={onRetry} disabled={isRetrying}>
                <RotateCw aria-hidden="true" />
                {retryLabel}
              </Button>
            ) : null}
            {children}
          </div>
        ) : null}
      </AlertDescription>
    </Alert>
  )
}
