import { vi } from 'vitest'
import { saveSession } from '@/lib/session'
import type { Limits, SessionInfo } from '@/lib/types'

/** Test double for the backend: a `fetch` replacement driven by a route table. */

export const TEST_TOKEN = 'test-token-abcdefghijklmnopqrstuvwxyz0123456789'
export const API = 'http://127.0.0.1:8000'

export const TEST_LIMITS: Limits = {
  max_profile_chars: 60_000,
  max_job_chars: 25_000,
  max_sources: 5,
  max_requirements: 25,
  session_ttl_hours: 24,
}

export function futureIso(hours = 24): string {
  return new Date(Date.now() + hours * 3_600_000).toISOString()
}

export function sessionInfo(overrides: Partial<SessionInfo> = {}): SessionInfo {
  return {
    expires_at: futureIso(),
    provider_mode: 'openai',
    limits: TEST_LIMITS,
    has_profile: false,
    ...overrides,
  }
}

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

export function errorResponse(
  status: number,
  code: string,
  message: string,
  extra: Record<string, unknown> = {},
): Response {
  return jsonResponse({ error: { code, message, request_id: 'req-test-123', ...extra } }, status)
}

export interface RecordedRequest {
  method: string
  path: string
  url: string
  headers: Record<string, string>
  body: unknown
}

type Handler = (request: RecordedRequest) => Response | Promise<Response>

/** Routes are keyed "METHOD /path". Unlisted routes fall back to harmless defaults. */
export type Routes = Record<string, Handler | Response>

const DEFAULT_ROUTES: Routes = {
  'GET /readyz': () =>
    jsonResponse({
      status: 'ready',
      checks: { database: 'ok', provider: 'configured' },
      provider_mode: 'openai',
    }),
  'GET /healthz': () => jsonResponse({ status: 'ok' }),
  'GET /api/session': () => jsonResponse(sessionInfo()),
  'GET /api/generations': () => jsonResponse({ generations: [] }),
}

/**
 * Replace global fetch. Returns the recorded requests so tests can assert on
 * what was sent. A route that is not listed answers 404 not_found.
 */
export function mockApi(routes: Routes = {}): RecordedRequest[] {
  const table: Routes = { ...DEFAULT_ROUTES, ...routes }
  const requests: RecordedRequest[] = []

  const fetchMock = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input)
    const request: RecordedRequest = {
      method: init.method ?? 'GET',
      path: url.replace(API, ''),
      url,
      headers: { ...(init.headers as Record<string, string> | undefined) },
      body: typeof init.body === 'string' ? JSON.parse(init.body) : undefined,
    }
    requests.push(request)
    const route = table[`${request.method} ${request.path}`]
    if (!route) return errorResponse(404, 'not_found', 'Not found')
    // A Response body can be read once, so hand out a copy each time.
    return route instanceof Response ? route.clone() : route(request)
  })
  vi.stubGlobal('fetch', fetchMock)
  return requests
}

/** Put a valid session token into sessionStorage, as if a session was created earlier. */
export function seedSession(): void {
  saveSession({ token: TEST_TOKEN, expiresAt: futureIso() })
}

/** A promise plus its resolver, for holding a mocked response open. */
export function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void } {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => {
    resolve = done
  })
  return { promise, resolve }
}
