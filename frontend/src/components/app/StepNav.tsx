import { Link, useLocation } from 'react-router'
import { cn } from 'cn'
import { isProfileReady, useLatestGenerationId, useProfile, useSession } from '@/lib/hooks'

type StepKey = 'start' | 'profile' | 'job' | 'workspace'

interface Step {
  key: StepKey
  label: string
  /** Where the step links to, or null when it cannot be opened yet. */
  to: string | null
  /** Why the step is locked; read out to screen readers and shown as a tooltip. */
  lockedReason: string
}

function currentStep(pathname: string): StepKey | null {
  if (pathname === '/') return 'start'
  if (pathname.startsWith('/profile')) return 'profile'
  if (pathname.startsWith('/job')) return 'job'
  if (pathname.startsWith('/workspace')) return 'workspace'
  return null
}

/**
 * The four-step workflow. A step is a link only when it is reachable:
 * Profile needs an ingested profile, Target job needs a confirmed and indexed
 * profile, and Workspace needs at least one generated draft.
 */
function useSteps(): Step[] {
  const { status, hasProfile } = useSession()
  const profile = useProfile().data
  const active = status === 'active'
  const profileExists = active && (hasProfile || !!profile)
  const profileReady = active && isProfileReady(profile)
  const latestGenerationId = useLatestGenerationId(profileExists)

  return [
    { key: 'start', label: 'Start', to: '/', lockedReason: '' },
    {
      key: 'profile',
      label: 'Profile',
      to: profileExists ? '/profile' : null,
      lockedReason: 'Add your profile on the Start step first',
    },
    {
      key: 'job',
      label: 'Target job',
      to: profileReady ? '/job' : null,
      lockedReason: 'Confirm your profile first',
    },
    {
      key: 'workspace',
      label: 'Workspace',
      to: latestGenerationId ? `/workspace/${latestGenerationId}` : null,
      lockedReason: 'Generate a draft from a target job first',
    },
  ]
}

const stepClasses =
  'flex items-center gap-1.5 rounded-md px-1.5 py-1.5 text-xs whitespace-nowrap sm:px-2 sm:text-sm'

export function StepNav({ className }: { className?: string }) {
  const steps = useSteps()
  const current = currentStep(useLocation().pathname)

  return (
    // On narrow screens the steps spread across the full width; if they ever
    // do not fit, the list scrolls inside itself instead of widening the page.
    <nav aria-label="Steps" className={cn('min-w-0 overflow-x-auto', className)}>
      <ol className="flex items-center justify-between gap-1 md:justify-start">
        {steps.map((step, index) => {
          const isCurrent = step.key === current
          const content = (
            <>
              <span
                aria-hidden="true"
                className={cn(
                  'grid size-5 shrink-0 place-items-center rounded-full border text-[0.7rem] tabular-nums',
                  isCurrent && 'border-primary bg-primary text-primary-foreground',
                )}
              >
                {index + 1}
              </span>
              <span>{step.label}</span>
            </>
          )
          return (
            <li key={step.key}>
              {step.to && !isCurrent ? (
                <Link to={step.to} className={cn(stepClasses, 'hover:bg-muted')}>
                  {content}
                </Link>
              ) : isCurrent ? (
                <span aria-current="step" className={cn(stepClasses, 'bg-muted font-medium')}>
                  {content}
                </span>
              ) : (
                <span
                  aria-disabled="true"
                  title={step.lockedReason}
                  className={cn(stepClasses, 'cursor-not-allowed text-muted-foreground/70')}
                >
                  {content}
                  <span className="sr-only">(locked: {step.lockedReason})</span>
                </span>
              )}
            </li>
          )
        })}
      </ol>
    </nav>
  )
}
