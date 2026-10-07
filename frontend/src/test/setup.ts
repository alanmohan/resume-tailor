import '@testing-library/jest-dom/vitest'
import { cleanup, configure } from '@testing-library/react'
import { afterEach, vi } from 'vitest'
import { clearSession } from '@/lib/sessionStore'

// How long findBy... and waitFor keep looking. Many tests render the whole
// app and wait for mocked network round trips; the default of 1 s is too
// tight when the machine is busy. A passing wait returns as soon as it
// succeeds, so only a failing test takes longer. (The matching limit for a
// whole test is `testTimeout` in vite.config.ts.)
configure({ asyncUtilTimeout: 5_000 })

// jsdom lacks a few browser APIs that the app shell and Radix UI call.
window.scrollTo = vi.fn()
window.matchMedia ??= (query: string) =>
  ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
    dispatchEvent: () => false,
  }) as MediaQueryList
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
}
Element.prototype.scrollIntoView ??= () => undefined
Element.prototype.hasPointerCapture ??= () => false

afterEach(() => {
  cleanup()
  // Resets the stored token and the module's "rejected by server" flag.
  clearSession()
  window.sessionStorage.clear()
  window.localStorage.clear()
  vi.unstubAllGlobals()
})
