/**
 * Light/dark theme using the shadcn class strategy: the `dark` class on
 * <html> switches every colour token. The choice is a display preference,
 * so it is the one thing this app keeps in localStorage.
 */
import { useSyncExternalStore } from 'react'

export type Theme = 'light' | 'dark'

export const THEME_STORAGE_KEY = 'resume-tailor.theme'

const DARK_QUERY = '(prefers-color-scheme: dark)'
const listeners = new Set<() => void>()

function storedTheme(): Theme | null {
  try {
    const value = window.localStorage.getItem(THEME_STORAGE_KEY)
    return value === 'light' || value === 'dark' ? value : null
  } catch {
    return null
  }
}

/** The user's saved choice, or the operating system's preference when there is none. */
export function getTheme(): Theme {
  return storedTheme() ?? (window.matchMedia(DARK_QUERY).matches ? 'dark' : 'light')
}

function show(theme: Theme): void {
  document.documentElement.classList.toggle('dark', theme === 'dark')
  // Native controls and scrollbars follow the same scheme.
  document.documentElement.style.colorScheme = theme
}

/** Put the current theme on <html>. Called once before the first render. */
export function applyTheme(): void {
  show(getTheme())
}

export function setTheme(theme: Theme): void {
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, theme)
  } catch {
    // Storage blocked: the theme still applies until the page is reloaded.
  }
  show(theme)
  for (const listener of listeners) listener()
}

function subscribe(listener: () => void): () => void {
  // Follow the operating system while the user has not chosen a theme.
  const media = window.matchMedia(DARK_QUERY)
  const onSystemChange = () => {
    applyTheme()
    listener()
  }
  listeners.add(listener)
  media.addEventListener('change', onSystemChange)
  return () => {
    listeners.delete(listener)
    media.removeEventListener('change', onSystemChange)
  }
}

/** The active theme; re-renders the caller when it changes. */
export function useTheme(): Theme {
  return useSyncExternalStore(subscribe, getTheme)
}
