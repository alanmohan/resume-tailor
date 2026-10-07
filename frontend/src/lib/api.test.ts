import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  confirmProfile,
  createGeneration,
  createSession,
  getProfile,
  getReady,
  ingestProfile,
  regenerateItem,
} from '@/lib/api'
import { ApiError, INVALID_RESPONSE, NETWORK_ERROR, NO_SESSION } from '@/lib/errors'
import { getSessionStatus, getToken } from '@/lib/session'
import { makeProfile } from '@/test/fixtures'
import {
  API,
  TEST_LIMITS,
  TEST_TOKEN,
  errorResponse,
  futureIso,
  jsonResponse,
  mockApi,
  seedSession,
} from '@/test/mockApi'

/** Run a call that must fail and return the ApiError it threw. */
async function failure(call: Promise<unknown>): Promise<ApiError> {
  const error = await call.then(
    () => null,
    (thrown: unknown) => thrown,
  )
  expect(error).toBeInstanceOf(ApiError)
  return error as ApiError
}

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('authenticated requests', () => {
  it('sends the token as a bearer header and never in the URL', async () => {
    seedSession()
    const requests = mockApi({ 'GET /api/profile': jsonResponse(makeProfile()) })

    const profile = await getProfile()

    expect(profile.profile_id).toBe('prof-1')
    expect(requests).toHaveLength(1)
    expect(requests[0].headers.Authorization).toBe(`Bearer ${TEST_TOKEN}`)
    expect(requests[0].url).toBe(`${API}/api/profile`)
    expect(requests[0].url).not.toContain(TEST_TOKEN)
  })

  it('sends JSON bodies with a content type', async () => {
    seedSession()
    const requests = mockApi({ 'POST /api/profile/confirm': jsonResponse(makeProfile()) })

    await confirmProfile(4)

    expect(requests[0].headers['Content-Type']).toBe('application/json')
    expect(requests[0].body).toEqual({ expected_version: 4 })
  })

  it('refuses to call the API without a session', async () => {
    const requests = mockApi()

    const error = await failure(getProfile())

    expect(error.code).toBe(NO_SESSION)
    expect(requests).toHaveLength(0)
  })

  it('sends the Idempotency-Key header for generation and regeneration', async () => {
    seedSession()
    const requests = mockApi({
      'POST /api/generations': jsonResponse({ generation_id: 'gen-1' }, 201),
      'POST /api/generations/gen-1/items/item-1/regenerate': jsonResponse({ generation_id: 'gen-1' }),
    })

    await createGeneration('job-1', 'key-12345678')
    await regenerateItem('gen-1', 'item-1', 'key-87654321', { instruction: 'Shorter' })

    expect(requests[0].headers['Idempotency-Key']).toBe('key-12345678')
    expect(requests[0].body).toEqual({ job_id: 'job-1' })
    expect(requests[1].headers['Idempotency-Key']).toBe('key-87654321')
    expect(requests[1].body).toEqual({ instruction: 'Shorter' })
  })
})

describe('error mapping', () => {
  it('maps the error envelope to an ApiError', async () => {
    seedSession()
    mockApi({
      'POST /api/profiles/ingest': errorResponse(422, 'validation_error', 'Check your input', {
        field_errors: [{ field: 'sources.0.text', message: 'Text is required' }],
        retryable: false,
        details: { limit: 5 },
      }),
    })

    const error = await failure(ingestProfile([]))

    expect(error.code).toBe('validation_error')
    expect(error.message).toBe('Check your input')
    expect(error.status).toBe(422)
    expect(error.requestId).toBe('req-test-123')
    expect(error.fieldErrors).toEqual([{ field: 'sources.0.text', message: 'Text is required' }])
    expect(error.retryable).toBe(false)
    expect(error.details).toEqual({ limit: 5 })
  })

  it('keeps the retryable flag of provider errors', async () => {
    seedSession()
    mockApi({
      'GET /api/profile': errorResponse(504, 'provider_timeout', 'The AI provider timed out', {
        retryable: true,
      }),
    })

    const error = await failure(getProfile())

    expect(error.code).toBe('provider_timeout')
    expect(error.retryable).toBe(true)
  })

  it('clears the token and flags the session as expired on 401', async () => {
    seedSession()
    mockApi({ 'GET /api/profile': errorResponse(401, 'session_expired', 'Session expired') })

    const error = await failure(getProfile())

    expect(error.code).toBe('session_expired')
    expect(getToken()).toBeNull()
    expect(getSessionStatus()).toBe('expired')
    expect(window.sessionStorage.length).toBe(0)
  })

  it('turns a network failure into a retryable ApiError', async () => {
    seedSession()
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))

    const error = await failure(getProfile())

    expect(error.code).toBe(NETWORK_ERROR)
    expect(error.status).toBe(0)
    expect(error.retryable).toBe(true)
    // A network failure says nothing about the session, so the token stays.
    expect(getToken()).toBe(TEST_TOKEN)
  })

  it('handles an error response that is not the API envelope', async () => {
    seedSession()
    mockApi({ 'GET /api/profile': new Response('<html>Bad Gateway</html>', { status: 502 }) })

    const error = await failure(getProfile())

    expect(error.code).toBe(INVALID_RESPONSE)
    expect(error.status).toBe(502)
    expect(error.retryable).toBe(true)
  })
})

describe('getReady', () => {
  it('returns a 503 not_ready body instead of throwing', async () => {
    mockApi({
      'GET /readyz': jsonResponse({ status: 'not_ready', checks: { database: 'unavailable' } }, 503),
    })

    await expect(getReady()).resolves.toEqual({
      status: 'not_ready',
      checks: { database: 'unavailable' },
    })
  })
})

describe('createSession wake-up retry', () => {
  const created = {
    token: TEST_TOKEN,
    expires_at: futureIso(),
    provider_mode: 'openai',
    limits: TEST_LIMITS,
  }

  it('retries while the server is unreachable and reports that it is waking', async () => {
    vi.useFakeTimers()
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce(new Response('', { status: 503 }))
      .mockResolvedValueOnce(jsonResponse(created, 201))
    vi.stubGlobal('fetch', fetchMock)
    const onWaking = vi.fn()

    const pending = createSession({ onWaking })
    await vi.advanceTimersByTimeAsync(10_000)

    await expect(pending).resolves.toEqual(created)
    expect(fetchMock).toHaveBeenCalledTimes(3)
    expect(onWaking).toHaveBeenCalled()
    // Session creation is the one /api call made without a token.
    expect(fetchMock.mock.calls[0][1].headers.Authorization).toBeUndefined()
  })

  it('gives up after about 90 seconds', async () => {
    vi.useFakeTimers()
    const fetchMock = vi.fn().mockRejectedValue(new TypeError('Failed to fetch'))
    vi.stubGlobal('fetch', fetchMock)

    const outcome = failure(createSession())
    await vi.advanceTimersByTimeAsync(120_000)

    expect((await outcome).code).toBe(NETWORK_ERROR)
    expect(fetchMock.mock.calls.length).toBeGreaterThan(5)
    expect(fetchMock.mock.calls.length).toBeLessThan(20)
  })

  it('does not retry a rate-limit response', async () => {
    const requests = mockApi({
      'POST /api/sessions': errorResponse(429, 'rate_limited', 'Too many sessions'),
    })

    const error = await failure(createSession())

    expect(error.code).toBe('rate_limited')
    expect(requests).toHaveLength(1)
  })
})
