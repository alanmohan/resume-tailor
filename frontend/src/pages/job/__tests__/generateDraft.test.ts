import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/lib/errors'
import type { GenerationStatus } from '@/lib/types'
import { errorResponse, jsonResponse, mockApi, seedSession } from '@/test/mockApi'
import {
  GENERATION_FAILED,
  GENERATION_STILL_RUNNING,
  MAX_WAIT_MS,
  POLL_INTERVAL_MS,
  generateDraft,
  isProfileNotReady,
} from '../generateDraft'
import { makeGeneration } from './jobFixtures'

const KEY = '11111111-2222-4333-8444-555555555555'
const IN_PROGRESS = errorResponse(409, 'generation_in_progress', 'Already running.', {
  details: { generation_id: 'gen-9' },
})

/** GET /api/generations/gen-9 answering with the given statuses in turn, then the last one forever. */
function pollRoute(statuses: GenerationStatus[]) {
  let calls = 0
  const handler = () => {
    const status = statuses[Math.min(calls, statuses.length - 1)]
    calls += 1
    return jsonResponse(makeGeneration({ generation_id: 'gen-9', status }))
  }
  return { handler, calls: () => calls }
}

/** Resolve with the rejection, so a failure can be awaited after timers were advanced. */
function failure(promise: Promise<unknown>): Promise<unknown> {
  return promise.then(
    () => {
      throw new Error('Expected the promise to reject')
    },
    (error: unknown) => error,
  )
}

function options(controller = new AbortController()) {
  return { signal: controller.signal, onWaiting: vi.fn() }
}

beforeEach(() => {
  vi.useFakeTimers()
  seedSession()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('generateDraft', () => {
  it('returns the draft from the POST when the server finishes in one request', async () => {
    const requests = mockApi({
      'POST /api/generations': jsonResponse(makeGeneration({ generation_id: 'gen-1' }), 201),
    })
    const opts = options()

    const generation = await generateDraft('job-1', KEY, opts)

    expect(generation.generation_id).toBe('gen-1')
    expect(opts.onWaiting).not.toHaveBeenCalled()
    expect(requests).toHaveLength(1)
    expect(requests[0].headers['Idempotency-Key']).toBe(KEY)
    expect(requests[0].body).toEqual({ job_id: 'job-1' })
  })

  it('joins a generation that is already running and checks it every 3 seconds', async () => {
    const poll = pollRoute(['running', 'running', 'completed'])
    const requests = mockApi({
      'POST /api/generations': IN_PROGRESS,
      'GET /api/generations/gen-9': poll.handler,
    })
    const opts = options()

    const pending = generateDraft('job-1', KEY, opts)
    await vi.advanceTimersByTimeAsync(0)
    // The first check is immediate; the next one waits a full interval.
    expect(poll.calls()).toBe(1)
    expect(opts.onWaiting).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS - 1)
    expect(poll.calls()).toBe(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(poll.calls()).toBe(2)
    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS)

    const generation = await pending
    expect(generation.status).toBe('completed')
    expect(poll.calls()).toBe(3)
    expect(requests.filter((request) => request.method === 'POST')).toHaveLength(1)
  })

  it('also waits when the POST itself answers with a running generation', async () => {
    const poll = pollRoute(['completed'])
    mockApi({
      'POST /api/generations': jsonResponse(
        makeGeneration({ generation_id: 'gen-9', status: 'running' }),
        201,
      ),
      'GET /api/generations/gen-9': poll.handler,
    })

    const pending = generateDraft('job-1', KEY, options())
    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS)

    expect((await pending).status).toBe('completed')
    expect(poll.calls()).toBe(1)
  })

  it('raises a stored failure with its own code and message, as retryable', async () => {
    mockApi({
      'POST /api/generations': IN_PROGRESS,
      'GET /api/generations/gen-9': jsonResponse(
        makeGeneration({
          generation_id: 'gen-9',
          status: 'failed',
          error: { code: 'provider_timeout', message: 'The AI provider timed out.' },
        }),
      ),
    })

    const error = await failure(generateDraft('job-1', KEY, options()))

    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({
      code: 'provider_timeout',
      message: 'The AI provider timed out.',
      retryable: true,
    })
  })

  it('falls back to a general message when a failed record carries no error', async () => {
    mockApi({
      'POST /api/generations': IN_PROGRESS,
      'GET /api/generations/gen-9': jsonResponse(
        makeGeneration({ generation_id: 'gen-9', status: 'failed', error: null }),
      ),
    })

    const error = await failure(generateDraft('job-1', KEY, options()))

    expect(error).toMatchObject({
      code: GENERATION_FAILED,
      message: 'The draft could not be generated.',
    })
  })

  it('passes every other API error through without polling', async () => {
    const requests = mockApi({
      'POST /api/generations': errorResponse(429, 'quota_exceeded', 'No generations left.'),
    })
    const opts = options()

    const error = await failure(generateDraft('job-1', KEY, opts))

    expect(error).toMatchObject({ code: 'quota_exceeded', status: 429 })
    expect(opts.onWaiting).not.toHaveBeenCalled()
    expect(requests).toHaveLength(1)
  })

  it('stops checking once the signal is aborted', async () => {
    const poll = pollRoute(['running'])
    mockApi({
      'POST /api/generations': IN_PROGRESS,
      'GET /api/generations/gen-9': poll.handler,
    })
    const controller = new AbortController()

    const outcome = failure(generateDraft('job-1', KEY, options(controller)))
    await vi.advanceTimersByTimeAsync(0)
    controller.abort()
    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 5)

    expect(await outcome).toMatchObject({ name: 'AbortError' })
    expect(poll.calls()).toBe(1)
  })

  it('gives up after the maximum wait and says the draft is still running', async () => {
    const poll = pollRoute(['running'])
    mockApi({
      'POST /api/generations': IN_PROGRESS,
      'GET /api/generations/gen-9': poll.handler,
    })

    const outcome = failure(generateDraft('job-1', KEY, options()))
    await vi.advanceTimersByTimeAsync(MAX_WAIT_MS + POLL_INTERVAL_MS)

    expect(await outcome).toMatchObject({ code: GENERATION_STILL_RUNNING, retryable: true })
    // One check per interval for the whole wait, then no more.
    const checks = poll.calls()
    expect(checks).toBeGreaterThan(MAX_WAIT_MS / POLL_INTERVAL_MS - 2)
    expect(checks).toBeLessThanOrEqual(MAX_WAIT_MS / POLL_INTERVAL_MS + 1)
    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 3)
    expect(poll.calls()).toBe(checks)
  })
})

describe('isProfileNotReady', () => {
  it('is true only for the two profile-state refusals', () => {
    const apiError = (code: string) => new ApiError({ code, message: 'x', status: 409 })

    expect(isProfileNotReady(apiError('profile_not_confirmed'))).toBe(true)
    expect(isProfileNotReady(apiError('profile_not_indexed'))).toBe(true)
    expect(isProfileNotReady(apiError('version_conflict'))).toBe(false)
    expect(isProfileNotReady(new Error('profile_not_confirmed'))).toBe(false)
  })
})
