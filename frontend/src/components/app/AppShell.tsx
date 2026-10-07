import { useEffect, useRef } from 'react'
import { Link, Outlet, useLocation } from 'react-router'
import { FlaskConical, TimerOff } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useProviderMode, useResetSession, useSession } from '@/lib/hooks'
import { ClearDataDialog } from './ClearDataDialog'
import { EmptyState } from './EmptyState'
import { SessionExpiryHint } from './SessionExpiryHint'
import { StepNav } from './StepNav'
import { ThemeToggle } from './ThemeToggle'

export const DEMO_MODE_MESSAGE =
  'Demo mode - responses come from a deterministic fake provider, not a real AI model.'
export const FOOTER_DISCLAIMER = 'Drafting assistant - review every claim before you submit'

/** Shown instead of the page when the server no longer accepts the session token. */
function SessionExpired() {
  const resetSession = useResetSession()
  const { limits } = useSession()
  return (
    <EmptyState
      icon={TimerOff}
      title="Your session has ended"
      description={
        <p>
          Sessions last {limits.session_ttl_hours} hours and are tied to one browser tab. The data stored for this
          session is deleted automatically, so it can no longer be opened. Start again to
          create a new profile.
        </p>
      }
      action={
        <Button type="button" onClick={resetSession}>
          Start over
        </Button>
      }
    />
  )
}

/**
 * Page frame shared by every route: header (name, steps, session expiry,
 * theme, clear data), the demo-mode banner, the routed page and the footer.
 * Header, banner and footer are hidden when printing.
 */
export function AppShell() {
  const { status, expiresAt } = useSession()
  const providerMode = useProviderMode()
  const { pathname, search } = useLocation()
  // The query string counts: /job shows either the form or one job's review.
  const screen = pathname + search
  const mainRef = useRef<HTMLElement>(null)
  const shownScreen = useRef(screen)

  useEffect(() => {
    window.scrollTo(0, 0)
    // Moving to another screen removes the control that had focus. Put focus
    // on the new screen's content so keyboard and screen-reader users continue
    // from there instead of from the top of the header. Not on first load.
    if (shownScreen.current !== screen) {
      shownScreen.current = screen
      mainRef.current?.focus({ preventScroll: true })
    }
  }, [screen])

  return (
    <div className="flex min-h-svh flex-col">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-50 focus:bg-background focus:px-3 focus:py-2"
      >
        Skip to main content
      </a>

      <header className="sticky top-0 z-40 border-b bg-background/95 backdrop-blur print:hidden">
        <div className="mx-auto grid w-full max-w-6xl grid-cols-[1fr_auto] items-center gap-x-4 gap-y-2 px-4 py-2.5 sm:px-6 md:grid-cols-[auto_1fr_auto]">
          <Link to="/" className="font-serif text-lg font-medium tracking-tight">
            Resume Tailor
          </Link>
          <StepNav className="order-last col-span-2 md:order-none md:col-span-1 md:justify-self-center" />
          <div className="flex items-center gap-0.5">
            {status === 'active' && expiresAt ? <SessionExpiryHint expiresAt={expiresAt} /> : null}
            <ThemeToggle />
            {status === 'active' ? <ClearDataDialog /> : null}
          </div>
        </div>
      </header>

      {providerMode === 'fake' ? (
        <div role="status" className="border-b border-warning/35 bg-warning/10 print:hidden">
          <p className="mx-auto flex w-full max-w-6xl items-start gap-2 px-4 py-2 text-sm font-medium text-warning sm:px-6">
            <FlaskConical aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
            {DEMO_MODE_MESSAGE}
          </p>
        </div>
      ) : null}

      <main id="main" ref={mainRef} tabIndex={-1} className="mx-auto w-full max-w-6xl flex-1 px-4 py-8 outline-none sm:px-6 sm:py-10">
        {status === 'expired' ? <SessionExpired /> : <Outlet />}
      </main>

      <footer className="border-t print:hidden">
        <p className="mx-auto w-full max-w-6xl px-4 py-4 text-xs text-muted-foreground sm:px-6">
          {FOOTER_DISCLAIMER}
        </p>
      </footer>
    </div>
  )
}
