import { Circle, CircleCheck, CircleX, LoaderCircle, type LucideIcon } from 'lucide-react'
import { cn } from 'cn'

export type StepStatus = 'waiting' | 'running' | 'done' | 'failed'

export interface RunStep {
  label: string
  status: StepStatus
}

/** The two steps of a run, in the words shown to the user. */
export const ANALYZE_STEP_LABEL = 'Analyzing the job description'
export const GENERATE_STEP_LABEL = 'Writing your resume and cover letter'

/** Every status is an icon plus a word, so it never depends on colour. */
const STEP_STATUS: Record<StepStatus, { text: string; icon: LucideIcon; className: string }> = {
  waiting: { text: 'Waiting', icon: Circle, className: 'text-muted-foreground' },
  running: { text: 'In progress', icon: LoaderCircle, className: 'text-foreground' },
  done: { text: 'Done', icon: CircleCheck, className: 'text-success' },
  failed: { text: 'Failed', icon: CircleX, className: 'text-destructive' },
}

interface RunProgressProps {
  /** The steps in order; an empty list means no run has been started. */
  steps: RunStep[]
  /** An extra sentence under the list, e.g. while a running generation is being checked on. */
  note?: string
}

/**
 * Honest progress for a run: which step is waiting, in progress, done or
 * failed. No percentage, because the app cannot know one.
 *
 * The live region is always in the page and only its content changes, which
 * is what makes screen readers announce each change.
 */
export function RunProgress({ steps, note }: RunProgressProps) {
  const running = steps.some((step) => step.status === 'running')
  return (
    <div role="status" aria-live="polite" aria-label="Progress" className="empty:hidden">
      {steps.length > 0 ? (
        <div className="space-y-3 rounded-xl border p-4">
          <ol className="space-y-2">
            {steps.map((step, index) => {
              const { text, icon: Icon, className } = STEP_STATUS[step.status]
              return (
                <li key={step.label} className="flex items-start gap-2 text-sm">
                  <Icon
                    aria-hidden="true"
                    className={cn(
                      'mt-0.5 size-4 shrink-0',
                      className,
                      step.status === 'running' && 'animate-spin motion-reduce:animate-none',
                    )}
                  />
                  <span className="min-w-0 flex-1">
                    {index + 1}. {step.label}
                  </span>
                  <span className={cn('shrink-0 font-medium', className)}>{text}</span>
                </li>
              )
            })}
          </ol>
          {running ? (
            <p className="text-sm text-muted-foreground">
              This usually takes about a minute. Keep this page open.
            </p>
          ) : null}
          {note ? <p className="text-sm text-muted-foreground">{note}</p> : null}
        </div>
      ) : null}
    </div>
  )
}
