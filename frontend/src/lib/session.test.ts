import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  SESSION_STORAGE_KEY,
  clearSession,
  ensureSession,
  getSessionStatus,
  getToken,
  markSessionExpired,
  saveSession,
  subscribeToSession,
} from '@/lib/session'
import { TEST_LIMITS, TEST_TOKEN, futureIso, jsonResponse, mockApi, seedSession } from '@/test/mockApi'

afterEach(() => {
  clearSession()
  vi.unstubAllGlobals()
})

describe('session storage', () => {
  it('keeps the token in sessionStorage under one key and nowhere else', () => {
    seedSession()

    expect(window.sessionStorage.length).toBe(1)
    expect(window.sessionStorage.getItem(SESSION_STORAGE_KEY)).toContain(TEST_TOKEN)
    expect(window.location.href).not.toContain(TEST_TOKEN)
    expect(window.localStorage.length).toBe(0)
    expect(document.cookie).toBe('')
    expect(getToken()).toBe(TEST_TOKEN)
    expect(getSessionStatus()).toBe('active')
  })

  it('treats a session past its expiry time as expired', () => {
    saveSession({ token: TEST_TOKEN, expiresAt: new Date(Date.now() - 1000).toISOString() })

    expect(getToken()).toBeNull()
    expect(getSessionStatus()).toBe('expired')
  })

  it('ignores a corrupt stored value', () => {
    window.sessionStorage.setItem(SESSION_STORAGE_KEY, '{not json')

    expect(getToken()).toBeNull()
    expect(getSessionStatus()).toBe('none')
  })

  it('notifies subscribers and distinguishes expired from cleared', () => {
    const listener = vi.fn()
    const unsubscribe = subscribeToSession(listener)
    seedSession()

    markSessionExpired()
    expect(getSessionStatus()).toBe('expired')
    clearSession()
    expect(getSessionStatus()).toBe('none')

    expect(listener).toHaveBeenCalledTimes(3)
    unsubscribe()
  })
})

describe('ensureSession', () => {
  const created = {
    token: TEST_TOKEN,
    expires_at: futureIso(),
    provider_mode: 'openai',
    limits: TEST_LIMITS,
  }

  it('creates one session for concurrent callers and stores its token', async () => {
    const requests = mockApi({ 'POST /api/sessions': jsonResponse(created, 201) })

    await Promise.all([ensureSession(), ensureSession()])

    expect(requests.filter((request) => request.path === '/api/sessions')).toHaveLength(1)
    expect(getToken()).toBe(TEST_TOKEN)
    expect(requests[0].url).not.toContain(TEST_TOKEN)
    expect(window.location.href).not.toContain(TEST_TOKEN)
  })

  it('does nothing when a session is already active', async () => {
    seedSession()
    const requests = mockApi()

    await ensureSession()

    expect(requests).toHaveLength(0)
  })
})
