/**
 * Where the anonymous session token lives in the browser.
 *
 * The token is kept in sessionStorage under exactly one key, so it is scoped
 * to this tab and disappears when the tab closes. It is never written to the
 * URL, localStorage, cookies or the console.
 *
 * This module only stores and reports; it never calls the network. That keeps
 * `api.ts` (which needs the token) free of a circular import with
 * `session.ts` (which needs the API to create a session).
 */

export const SESSION_STORAGE_KEY = 'resume-tailor.session'

export interface StoredSession {
  token: string
  /** ISO-8601 UTC timestamp sent by the server. */
  expiresAt: string
}

/**
 * none    - no session in this tab yet
 * active  - a token is stored and has not expired
 * expired - the server rejected the token, or its expiry time has passed
 */
export type SessionStatus = 'none' | 'active' | 'expired'

/** Set when the server answers 401, so the shell can explain what happened. */
let rejectedByServer = false

const listeners = new Set<() => void>()

function notify(): void {
  for (const listener of listeners) listener()
}

function readStoredSession(): StoredSession | null {
  try {
    const raw = window.sessionStorage.getItem(SESSION_STORAGE_KEY)
    if (!raw) return null
    const value: unknown = JSON.parse(raw)
    if (typeof value !== 'object' || value === null) return null
    const { token, expiresAt } = value as Record<string, unknown>
    if (typeof token !== 'string' || typeof expiresAt !== 'string') return null
    return { token, expiresAt }
  } catch {
    // Storage blocked or the value is corrupt: behave as if there is no session.
    return null
  }
}

function removeStoredSession(): void {
  try {
    window.sessionStorage.removeItem(SESSION_STORAGE_KEY)
  } catch {
    // Nothing to remove when storage is unavailable.
  }
}

function hasExpired(session: StoredSession): boolean {
  return Date.parse(session.expiresAt) <= Date.now()
}

export function getSessionStatus(): SessionStatus {
  if (rejectedByServer) return 'expired'
  const session = readStoredSession()
  if (!session) return 'none'
  return hasExpired(session) ? 'expired' : 'active'
}

/** The bearer token, or null when there is no usable session. */
export function getToken(): string | null {
  const session = readStoredSession()
  if (!session || rejectedByServer || hasExpired(session)) return null
  return session.token
}

/** When the stored session ends, or null when there is none. */
export function getSessionExpiry(): string | null {
  return readStoredSession()?.expiresAt ?? null
}

export function saveSession(session: StoredSession): void {
  window.sessionStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify(session))
  rejectedByServer = false
  notify()
}

/** Called when the API answers 401: forget the token and flag the session as ended. */
export function markSessionExpired(): void {
  removeStoredSession()
  rejectedByServer = true
  notify()
}

/** Forget the session completely (after "Clear my data" or a restart). */
export function clearSession(): void {
  removeStoredSession()
  rejectedByServer = false
  notify()
}

/** Subscribe to status changes; returns the unsubscribe function. */
export function subscribeToSession(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}
