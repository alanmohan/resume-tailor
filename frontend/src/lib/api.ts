/**
 * The only module that calls `fetch`.
 *
 * Every function returns parsed JSON typed from `types.ts` or throws an
 * `ApiError`. Authenticated calls send the session token as a bearer header;
 * the token never goes into a URL.
 */
import { ApiError, INVALID_RESPONSE, NETWORK_ERROR, NO_SESSION, isApiError } from '@/lib/errors'
import { getToken, markSessionExpired } from '@/lib/sessionStore'
import type {
  DeleteSessionResult,
  ErrorEnvelope,
  Evidence,
  Generation,
  GenerationListResponse,
  GenerationPatchRequest,
  HealthStatus,
  Job,
  JobCreateRequest,
  Profile,
  ProfilePatchRequest,
  ReadyStatus,
  RegenerateItemRequest,
  SessionCreated,
  SessionInfo,
  SourceInput,
} from '@/lib/types'

const DEFAULT_API_BASE_URL = 'http://127.0.0.1:8000'

/** Public backend address, fixed at build time. Trailing slashes are removed. */
export const API_BASE_URL = (
  import.meta.env.VITE_API_BASE_URL || DEFAULT_API_BASE_URL
).replace(/\/+$/, '')

type HttpMethod = 'GET' | 'POST' | 'PATCH' | 'DELETE'

interface RequestOptions {
  body?: unknown
  /** Send the bearer token (default true). Only session creation and health checks opt out. */
  auth?: boolean
  headers?: Record<string, string>
}

interface RawResponse {
  status: number
  ok: boolean
  /** Parsed JSON body, or null when the body is empty or not JSON. */
  payload: unknown
}

async function readJson(response: Response): Promise<unknown> {
  const text = await response.text()
  if (!text) return null
  try {
    return JSON.parse(text)
  } catch {
    return null
  }
}

/** Perform the HTTP exchange; a request that gets no response becomes a retryable ApiError. */
async function send(method: HttpMethod, path: string, options: RequestOptions): Promise<RawResponse> {
  const headers: Record<string, string> = { Accept: 'application/json', ...options.headers }
  if (options.body !== undefined) headers['Content-Type'] = 'application/json'

  let response: Response
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      method,
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    })
  } catch {
    throw new ApiError({
      code: NETWORK_ERROR,
      status: 0,
      retryable: true,
      message: 'Could not reach the server. Check your connection and try again.',
    })
  }
  return { status: response.status, ok: response.ok, payload: await readJson(response) }
}

function isErrorEnvelope(payload: unknown): payload is ErrorEnvelope {
  if (typeof payload !== 'object' || payload === null) return false
  const error = (payload as { error?: unknown }).error
  return (
    typeof error === 'object' &&
    error !== null &&
    typeof (error as { code?: unknown }).code === 'string' &&
    typeof (error as { message?: unknown }).message === 'string'
  )
}

/** Map a non-2xx response to an ApiError, using the API's error envelope when present. */
function toApiError(status: number, payload: unknown): ApiError {
  if (isErrorEnvelope(payload)) {
    const { code, message, request_id, field_errors, retryable, details } = payload.error
    return new ApiError({
      code,
      message,
      status,
      requestId: request_id,
      fieldErrors: field_errors,
      retryable,
      details,
    })
  }
  // Not this API's envelope, e.g. the hosting platform's own 502 page.
  return new ApiError({
    code: INVALID_RESPONSE,
    status,
    retryable: status >= 500,
    message: `The server returned an unexpected response (HTTP ${status}).`,
  })
}

async function request<T>(method: HttpMethod, path: string, options: RequestOptions = {}): Promise<T> {
  const useAuth = options.auth ?? true
  const token = useAuth ? getToken() : null
  if (useAuth && !token) {
    throw new ApiError({
      code: NO_SESSION,
      status: 401,
      message: 'There is no active session in this tab. Start again to continue.',
    })
  }
  const headers = token ? { ...options.headers, Authorization: `Bearer ${token}` } : options.headers

  const { status, ok, payload } = await send(method, path, { ...options, headers })
  if (!ok) {
    // Only end the session the rejected token belongs to: a late 401 for a
    // token that was already cleared must not disturb a newer session.
    if (status === 401 && token && getToken() === token) markSessionExpired()
    throw toApiError(status, payload)
  }
  if (payload === null) {
    throw new ApiError({
      code: INVALID_RESPONSE,
      status,
      retryable: true,
      message: 'The server returned a response this app could not read.',
    })
  }
  return payload as T
}

/** A fresh key for one logical "generate" or "regenerate" action. Reuse it when retrying that action. */
export function newIdempotencyKey(): string {
  return crypto.randomUUID()
}

// ---------------------------------------------------------------- health

export function getHealth(): Promise<HealthStatus> {
  return request<HealthStatus>('GET', '/healthz', { auth: false })
}

/** Readiness, including the provider mode. A 503 "not_ready" body is returned, not thrown. */
export async function getReady(): Promise<ReadyStatus> {
  const { status, ok, payload } = await send('GET', '/readyz', {})
  const body = payload as Partial<ReadyStatus> | null
  if (body && (body.status === 'ready' || body.status === 'not_ready')) {
    return body as ReadyStatus
  }
  throw ok
    ? new ApiError({ code: INVALID_RESPONSE, status, message: 'Unreadable readiness response.' })
    : toApiError(status, payload)
}

