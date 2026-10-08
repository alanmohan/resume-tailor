import { screen, within } from '@testing-library/react'
import { vi } from 'vitest'
import type { Generation } from '@/lib/types'
import { jsonResponse, mockApi, seedSession, sessionInfo, type Routes } from '@/test/mockApi'
import { renderApp } from '@/test/render'
import { claimElementId } from '../workspaceModel'
import { makeGeneration } from './fixtures'

/**
 * These tests render the whole app and wait for mocked network round trips,
 * several per test. The shared setup already gives every wait 5 s and every
 * test 20 s (src/test/setup.ts, vite.config.ts). The workspace files get a
 * little more per test, because one test here makes many round trips and a
 * busy machine can stretch each of them. A correct run is not slowed down.
 */
export function allowForSlowMachine(): void {
  vi.setConfig({ testTimeout: 30_000 })
}

export function generationPath(generation: Generation): string {
  return `/api/generations/${generation.generation_id}`
}

/** Open /workspace/:id in a session whose API serves the given draft. */
export function openWorkspace(generation: Generation = makeGeneration(), routes: Routes = {}) {
  seedSession()
  const requests = mockApi({
    'GET /api/session': jsonResponse(sessionInfo({ has_profile: true })),
    [`GET ${generationPath(generation)}`]: jsonResponse(generation),
    ...routes,
  })
  return { requests, ...renderApp(`/workspace/${generation.generation_id}`) }
}

/** Wait until the completed draft is on screen. */
export async function draftIsShown(): Promise<void> {
  await screen.findByRole('heading', { name: 'Your tailored draft' })
  await screen.findByRole('tab', { name: 'Resume' })
}

/** The element of one statement (its text and its controls). */
export function claimElement(itemId: string): HTMLElement {
  const element = document.getElementById(claimElementId(itemId))
  if (!element) throw new Error(`No statement with item_id "${itemId}" is rendered`)
  return element
}

/** Queries scoped to one statement. */
export function claim(itemId: string) {
  return within(claimElement(itemId))
}

/** Queries scoped to the row of one requirement in the coverage list. */
export function requirement(requirementId: string) {
  const row = document.getElementById(`requirement-${requirementId}`)?.closest('li')
  if (!row) throw new Error(`No requirement row for "${requirementId}" is rendered`)
  return within(row)
}
