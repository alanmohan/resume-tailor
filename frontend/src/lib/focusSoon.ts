/**
 * Focus an element once the key press or click that asked for it is over.
 *
 * Focusing at once would hand the rest of that key press to the new element:
 * when Enter on one button moves focus to another button, the browser would
 * deliver the same Enter to the newly focused button and press it as well.
 *
 * The element is looked up when the time comes, because it may not exist yet.
 * Returns a function that cancels the request (use it as an effect cleanup).
 */
export function focusSoon(getElement: () => HTMLElement | null): () => void {
  const timer = window.setTimeout(() => getElement()?.focus(), 0)
  return () => window.clearTimeout(timer)
}