// --------------------------------------------------------------- session

const WAKE_DEADLINE_MS = 90_000
const WAKE_NOTICE_AFTER_MS = 4_000
const WAKE_RETRY_DELAYS_MS = [2_000, 3_000, 5_000, 8_000]
const WAKE_MAX_DELAY_MS = 10_000

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

/** No response, or a gateway/unavailable status: the signs of a server that is still starting. */
function looksLikeServerWaking(error: unknown): boolean {
  return isApiError(error) && [0, 502, 503, 504].includes(error.status)
}

export interface CreateSessionOptions {
  /** Called (possibly more than once) when the server appears to be waking up. */
  onWaking?: () => void
}

/**
 * Create an anonymous session.
 *
 * The free hosting tier puts the backend to sleep when idle and needs about a
 * minute to wake it. So this call retries with backoff for up to ~90 seconds
 * while the server is unreachable, and tells the caller so it can show
 * "Waking the server...". Other failures (for example 429) are thrown at once.
 */
export async function createSession(options: CreateSessionOptions = {}): Promise<SessionCreated> {
  const startedAt = Date.now()
  // A sleeping server may also just hold the first request open while it
  // boots, so announce the wake-up when a single attempt is slow.
  const slowTimer = setTimeout(() => options.onWaking?.(), WAKE_NOTICE_AFTER_MS)
  try {
    for (let attempt = 0; ; attempt += 1) {
      try {
        return await request<SessionCreated>('POST', '/api/sessions', { auth: false })
      } catch (error) {
        const delay = WAKE_RETRY_DELAYS_MS[attempt] ?? WAKE_MAX_DELAY_MS
        const outOfTime = Date.now() - startedAt + delay > WAKE_DEADLINE_MS
        if (!looksLikeServerWaking(error) || outOfTime) throw error
        options.onWaking?.()
        await sleep(delay)
      }
    }
  } finally {
    clearTimeout(slowTimer)
  }
}

export function getSession(): Promise<SessionInfo> {
  return request<SessionInfo>('GET', '/api/session')
}

/** Revoke the session and delete everything stored for it. */
export function deleteSession(): Promise<DeleteSessionResult> {
  return request<DeleteSessionResult>('DELETE', '/api/session')
}

// --------------------------------------------------------------- profile

export function ingestProfile(sources: SourceInput[]): Promise<Profile> {
  return request<Profile>('POST', '/api/profiles/ingest', { body: { sources } })
}

export function getProfile(): Promise<Profile> {
  return request<Profile>('GET', '/api/profile')
}

export function updateProfile(body: ProfilePatchRequest): Promise<Profile> {
  return request<Profile>('PATCH', '/api/profile', { body })
}

export function confirmProfile(expectedVersion: number): Promise<Profile> {
  return request<Profile>('POST', '/api/profile/confirm', {
    body: { expected_version: expectedVersion },
  })
}

// ------------------------------------------------------------------ jobs

export function createJob(body: JobCreateRequest): Promise<Job> {
  return request<Job>('POST', '/api/jobs', { body })
}

export function getJob(jobId: string): Promise<Job> {
  return request<Job>('GET', `/api/jobs/${encodeURIComponent(jobId)}`)
}

// ----------------------------------------------------------- generations

/** Start a generation. Sending the same key again returns the stored result without a second paid call. */
export function createGeneration(jobId: string, idempotencyKey: string): Promise<Generation> {
  return request<Generation>('POST', '/api/generations', {
    body: { job_id: jobId },
    headers: { 'Idempotency-Key': idempotencyKey },
  })
}

export function listGenerations(): Promise<GenerationListResponse> {
  return request<GenerationListResponse>('GET', '/api/generations')
}

export function getGeneration(generationId: string): Promise<Generation> {
  return request<Generation>('GET', `/api/generations/${encodeURIComponent(generationId)}`)
}

/** Save manual text edits and coverage corrections. Never regenerates text. */
export function updateGeneration(
  generationId: string,
  body: GenerationPatchRequest,
): Promise<Generation> {
  return request<Generation>('PATCH', `/api/generations/${encodeURIComponent(generationId)}`, {
    body,
  })
}

/** Re-check user-edited items against their cited evidence without changing any text. */
export function validateGeneration(generationId: string): Promise<Generation> {
  return request<Generation>(
    'POST',
    `/api/generations/${encodeURIComponent(generationId)}/validate`,
  )
}

export function regenerateItem(
  generationId: string,
  itemId: string,
  idempotencyKey: string,
  body: RegenerateItemRequest = {},
): Promise<Generation> {
  const path = `/api/generations/${encodeURIComponent(generationId)}/items/${encodeURIComponent(itemId)}/regenerate`
  return request<Generation>('POST', path, {
    body: { instruction: body.instruction ?? null },
    headers: { 'Idempotency-Key': idempotencyKey },
  })
}

// -------------------------------------------------------------- evidence

export function getEvidence(evidenceId: string): Promise<Evidence> {
  return request<Evidence>('GET', `/api/evidence/${encodeURIComponent(evidenceId)}`)
}
