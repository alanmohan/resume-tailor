import type { ReactNode } from 'react'
import type { LucideIcon } from 'lucide-react'
import { cn } from 'cn'

interface EmptyStateProps {
  icon?: LucideIcon
  title: string
  description?: ReactNode
  /** Usually one Button or link that moves the user forward. */
  action?: ReactNode
  className?: string
}

/** Shown when there is nothing to display yet; always points to the next step. */
export function EmptyState({ icon: Icon, title, description, action, className }: EmptyStateProps) {
  return (
    <div
      className={cn(
        'flex flex-col items-start gap-3 rounded-xl border border-dashed px-5 py-8 sm:px-8',
        className,
      )}
    >
      {Icon ? <Icon aria-hidden="true" className="size-6 text-muted-foreground" /> : null}
      <h2 className="text-lg font-medium">{title}</h2>
      {description ? (
        <div className="max-w-prose text-sm text-muted-foreground">{description}</div>
      ) : null}
      {action ? <div className="mt-1 flex flex-wrap gap-2">{action}</div> : null}
    </div>
  )
}
