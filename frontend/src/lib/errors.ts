import type { FieldError } from '@/lib/types'

/** Codes the client produces itself, for failures that never reached the API. */
export const NETWORK_ERROR = 'network_error'
export const NO_SESSION = 'no_session'
export const INVALID_RESPONSE = 'invalid_response'

interface ApiErrorInit {
  code: string
  message: string
  status: number
  requestId?: string | null
  fieldErrors?: FieldError[]
  retryable?: boolean
  details?: Record<string, unknown>
}

/**
 * The single error type thrown by `src/lib/api.ts`.
 *
 * `status` is the HTTP status, or 0 when no response arrived (network down,
 * server asleep, CORS failure).
 */
export class ApiError extends Error {
  readonly code: string
  readonly status: number
  readonly requestId: string | null
  readonly fieldErrors: FieldError[]
  readonly retryable: boolean
  readonly details: Record<string, unknown>

  constructor(init: ApiErrorInit) {
    super(init.message)
    this.name = 'ApiError'
    this.code = init.code
    this.status = init.status
    this.requestId = init.requestId ?? null
    this.fieldErrors = init.fieldErrors ?? []
    this.retryable = init.retryable ?? false
    this.details = init.details ?? {}
  }
}

/** True for an ApiError, optionally only when it carries the given code. */
export function isApiError(error: unknown, code?: string): error is ApiError {
  return error instanceof ApiError && (code === undefined || error.code === code)
}

/** Whether offering "Retry" makes sense without the user changing anything. */
export function isRetryable(error: unknown): boolean {
  return isApiError(error) && error.retryable
}

/** A sentence that is safe to show to the user for any thrown value. */
export function errorMessage(error: unknown): string {
  if (isApiError(error)) return error.message
  if (error instanceof Error && error.message) return error.message
  return 'Something went wrong. Please try again.'
}

/**
 * Index the API's field errors by field path, e.g. "sources.0.text".
 * A leading "body." (the request-body prefix some validators add) is dropped
 * so callers can match paths against their own form field names.
 */
export function fieldErrorMap(error: unknown): Record<string, string> {
  if (!isApiError(error)) return {}
  const map: Record<string, string> = {}
  for (const { field, message } of error.fieldErrors) {
    const path = field.replace(/^body\./, '')
    if (!(path in map)) map[path] = message
  }
  return map
}
