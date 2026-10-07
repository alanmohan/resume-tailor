import { useSyncExternalStore } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router'
import { toast } from 'sonner'
import { deleteSession, getProfile, getReady, getSession, listGenerations } from '@/lib/api'
import { isApiError } from '@/lib/errors'
import { queryKeys } from '@/lib/queryKeys'
import {
  DEFAULT_LIMITS,
  clearSession,
  getSessionExpiry,
  getSessionStatus,
  subscribeToSession,
  type SessionStatus,
} from '@/lib/session'
import type { Limits, Profile, ProviderMode, SessionInfo } from '@/lib/types'

/** How often to re-read the profile while the evidence index is being built. */
export const PROFILE_POLL_MS = 1_500

/** 'none' | 'active' | 'expired' for this tab, kept in sync with sessionStorage. */
export function useSessionStatus(): SessionStatus {
  return useSyncExternalStore(subscribeToSession, getSessionStatus)
}

/**
 * The public readiness endpoint. It needs no session, so it is how the app
 * learns the server's provider and limits before anything is submitted.
 */
function useReady() {
  return useQuery({
    queryKey: queryKeys.ready,
    queryFn: getReady,
    staleTime: 5 * 60_000,
    retry: false,
  })
}

export interface SessionView {
  status: SessionStatus
  /** Server-side session details; undefined until loaded or when there is no session. */
  session: SessionInfo | undefined
  /**
   * The server's limits: from the session when there is one, before that from
   * the readiness endpoint, and the documented defaults only if neither has them.
   */
  limits: Limits
  hasProfile: boolean
  /** ISO timestamp at which the session ends, or null without a session. */
  expiresAt: string | null
  isLoading: boolean
}

/** The tab's session: status from storage plus details from GET /api/session. */
export function useSession(): SessionView {
  const status = useSessionStatus()
  const query = useQuery({
    queryKey: queryKeys.session,
    queryFn: getSession,
    enabled: status === 'active',
  })
  const ready = useReady()
  const session = status === 'active' ? query.data : undefined
  return {
    status,
    session,
    limits: session?.limits ?? ready.data?.limits ?? DEFAULT_LIMITS,
    hasProfile: session?.has_profile ?? false,
    expiresAt: status === 'active' ? (session?.expires_at ?? getSessionExpiry()) : null,
    isLoading: status === 'active' && query.isLoading,
  }
}

/**
 * Which AI provider the server uses, or null while unknown.
 * Read from the session when there is one, otherwise from the public
 * readiness endpoint so demo mode is labelled before any data is submitted.
 */
export function useProviderMode(): ProviderMode | null {
  const { session } = useSession()
  const ready = useReady()
  return session?.provider_mode ?? ready.data?.provider_mode ?? null
}

async function getProfileOrNull(): Promise<Profile | null> {
  try {
    return await getProfile()
  } catch (error) {
    // "No profile yet" is a normal state for a new session, not a failure.
    if (isApiError(error, 'not_found')) return null
    throw error
  }
}

/**
 * The session's profile; `data` is null when none has been ingested yet.
 * Polls while `poll` is true or while the server reports indexing in
 * progress, so the page can show real embedding counts.
 */
export function useProfile(options: { poll?: boolean } = {}) {
  const status = useSessionStatus()
  const poll = options.poll ?? false
  return useQuery({
    queryKey: queryKeys.profile,
    queryFn: getProfileOrNull,
    enabled: status === 'active',
    refetchInterval: (query) =>
      poll || query.state.data?.index_state === 'indexing' ? PROFILE_POLL_MS : false,
  })
}

/** True when the profile is confirmed and its current version is fully indexed. */
export function isProfileReady(profile: Profile | null | undefined): boolean {
  return (
    !!profile &&
    profile.status === 'confirmed' &&
    profile.index_state === 'indexed' &&
    profile.indexed_version === profile.version
  )
}

/** The newest generation's id, used by the step navigation to link to the workspace. */
export function useLatestGenerationId(enabled: boolean): string | null {
  const status = useSessionStatus()
  const query = useQuery({
    queryKey: queryKeys.generations,
    queryFn: listGenerations,
    enabled: enabled && status === 'active',
  })
  const generations = query.data?.generations ?? []
  if (generations.length === 0) return null
  const newest = generations.reduce((a, b) => (b.created_at > a.created_at ? b : a))
  return newest.generation_id
}

/** Forget the session locally and return to Start with an empty cache. */
export function useResetSession(): () => void {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  return () => {
    clearSession()
    queryClient.clear()
    void navigate('/')
  }
}

/**
 * "Clear my data": DELETE /api/session, then forget the token, empty the
 * query cache and return to Start. Nothing is cleared locally unless the
 * server confirmed the deletion, so a failure can be retried.
 */
export function useClearData() {
  const resetSession = useResetSession()
  return useMutation({
    mutationFn: deleteSession,
    onSuccess: () => {
      resetSession()
      toast.success('Your data was deleted')
    },
  })
}
