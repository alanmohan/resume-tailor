import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, vi } from 'vitest'
import { clearSession } from '@/lib/sessionStore'

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
