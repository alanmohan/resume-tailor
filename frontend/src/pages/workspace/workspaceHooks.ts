import { useCallback, useEffect, useRef, useSyncExternalStore } from 'react'

/** Whether a CSS media query currently matches; re-renders the caller when that changes. */
export function useMediaQuery(query: string): boolean {
  const subscribe = useCallback(
    (onChange: () => void) => {
      const media = window.matchMedia(query)
      media.addEventListener('change', onChange)
      return () => media.removeEventListener('change', onChange)
    },
    [query],
  )
  return useSyncExternalStore(subscribe, () => window.matchMedia(query).matches)
}

/**
 * Focus handling for an inline form that a button opens in place.
 *
 * Call the returned function with the button when it is clicked. When `open`
 * turns false the form has unmounted and taken keyboard focus with it, so
 * focus is put back on that button instead of being lost at the top of the
 * page.
 */
export function useReturnFocus(open: boolean): (trigger: HTMLElement) => void {
  const triggerRef = useRef<HTMLElement | null>(null)
  useEffect(() => {
    if (!open) triggerRef.current?.focus()
  }, [open])
  return useCallback((trigger: HTMLElement) => {
    triggerRef.current = trigger
  }, [])
}
