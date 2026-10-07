/**
 * Session lifecycle for this browser tab.
 *
 * Import session helpers from here. Storage lives in `sessionStore.ts`;
 * this module adds the one operation that needs the network.
 */
import { createSession, type CreateSessionOptions } from '@/lib/api'
import { getSessionStatus, saveSession } from '@/lib/sessionStore'
import type { Limits } from '@/lib/types'

export {
  SESSION_STORAGE_KEY,
  clearSession,
  getSessionExpiry,
  getSessionStatus,
  getToken,
  markSessionExpired,
  saveSession,
  subscribeToSession,
} from '@/lib/sessionStore'
export type { SessionStatus, StoredSession } from '@/lib/sessionStore'

/**
 * Limits to assume before the server has reported its real ones. These are
 * the documented defaults; the server always enforces its own values.
 */
export const DEFAULT_LIMITS: Limits = {
  max_profile_chars: 60_000,
  max_job_chars: 25_000,
  max_sources: 5,
  max_requirements: 25,
  session_ttl_hours: 24,
}

let pendingCreation: Promise<void> | null = null

/**
 * Make sure this tab has a usable session, creating one if needed.
 * Concurrent callers share a single creation request, so a double click or a
 * React re-render can never create two sessions.
 */
export function ensureSession(options: CreateSessionOptions = {}): Promise<void> {
  if (getSessionStatus() === 'active') return Promise.resolve()
  pendingCreation ??= createSession(options)
    .then((created) => saveSession({ token: created.token, expiresAt: created.expires_at }))
    .finally(() => {
      pendingCreation = null
    })
  return pendingCreation
}
